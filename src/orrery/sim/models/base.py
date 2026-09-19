"""Behavior models: how an entity of a given kind reacts to an event.

A model receives (entity, event) and returns effects: status changes and follow-on events.
Generic models live here; company-specific calibration lives outside this repo.

**Models do not count.** Anything that requires looking at the graph — how many places are
left to run, whether a cluster still has quorum — is decided in `propagate` before the
event reaches the model, and arrives encoded in the event name. A model that reads
`attrs` to decide how much redundancy an entity has is trusting a number somebody typed
once; that number drifts, and the graph does not. This split is not stylistic: a previous
version had `ServiceModel` re-derive redundancy from `attrs["replicas"]` after `propagate`
had already counted survivors, so a service on forty-nine healthy nodes was reported down
when one node rebooted.
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
        if event == "place_lost":
            # Somewhere it ran is gone but somewhere else is left. For an entity with no
            # opinion about redundancy that is a degradation, not an outage.
            return Effect(
                entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="lost a place to run"
            )
        return Effect(entity.id)


class ServiceModel:
    """A service dies when a hard dependency dies or it has nowhere left to run.

    `place_lost` means propagate already checked and found somewhere still standing, so the
    only honest answer is degraded. The service's declared replica count is not consulted:
    three replicas pinned to one node is one place to lose, and a `replicas` attribute that
    nobody has updated since the service was halved is worse than no attribute at all.
    """

    kind = EntityKind.SERVICE

    def react(self, entity: Entity, event: str) -> Effect:
        if event in ("down", "dependency_down"):
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"], note="hard dep down")
        if event == "place_lost":
            return Effect(
                entity.id,
                Status.DEGRADED,
                emit=["dependency_degraded"],
                note="lost one of its places to run",
            )
        if event in ("degraded", "dependency_degraded"):
            # Replica count does not help when something you depend on is slow — every
            # replica talks to the same slow thing.
            return Effect(
                entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="dep degraded"
            )
        return Effect(entity.id)


class DatabaseModel:
    """A database that loses one of several places to run fails over and serves reads.

    There is deliberately no `replica: true` shortcut. An attribute asserting a replica
    exists is a claim about redundancy that the graph can check, and `orrery check` flags
    exactly that shape — a declared replica with one recorded place to run — as redundancy
    on paper only. Believing the attribute here would have the engine contradict its own
    audit, and in the direction that hides an outage.
    """

    kind = EntityKind.DATABASE

    def react(self, entity: Entity, event: str) -> Effect:
        if event in ("down", "dependency_down"):
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"])
        if event == "place_lost":
            return Effect(
                entity.id,
                Status.DEGRADED,
                emit=["dependency_degraded"],
                note="failed over, reads only",
            )
        if event in ("degraded", "dependency_degraded"):
            return Effect(
                entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="dep degraded"
            )
        return Effect(entity.id)


class LoadBalancerModel:
    """A load balancer is down when it is down, and degraded when its pool thins out.

    Membership is read upward here: losing a backend does not take the VIP with it, it
    makes it thinner. Whether the VIP survives losing *enough* backends is a quorum
    question — declare `quorum` on the load balancer and propagate will enforce it.
    """

    kind = EntityKind.LOAD_BALANCER

    def react(self, entity: Entity, event: str) -> Effect:
        if event in ("down", "dependency_down"):
            return Effect(entity.id, Status.DOWN, emit=["dependency_down"], note="down")
        if event in ("degraded", "dependency_degraded", "place_lost", "member_lost"):
            return Effect(
                entity.id, Status.DEGRADED, emit=["dependency_degraded"], note="pool thinned"
            )
        return Effect(entity.id)


def default_models() -> dict[EntityKind, BehaviorModel]:
    models: dict[EntityKind, BehaviorModel] = {k: _Passthrough(k) for k in EntityKind}
    models[EntityKind.SERVICE] = ServiceModel()
    models[EntityKind.DATABASE] = DatabaseModel()
    models[EntityKind.LOAD_BALANCER] = LoadBalancerModel()
    return models
