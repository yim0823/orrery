"""Behavior models: how an entity of a given kind reacts to an event.

A model receives (entity, event) and returns effects: status changes and follow-on events.
Generic models live here; company-specific calibration lives outside this repo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from orrery.schema import Entity, EntityKind, Status


@dataclass
class Effect:
    entity_id: str
    status: Status | None = None
    emit: list[str] = field(default_factory=list)  # event names to propagate to dependents
    note: str = ""


class BehaviorModel(Protocol):
    kind: EntityKind

    def react(self, entity: Entity, event: str) -> Effect: ...


class _Passthrough:
    """Default: if a dependency goes down, this goes down; if degraded, degraded."""

    def __init__(self, kind: EntityKind):
        self.kind = kind

    def react(self, entity: Entity, event: str) -> Effect:
        if event in ("down", "dependency_down"):
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"], note="passthrough")
        if event in ("degraded", "dependency_degraded"):
            return Effect(entity.id, Status.DEGRADED, emit=["dependency_degraded"])
        return Effect(entity.id)


class ServiceModel:
    """A service with replicas > 1 degrades (not dies) when one dependency degrades;
    dies when a hard dependency dies."""

    kind = EntityKind.SERVICE

    def react(self, entity: Entity, event: str) -> Effect:
        replicas = int(entity.attrs.get("replicas", 1))
        if event in ("down", "dependency_down"):
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"], note="hard dep down")
        if event == "dependency_degraded":
            # Your own replica count does not help when something you depend on is slow —
            # every replica talks to the same degraded thing. Replicas matter for losing
            # a node you run on, which is `node_lost` below.
            return Effect(
                entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="dep degraded"
            )
        if event == "node_lost":
            # losing one node out of N degrades; losing the last one kills
            return Effect(
                entity.id,
                Status.DEGRADED if replicas > 1 else Status.DOWN,
                emit=["dependency_degraded" if replicas > 1 else "dependency_down"],
                note=f"replicas={replicas}",
            )
        return Effect(entity.id)


class DatabaseModel:
    """Primary down with a replica -> degraded (read-only); without -> down."""

    kind = EntityKind.DATABASE

    def react(self, entity: Entity, event: str) -> Effect:
        has_replica = bool(entity.attrs.get("replica", False))
        if event in ("down", "dependency_down"):
            if has_replica:
                return Effect(entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="failover to replica, read-only")
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"])
        return Effect(entity.id)


def default_models() -> dict[EntityKind, BehaviorModel]:
    models: dict[EntityKind, BehaviorModel] = {k: _Passthrough(k) for k in EntityKind}
    models[EntityKind.SERVICE] = ServiceModel()
    models[EntityKind.DATABASE] = DatabaseModel()
    return models
