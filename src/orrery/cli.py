from __future__ import annotations

import pathlib

import typer

from orrery.connectors import StaticYamlConnector
from orrery.resolve import Resolver
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius

app = typer.Typer(help="orrery: every server is on the map, and when you act, the consequence is computed.")
_STATE = pathlib.Path(".orrery/world.yaml")


def _load() -> World:
    if not _STATE.exists():
        raise typer.BadParameter("no world ingested yet; run `orrery ingest <fixture>`")
    return World.load(_STATE)


@app.command()
def ingest(fixture: pathlib.Path):
    """Ingest a YAML fixture into the world and persist it under .orrery/."""
    w = World()
    w.ingest(StaticYamlConnector(fixture).discover(), Resolver())
    _STATE.parent.mkdir(exist_ok=True)
    w.save(_STATE)
    typer.echo(f"ingested {len(w)} entities, {len(w.relations())} relations -> {_STATE}")


@app.command()
def blast(entity_id: str, max_hops: int | None = None):
    """If this entity goes down, what dies? Structural blast radius."""
    w = _load()
    br = blast_radius(w, entity_id, max_hops)
    typer.echo(f"root: {entity_id} ({w.entity(entity_id).kind})")
    for hop, ids in br.by_hop().items():
        typer.echo(f"  hop {hop}: " + ", ".join(f"{i} ({w.entity(i).kind})" for i in ids))
    typer.echo(f"impacted: {len(br.impacted)} / {len(w) - 1}")


@app.command()
def simulate(entity_id: str, event: str = "down"):
    """Apply an event and propagate consequences through behavior models (fork; does not persist)."""
    w = _load().fork()
    effects = propagate(w, Event(entity_id, event))
    for e in effects:
        st = e.status.value if e.status else "-"
        typer.echo(f"  {e.entity_id:<24} -> {st:<9} {e.note}")


@app.command()
def resolve(fixture: pathlib.Path):
    """Propose entity-resolution candidates for a fixture (never merges)."""
    d = StaticYamlConnector(fixture).discover()
    for group in Resolver().propose(d.entities):
        typer.echo("candidate: " + " | ".join(f"{e.id} ({e.name})" for e in group))
