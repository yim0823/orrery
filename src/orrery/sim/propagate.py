"""Consequence propagation: apply an event to an entity, let its behavior model react,
then propagate emitted events to everything that depends on it (same edges as blast radius).

The computation is a monotone fixpoint. A status may only ever get worse, never better, so
the traversal converges no matter what order events arrive in and no matter how many cycles
the graph contains. That property is the whole design: an earlier version stopped at the
first visit to each entity, which meant a degrade arriving before a down left the entity
recorded as merely degraded. Arrival order decided the answer, and the answer was wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from orrery.schema import EntityKind, RelationKind, RelationStrength, Status
from orrery.world import World

from .models import BehaviorModel, Effect, default_models

_DEPENDENT_EDGES = (
    RelationKind.RUNS_ON,
    RelationKind.HOSTED_IN,
    RelationKind.MEMBER_OF,
    RelationKind.DEPENDS_ON,
)

_RANK: dict[Status, int] = {
    Status.UP: 0,
    Status.UNKNOWN: 0,
    Status.DEGRADED: 1,
    Status.DOWN: 2,
}

# A soft edge caps what crosses it at "degraded". Losing something you can live without
# slows you down; it does not kill you.
#
# An earlier version had soft edges absorb "degraded" entirely, on the reasoning that a
# slow optional dependency is not your problem. Backtesting said otherwise: a storefront
# whose checkout is slow is itself slow, and absorbing produced misses — predicting
# healthy for something that was not. A miss is the error class that gets people hurt,
# so a soft edge now weakens events rather than swallowing them.
_SOFT_CAP: dict[str, str] = {
    "down": "dependency_degraded",
    "dependency_down": "dependency_degraded",
    "node_lost": "dependency_degraded",
}

DEFAULT_TOLERANCE_S = 0
"""How long a soft dependency stays soft when the relation does not say.

Zero means "soft for as long as you like" — the tolerance only bites once a relation
declares one. A default of, say, an hour would silently turn every soft edge hard in
long outages, which is a claim the model has no basis for making on its own.
"""


@dataclass
class Event:
    entity_id: str
    name: str  # "down", "degraded", "node_lost", ...


def _translate(
    event: str,
    dependent_kind: EntityKind,
    edge: RelationKind,
    world: World,
    dependent_id: str,
) -> str:
    """What the dependent actually experiences.

    A service whose node died does not experience "down"; it experiences losing one of
    the places it runs. Whether that matters depends on how many places are left, which
    is a question about the graph and not about the service — so it is answered here,
    by counting, rather than by trusting a `replicas` attribute that was written down
    once and has been drifting ever since.
    """
    if (
        edge is RelationKind.RUNS_ON
        and dependent_kind is EntityKind.SERVICE
        and event == "dependency_down"
    ):
        hosts = world.out_edges(dependent_id, RelationKind.RUNS_ON)
        alive = sum(1 for h in hosts if world.entity(h).status is not Status.DOWN)
        # nowhere left to run: this is not a degradation, it is an outage
        return "node_lost" if alive else "dependency_down"
    return event


def _soften(event: str) -> str:
    """What a soft edge lets through: the same event, capped at degraded."""
    return _SOFT_CAP.get(event, event)


def _tolerance_exceeded(relation, elapsed_s: int | None) -> bool:
    """Has this soft dependency been gone long enough to stop being survivable?

    A checkout service queues orders while its payment provider is away — until the queue
    fills. Past that point the dependency is load-bearing after all, so the edge is treated
    as hard. A relation opts in by declaring `tolerance_s`; without one it stays soft.
    """
    if elapsed_s is None:
        return False
    tolerance = relation.attrs.get("tolerance_s", DEFAULT_TOLERANCE_S)
    if not tolerance:
        return False
    return elapsed_s >= int(tolerance)


def propagate(
    world: World,
    event: Event,
    models: dict[EntityKind, BehaviorModel] | None = None,
    elapsed_s: int | None = None,
) -> list[Effect]:
    """Apply `event` and settle the world into its consequence.

    `elapsed_s` is how long the triggering failure has been going on. Leave it out for the
    instantaneous question ("what happens the moment this dies"). Supply it to ask the
    question that actually pages people: "we have been down forty minutes — what now?"
    """
    models = models or default_models()
    effects: dict[str, Effect] = {}
    order: list[str] = []
    queue: list[Event] = [event]

    while queue:
        ev = queue.pop(0)
        ent = world.entity(ev.entity_id)
        eff = models[ent.kind].react(ent, ev.name)

        if eff.status is not None:
            worsened = _RANK[eff.status] > _RANK[ent.status]
            # Only a worsening tells us anything new. Anything else is an event arriving
            # by a second path, or a second call to propagate() on a world that already
            # carries damage from the first.
            if not worsened and ent.id in effects:
                continue
            if worsened:
                world.set_status(ent.id, eff.status)
            else:
                # The world already knows something at least this bad. Leave it, and
                # report what is true rather than what this model proposed — a status
                # may never improve, and the returned effects must say so too.
                eff = replace(eff, status=ent.status)
        elif ent.id in effects:
            continue

        if ent.id not in effects:
            order.append(ent.id)
        effects[ent.id] = eff

        queue.extend(_quorum_failures(world, ent.id))

        for emitted in eff.emit:
            for edge in _DEPENDENT_EDGES:
                for rel in world.in_relations(ent.id, edge):
                    soft = rel.strength is RelationStrength.SOFT and not _tolerance_exceeded(
                        rel, elapsed_s
                    )
                    crossing = _soften(emitted) if soft else emitted
                    dep_kind = world.entity(rel.src).kind
                    queue.append(
                        Event(rel.src, _translate(crossing, dep_kind, edge, world, rel.src))
                    )

    return [effects[eid] for eid in order]


def _quorum_failures(world: World, member_id: str) -> list[Event]:
    """Clusters this entity belongs to that have just lost their majority.

    MEMBER_OF normally carries consequence downward: kill the cluster and its members go
    with it. Quorum is the same edge read upward. A three-member cluster shrugs off one
    loss and dies at two, and when it dies it takes the survivor with it — a lone etcd
    member with no quorum is as useless as the two that are gone, even though nothing
    touched the machine it runs on.

    This lives here rather than in a behavior model because it is a fact about the graph,
    not about one entity: answering it means counting siblings.
    """
    if world.entity(member_id).status is not Status.DOWN:
        return []
    out: list[Event] = []
    for cluster_id in world.out_edges(member_id, RelationKind.MEMBER_OF):
        cluster = world.entity(cluster_id)
        quorum = cluster.attrs.get("quorum")
        if not quorum or cluster.status is Status.DOWN:
            continue
        members = world.in_edges(cluster_id, RelationKind.MEMBER_OF)
        alive = sum(1 for m in members if world.entity(m).status is not Status.DOWN)
        if alive < int(quorum):
            out.append(Event(cluster_id, "down"))
    return out
