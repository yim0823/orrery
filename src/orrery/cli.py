from __future__ import annotations

import json
import pathlib
import sys

import typer

from orrery.backtest import Incident, Outcome, format_report, run
from orrery.connectors import StaticYamlConnector
from orrery.resolve import Resolver
from orrery.sim import Event, propagate
from orrery.world import (
    EntityKind,
    World,
    audit,
    blast_radius,
    diff,
    format_risks,
    single_points_of_failure,
)

app = typer.Typer(
    help="orrery: every server is on the map, and when you act, the consequence is computed."
)
_STATE = pathlib.Path(".orrery/world.yaml")

SCHEMA_VERSION = 1
"""Bumped when the shape of --json output changes incompatibly.

Anything reading this output should check it. A CLI that silently changes its machine
output breaks the pipeline someone built on it, at a moment nobody is watching.
"""


def _load(path: pathlib.Path | None = None) -> World:
    p = path or _STATE
    if not p.exists():
        raise typer.BadParameter(f"no world at {p}; run `orrery ingest <fixture>` first")
    return World.load(p)


def _emit(payload: dict) -> None:
    json.dump({"schema": SCHEMA_VERSION, **payload}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


@app.command()
def ingest(fixture: pathlib.Path, out: pathlib.Path = _STATE, json_out: bool = False):
    """Ingest a YAML fixture into the world and persist it."""
    w = World()
    w.ingest(StaticYamlConnector(fixture).discover(), Resolver())
    out.parent.mkdir(parents=True, exist_ok=True)
    w.save(out)
    if json_out:
        _emit({"entities": len(w), "relations": len(w.relations()), "out": str(out)})
        return
    typer.echo(f"ingested {len(w)} entities, {len(w.relations())} relations -> {out}")


@app.command()
def blast(
    entity_id: str,
    max_hops: int | None = None,
    world: pathlib.Path | None = None,
    json_out: bool = False,
):
    """If this entity goes down, what is in range? Structural blast radius."""
    w = _load(world)
    br = blast_radius(w, entity_id, max_hops)
    if json_out:
        _emit(
            {
                "root": entity_id,
                "root_kind": w.entity(entity_id).kind.value,
                "total": len(w) - 1,
                "impacted": [
                    {
                        "id": eid,
                        "kind": w.entity(eid).kind.value,
                        "hop": hop,
                        "path": br.paths.get(eid, []),
                    }
                    for eid, hop in sorted(br.impacted.items(), key=lambda kv: (kv[1], kv[0]))
                ],
            }
        )
        return
    typer.echo(f"root: {entity_id} ({w.entity(entity_id).kind})")
    for hop, ids in br.by_hop().items():
        typer.echo(f"  hop {hop}: " + ", ".join(f"{i} ({w.entity(i).kind})" for i in ids))
    typer.echo(f"impacted: {len(br.impacted)} / {len(w) - 1}")


@app.command()
def simulate(
    entity_id: str,
    event: str = "down",
    elapsed_s: int | None = None,
    world: pathlib.Path | None = None,
    json_out: bool = False,
):
    """Apply an event and propagate consequences through behavior models.

    Runs on a fork and does not persist.

    --elapsed-s asks the question that actually pages people: not "what happens the moment
    this dies" but "we have been down this long — what now?". Soft dependencies with a
    declared tolerance turn hard once it is exceeded.
    """
    w = _load(world).fork()
    effects = propagate(w, Event(entity_id, event), elapsed_s=elapsed_s)
    if json_out:
        _emit(
            {
                "trigger": entity_id,
                "event": event,
                "elapsed_s": elapsed_s,
                "effects": [
                    {
                        "id": e.entity_id,
                        "kind": w.entity(e.entity_id).kind.value,
                        "status": e.status.value if e.status else None,
                        "why": e.note,
                    }
                    for e in effects
                ],
            }
        )
        return
    for e in effects:
        st = e.status.value if e.status else "-"
        typer.echo(f"  {e.entity_id:<24} -> {st:<9} {e.note}")


@app.command()
def resolve(fixture: pathlib.Path, json_out: bool = False):
    """Propose entity-resolution candidates for a fixture. Never merges."""
    d = StaticYamlConnector(fixture).discover()
    groups = Resolver().propose(d.entities)
    if json_out:
        _emit(
            {
                "candidates": [
                    [{"id": e.id, "name": e.name, "kind": e.kind.value} for e in g]
                    for g in groups
                ]
            }
        )
        return
    for group in groups:
        typer.echo("candidate: " + " | ".join(f"{e.id} ({e.name})" for e in group))


@app.command(name="diff")
def diff_cmd(before: pathlib.Path, after: pathlib.Path, json_out: bool = False):
    """What changed between two world snapshots?

    A map that drifts without anyone noticing is the failure mode every CMDB dies of.
    Run this between snapshots to see what appeared, vanished, or was rewired.
    """
    d = diff(World.load(before), World.load(after))
    if json_out:
        _emit(d.to_dict())
        return
    typer.echo(d.summary())


@app.command()
def check(world: pathlib.Path | None = None, json_out: bool = False):
    """Is this map any good?

    Looks for the shapes that usually mean the map is wrong rather than the estate:
    entities nothing connects to, services with nowhere recorded to run, redundancy that
    exists on paper but not in the graph, and how much of the map rests on one source.
    """
    a = audit(_load(world))
    if json_out:
        _emit(a.to_dict())
        return
    typer.echo(a.summary())


@app.command()
def spof(
    limit: int = 20,
    kind: str | None = None,
    world: pathlib.Path | None = None,
    json_out: bool = False,
):
    """What is most dangerous? Entities ranked by how much goes with them.

    Structural reach, not predicted damage — it deliberately ignores replicas, because
    redundancy that is recorded but not real is exactly what this is for finding.
    """
    w = _load(world)
    kinds = (EntityKind(kind),) if kind else None
    risks = single_points_of_failure(w, limit=limit, kinds=kinds)
    if json_out:
        _emit({"total": len(w), "risks": [r.to_dict() for r in risks]})
        return
    typer.echo(format_risks(risks, len(w)))


@app.command()
def backtest(path: pathlib.Path, verbose: bool = False, json_out: bool = False):
    """Replay past incidents and score the engine against what actually happened."""
    incidents = Incident.load_dir(path)
    if not incidents:
        raise typer.BadParameter(f"no incident records found in {path}")
    report = run(incidents)
    if json_out:
        _emit(
            {
                "incidents": report.incidents,
                "scored": report.scored,
                "skipped": report.skipped,
                "recall": report.recall(),
                "precision": report.precision(),
                "exact": report.exact_rate(),
                "outcomes": {o.value: report.total(o) for o in Outcome},
                "comparisons": [
                    {
                        "id": c.incident_id,
                        "title": c.title,
                        "scored": c.scored,
                        "judgements": [
                            {
                                "id": j.entity_id,
                                "predicted": j.predicted.value,
                                "actual": j.actual.value,
                                "outcome": j.outcome.value,
                            }
                            for j in c.judgements
                        ],
                    }
                    for c in report.comparisons
                ],
            }
        )
        return
    typer.echo(format_report(report))
    if verbose:
        typer.echo("\nper incident:")
        for c in report.comparisons:
            typer.echo(
                f"  {c.incident_id:<12} scored {c.scored:>3}  "
                f"miss {c.count(Outcome.MISS)}  false alarm {c.count(Outcome.FALSE_ALARM)}  "
                f"{c.title}"
            )


if __name__ == "__main__":  # `python -m orrery.cli`
    app()
