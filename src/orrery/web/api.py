"""What the map page asks, answered by the engine.

The CLI's `--json-out` for `blast` and `simulate` is built here too. A page and a command
that shaped the same answer twice would drift, and two views of one estate that disagree
cost more trust than either one being wrong.
"""
from __future__ import annotations

import datetime as _dt
from importlib import metadata

from orrery.sim import Event, propagate
from orrery.sim.propagate import _DEPENDENT_EDGES, INJECTABLE_EVENTS, consequence_crosses
from orrery.world import World, audit, blast_radius, single_points_of_failure

EXPORT_LIMIT = 3000
"""Above this many entities, `--export` refuses and points at `orrery map`.

An export precomputes every simulation the page can ask for: each entity, each injectable
event, each tolerance boundary. That is fine for a demo world and for a team's corner of an
estate, and a file of hundreds of megabytes for the whole of one. The live server answers
the same questions on demand and has no such limit.
"""


def blast_payload(world: World, entity_id: str, max_hops: int | None = None) -> dict:
    br = blast_radius(world, entity_id, max_hops)
    return {
        "root": entity_id,
        "root_kind": world.entity(entity_id).kind.value,
        "total": len(world) - 1,
        "impacted": [
            {
                "id": eid,
                "kind": world.entity(eid).kind.value,
                "hop": hop,
                "path": br.paths.get(eid, []),
            }
            for eid, hop in sorted(br.impacted.items(), key=lambda kv: (kv[1], kv[0]))
            if eid not in br.through_quorum
        ],
        # in range only if the quorum group named also loses enough other voters
        "through_quorum": [
            {"id": eid, "kind": world.entity(eid).kind.value, "group": g, "path": br.paths.get(eid, [])}
            for eid, g in sorted(br.through_quorum.items())
        ],
    }


def simulate_payload(
    world: World, entity_id: str, event: str = "down", elapsed_s: int | None = None
) -> dict:
    """Simulate on a fork. The world passed in is never changed."""
    if event not in INJECTABLE_EVENTS:
        raise ValueError(
            f"unknown event {event!r}. Known: {', '.join(sorted(INJECTABLE_EVENTS))}. "
            f"An unrecognised event propagates nothing, which looks identical to "
            f"nothing being affected."
        )
    w = world.fork()
    effects = propagate(w, Event(entity_id, event), elapsed_s=elapsed_s)
    return {
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


def _version() -> str:
    try:
        return metadata.version("orrery-engine")
    except metadata.PackageNotFoundError:  # running from a source tree nobody installed
        return "unknown"


def _tolerances(world: World) -> list[int]:
    """Every elapsed time at which some answer can change, in seconds, ascending.

    A soft edge turns hard at exactly its declared tolerance and at no other moment, so
    between two of these the simulation is constant. The page draws them on its clock, and
    an export needs one precomputed answer per interval rather than per second.
    """
    out: set[int] = set()
    for r in world.relations():
        raw = r.attrs.get("tolerance_s")
        try:
            t = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            continue  # `simulate` reports the malformed value when it is actually crossed
        if t > 0:
            out.add(t)
    return sorted(out)


def world_payload(world: World) -> dict:
    """Everything the page needs to draw the map before anyone clicks anything."""
    # Reach for every entity, sites included: the page sizes each body by what goes with
    # it, and leaving sites out there would draw a datacentre smaller than a rack.
    reach = {
        r.entity_id: {"reach": r.reach, "share": r.share}
        for r in single_points_of_failure(world, limit=len(world))
    }
    carries = {
        e.id: tuple(k.value for k in _DEPENDENT_EDGES if consequence_crosses(k, e.kind))
        for e in world.entities()
    }
    return {
        "version": _version(),
        "entities": [
            {
                "id": e.id,
                "kind": e.kind.value,
                "name": e.name,
                "status": e.status.value,
                "attrs": e.attrs,
                "sources": sorted({p.source for p in e.provenance}),
                "reach": reach.get(e.id, {}).get("reach", 0),
                "share": round(reach.get(e.id, {}).get("share", 0.0), 4),
                "carries": list(carries[e.id]),
            }
            for e in world.entities()
        ],
        "relations": [
            {
                "src": r.src,
                "dst": r.dst,
                "kind": r.kind.value,
                "strength": r.strength.value,
                "attrs": r.attrs,
            }
            for r in world.relations()
        ],
        "tolerances": _tolerances(world),
        "events": sorted(INJECTABLE_EVENTS),
        "audit": audit(world).to_dict(),
    }


def snapshot_payload(world: World) -> dict:
    """The world plus every answer the page can ask for, for a file with no server behind it.

    Simulations are keyed `"<id>|<event>|<bucket>"`, where bucket 0 is "no time has passed"
    and bucket i is "at least `tolerances[i-1]` seconds have". Elapsed times inside one
    bucket give identical answers, so this is exact rather than sampled.
    """
    if len(world) > EXPORT_LIMIT:
        raise ValueError(
            f"this world has {len(world):,} entities; an export precomputes every "
            f"simulation and is capped at {EXPORT_LIMIT:,}. Serve it live with "
            f"`orrery map` instead."
        )
    base = world_payload(world)
    buckets: list[int | None] = [None, *base["tolerances"]]
    blasts: dict[str, dict] = {}
    sims: dict[str, list] = {}
    for e in world.entities():
        br = blast_payload(world, e.id)
        blasts[e.id] = {"impacted": br["impacted"]}
        for event in base["events"]:
            for i, elapsed in enumerate(buckets):
                effects = simulate_payload(world, e.id, event, elapsed)["effects"]
                sims[f"{e.id}|{event}|{i}"] = [
                    [f["id"], f["status"], f["why"]] for f in effects
                ]
    return {
        **base,
        "generated_at": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
        "blasts": blasts,
        "sims": sims,
    }
