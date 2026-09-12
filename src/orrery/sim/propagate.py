"""Consequence propagation: apply an event to an entity, let its behavior model react,
then propagate emitted events to everything that depends on it (same edges as blast radius)."""
from __future__ import annotations

from dataclasses import dataclass

from orrery.schema import EntityKind, RelationKind
from orrery.world import World

from .models import BehaviorModel, Effect, default_models

_DEPENDENT_EDGES = (
    RelationKind.RUNS_ON,
    RelationKind.HOSTED_IN,
    RelationKind.MEMBER_OF,
    RelationKind.DEPENDS_ON,
)


@dataclass
class Event:
    entity_id: str
    name: str  # "down", "degraded", "node_lost", ...


def _translate(event: str, dependent_kind: EntityKind, edge: RelationKind) -> str:
    # a service running on a node that went down experiences "node_lost", not "down"
    if edge == RelationKind.RUNS_ON and dependent_kind == EntityKind.SERVICE and event == "dependency_down":
        return "node_lost"
    return event


def propagate(
    world: World, event: Event, models: dict[EntityKind, BehaviorModel] | None = None
) -> list[Effect]:
    models = models or default_models()
    applied: list[Effect] = []
    seen: set[str] = set()
    queue: list[Event] = [event]
    while queue:
        ev = queue.pop(0)
        if ev.entity_id in seen:
            continue
        seen.add(ev.entity_id)
        ent = world.entity(ev.entity_id)
        eff = models[ent.kind].react(ent, ev.name)
        if eff.status is not None:
            world.set_status(ent.id, eff.status)
        applied.append(eff)
        for emitted in eff.emit:
            for edge in _DEPENDENT_EDGES:
                for dep in world.in_edges(ent.id, edge):
                    dep_kind = world.entity(dep).kind
                    queue.append(Event(dep, _translate(emitted, dep_kind, edge)))
    return applied
