from __future__ import annotations

import difflib
import functools
import json
import pathlib
import sys

import typer
import yaml

from orrery.backtest import Incident, Outcome, format_report, run
from orrery.connectors import StaticYamlConnector
from orrery.resolve import Resolver
from orrery.sim import Event, propagate
from orrery.sim.propagate import INJECTABLE_EVENTS
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


def _resolve(world: World, entity_id: str) -> str:
    """Fail on an unknown id with the ids it might have been.

    Mistyping an id at 3am is the most likely thing anyone does with this tool, and a
    forty-line traceback for a typo teaches people the tool is fragile.
    """
    if entity_id in world.g:
        return entity_id
    near = difflib.get_close_matches(entity_id, list(world.g), n=5, cutoff=0.4)
    hint = f" Did you mean: {', '.join(near)}?" if near else ""
    raise typer.BadParameter(f"no entity {entity_id!r} in this world.{hint}")


def friendly(fn):
    """Turn the errors a user can actually cause into a sentence.

    Anything not listed here is a bug in orrery, and a traceback is the right output for
    those — it is what someone would paste into an issue.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError as exc:
            raise typer.BadParameter(f"no such file: {exc.filename}") from exc
        except IsADirectoryError as exc:
            raise typer.BadParameter(f"{exc.filename} is a directory, not a file") from exc
        except KeyError as exc:
            # The engine raises KeyError for an id it does not know. By the time it gets
            # here the message already names the id; a traceback would only bury it.
            raise typer.BadParameter(exc.args[0] if exc.args else str(exc)) from exc
        except yaml.YAMLError as exc:
            raise typer.BadParameter(f"not valid YAML: {exc}") from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    return wrapper


def _emit(payload: dict) -> None:
    json.dump({"schema": SCHEMA_VERSION, **payload}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


@app.command()
@friendly
def ingest(fixture: pathlib.Path, out: pathlib.Path = _STATE, json_out: bool = False):
    """Ingest a YAML fixture into the world and persist it."""
    w = World()
    w.ingest(StaticYamlConnector(fixture).discover(), Resolver())
    out.parent.mkdir(parents=True, exist_ok=True)
    w.save(out)
    if json_out:
        _emit(
            {
                "entities": len(w),
                "relations": len(w.relations()),
                "out": str(out),
                "collisions": [
                    {"id": i, "kept": kept, "discarded": lost} for i, kept, lost in w.collisions
                ],
            }
        )
        return
    typer.echo(f"ingested {len(w)} entities, {len(w.relations())} relations -> {out}")
    if w.collisions:
        typer.echo(
            f"  ⚠ {len(w.collisions)} id(s) arrived twice under different names and were "
            f"merged:"
        )
        for eid, kept, lost in w.collisions[:10]:
            typer.echo(f"    {eid}: kept {kept!r}, discarded {lost!r}")
        if len(w.collisions) > 10:
            typer.echo(f"    ... and {len(w.collisions) - 10} more")


@app.command()
@friendly
def blast(
    entity_id: str,
    max_hops: int | None = None,
    world: pathlib.Path | None = None,
    json_out: bool = False,
):
    """If this entity goes down, what is in range? Structural blast radius."""
    w = _load(world)
    entity_id = _resolve(w, entity_id)
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
@friendly
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
    entity_id = _resolve(w, entity_id)
    if event not in INJECTABLE_EVENTS:
        raise typer.BadParameter(
            f"unknown event {event!r}. Known: {', '.join(sorted(INJECTABLE_EVENTS))}. "
            f"An unrecognised event propagates nothing, which looks identical to "
            f"nothing being affected."
        )
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
@friendly
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
@friendly
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
@friendly
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
@friendly
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
    if kind and kind not in {k.value for k in EntityKind}:
        raise typer.BadParameter(
            f"unknown kind {kind!r}. Known: {', '.join(k.value for k in EntityKind)}"
        )
    kinds = (EntityKind(kind),) if kind else None
    risks = single_points_of_failure(w, limit=limit, kinds=kinds)
    if json_out:
        _emit({"total": len(w), "risks": [r.to_dict() for r in risks]})
        return
    typer.echo(format_risks(risks, len(w)))


@app.command()
@friendly
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
                "exact_on_impacted": report.exact_on_impacted(),
                "unverified_predictions": report.unverified,
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
