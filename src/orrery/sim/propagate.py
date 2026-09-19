"""Consequence propagation: apply an event to an entity, let its behavior model react,
then propagate emitted events to everything that depends on it (same edges as blast radius).

The computation is a monotone fixpoint. A status may only ever get worse, never better, so
the traversal converges no matter what order events arrive in and no matter how many cycles
the graph contains. That property is the whole design: an earlier version stopped at the
first visit to each entity, which meant a degrade arriving before a down left the entity
recorded as merely degraded. Arrival order decided the answer, and the answer was wrong.

Everything requiring a look at the graph is decided here, not in a behavior model. How many
places are left to run, whether a cluster still has quorum, whether a soft dependency has
been gone long enough to matter: all of it is resolved before the event reaches a model, and
travels as the event's name. Models that count would be models trusting attributes, and
attributes drift.
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
    # A host attaches to exactly one network segment, and losing the segment takes the
    # host with it. Excluding this once cost the engine an entire class of outage: a VLAN
    # or top-of-rack failure computed as affecting nothing at all.
    RelationKind.CONNECTS_TO,
)

_RANK: dict[Status, int] = {
    Status.UP: 0,
    Status.UNKNOWN: 0,
    Status.DEGRADED: 1,
    Status.DOWN: 2,
}

# Somewhere you can still run. DEGRADED counts — slow is not gone. UNKNOWN does not: a node
# the inventory cannot reach during an incident is not evidence of a survivor, and treating
# it as one produces the error class that hurts people.
_ALIVE = (Status.UP, Status.DEGRADED)

# A soft edge caps what crosses it at "degraded". Losing something you can live without
# slows you down; it does not kill you.
#
# An earlier version had soft edges absorb "degraded" entirely, on the reasoning that a
# slow optional dependency is not your problem. Backtesting said otherwise: a storefront
# whose checkout is slow is itself slow, and absorbing produced misses — predicting
# healthy for something that was not. A miss is the error class that gets people hurt,
# so a soft edge weakens events rather than swallowing them.
_SOFT_CAP: dict[str, str] = {
    "down": "dependency_degraded",
    "dependency_down": "dependency_degraded",
}

INJECTABLE_EVENTS = frozenset({"down", "degraded"})
"""What a caller may apply to an entity from outside.

Everything else — `dependency_down`, `place_lost`, `member_lost` — is produced by
propagation and describes something arriving over an edge. Accepting an arbitrary string
here meant a typo returned a clean, empty, confident result: nothing propagated, no error,
and a scenario that injected `memory_leak` briefed the agent with "0 entities unhealthy".
"""

MAX_DEGRADE_HOPS = 2
"""How many service calls a degradation travels before the model stops claiming to know.

Degradation has no magnitude here — an entity is slow or it is not — so it cannot
attenuate the way a real one does. Without a bound, one slow telemetry sink paints every
service that transitively touches it, and a report where everything is yellow is a report
nobody reads. Two hops is a blunt instrument chosen because it keeps the cases that are
obviously right (a storefront whose checkout is slow is slow) and drops the ones that are
obviously wrong (nine calls later, through unrelated systems).

Only `DEPENDS_ON` edges count. A degraded host degrades the node on it, which degrades
the service on that — those are not calls, they are places, and a first version that
counted them spent the whole budget on infrastructure and reported a brown-out segment as
touching no application at all.

The real fix is capacity, which this engine does not model. Until then this is a guess,
and it is stated here rather than buried so that anyone who disagrees can raise it.
"""

_CALL_EDGES = frozenset({RelationKind.DEPENDS_ON})

# Which kinds carry their own death downward to their members. A cluster does: the nodes
# in it stop being nodes. A load balancer does not: a VIP losing its pool, or dying
# itself, leaves the backends running and merely unreachable by that path — and treating
# it otherwise had one backend's death power off every other backend and its databases.
_DOWNWARD_MEMBERSHIP = frozenset({EntityKind.CLUSTER})


def consequence_crosses(edge: RelationKind, dst_kind: EntityKind) -> bool:
    """Does the death of the thing at `dst` reach whatever points at it over `edge`?

    True for every edge except membership of a kind that does not carry its own death
    downward. It lives here, next to `_DEPENDENT_EDGES`, because the two are one rule in
    two halves and splitting them is how this engine drifts: 0.2.0 unified the edge list
    after `blast` said a network segment failure affected nothing while `simulate` said
    it affected plenty, and the membership rule then rebuilt the same disagreement one
    level down, where only `propagate` could see it. `blast lb-edge` listed a backend
    that `simulate lb-edge` left untouched, and `spof` ranked the load balancer for
    carrying it.

    Two answers from one tool that contradict each other cost more trust than either one
    being wrong.
    """
    if edge is RelationKind.MEMBER_OF:
        return dst_kind in _DOWNWARD_MEMBERSHIP
    return True


@dataclass
class Event:
    entity_id: str
    name: str  # "down", "degraded", "place_lost", ...
    degrade_hops: int = 0
    """How many hops this degradation has already travelled. Ignored for outages."""


def _is_degrade(event: str) -> bool:
    return event in ("degraded", "dependency_degraded")


def _translate(event: str, edge: RelationKind, world: World, dependent_id: str) -> str:
    """What the dependent actually experiences.

    Something whose node died does not experience "down"; it experiences losing one of the
    places it runs. Whether that matters depends on how many places are left, which is a
    question about the graph rather than about the entity — so it is answered here, by
    counting, rather than by trusting a `replicas` attribute written down once.
    """
    if edge is RelationKind.RUNS_ON and event == "dependency_down":
        places = world.out_edges(dependent_id, RelationKind.RUNS_ON)
        alive = sum(1 for p in places if world.entity(p).status in _ALIVE)
        # nowhere left to run: this is not a degradation, it is an outage
        return "place_lost" if alive else "dependency_down"
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
    raw = relation.attrs.get("tolerance_s", 0)
    try:
        tolerance = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"{relation.src} -> {relation.dst}: tolerance_s must be a number of seconds, "
            f"got {raw!r}"
        ) from None
    if tolerance <= 0:
        return False
    return elapsed_s >= tolerance


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
    if event.name not in INJECTABLE_EVENTS:
        raise ValueError(
            f"unknown event {event.name!r}. Injectable: "
            f"{', '.join(sorted(INJECTABLE_EVENTS))}. Anything else is produced by "
            f"propagation, and an unrecognised name propagates nothing — which reads "
            f"exactly like nothing being affected."
        )
    models = default_models() if models is None else models
    effects: dict[str, Effect] = {}
    order: list[str] = []
    queue: list[Event] = [event]
    # The fewest call-hops at which degradation has reached each entity. Status only
    # worsens and this only shrinks, so both are monotone and the walk still converges —
    # but a degradation arriving later by a shorter path has further to travel than the
    # one already recorded, and must be allowed to. Dropping it because the status did not
    # change made the answer depend on relation order in the file.
    fewest_hops: dict[str, int] = {}
    # Members still standing, per group, taken once and then shrunk as members die.
    # Recounting on every death was quadratic; decrementing a count was wrong, because a
    # member dead before the call started got subtracted a second time.
    standing: dict[str, set[str]] = {}

    while queue:
        ev = queue.pop(0)
        ent = world.entity(ev.entity_id)
        eff = models[ent.kind].react(ent, ev.name)

        shorter = _is_degrade(ev.name) and ev.degrade_hops < fewest_hops.get(
            ev.entity_id, MAX_DEGRADE_HOPS + 1
        )
        if _is_degrade(ev.name):
            fewest_hops[ev.entity_id] = min(
                ev.degrade_hops, fewest_hops.get(ev.entity_id, ev.degrade_hops)
            )

        if eff.status is not None:
            worsened = _RANK[eff.status] > _RANK[ent.status]
            # A worsening is news. So is a degradation arriving by a shorter path than
            # before, since it can reach further. Anything else is an event arriving a
            # second way, or a second call to propagate() on an already-damaged world.
            if not worsened and not shorter and ent.id in effects:
                continue
            if worsened:
                world.set_status(ent.id, eff.status)
            else:
                # The world already knows something at least this bad. Leave it, and
                # report what is true rather than what this model proposed — a status
                # may never improve, and the returned effects must say so too.
                eff = replace(eff, status=ent.status)
                if ent.id in effects:
                    # Keep the reason that goes with the status already recorded. Only the
                    # status was being carried over, so a degradation arriving by a shorter
                    # path overwrote the note and printed `down  dep degraded` — a row
                    # whose two halves contradict each other. The shorter path still
                    # matters for what this emits onward; it just did not cause this.
                    eff = replace(eff, note=effects[ent.id].note)
        elif not eff.emit:
            # Nothing happened here. Recording it would put a row with no status in the
            # output, which reads as "we looked and are not saying", and this tool has
            # enough of those already.
            continue

        if ent.id not in effects:
            order.append(ent.id)
        effects[ent.id] = eff

        queue.extend(_membership_effects(world, ent.id, standing))

        for emitted in eff.emit:
            for edge in _DEPENDENT_EDGES:
                if not consequence_crosses(edge, ent.kind):
                    # Neither this kind's death nor its degradation reaches its members. A
                    # load balancer losing one backend is thinner; the other backends are
                    # not slower for it, and a first version that let the degrade through
                    # had one dead backend paint every sibling and their dependents.
                    continue
                for rel in world.in_relations(ent.id, edge):
                    soft = rel.strength is RelationStrength.SOFT and not _tolerance_exceeded(
                        rel, elapsed_s
                    )
                    crossing = _soften(emitted) if soft else emitted
                    crossing = _translate(crossing, edge, world, rel.src)
                    hops = 0
                    if _is_degrade(crossing):
                        # Degradation that started here starts at zero. Degradation
                        # passing through spends a hop only on a call edge.
                        base = ev.degrade_hops if _is_degrade(ev.name) else 0
                        hops = base + (1 if edge in _CALL_EDGES else 0)
                        if hops > MAX_DEGRADE_HOPS:
                            continue  # past the point this model can honestly claim to know
                    queue.append(Event(rel.src, crossing, degrade_hops=hops))

    return [effects[eid] for eid in order]


def _membership_effects(
    world: World, member_id: str, standing: dict[str, set[str]] | None = None
) -> list[Event]:
    """What losing this member does to the things it belongs to.

    `MEMBER_OF` normally carries consequence downward: kill the cluster and its members go
    with it. Read upward it says something different and equally real — a load balancer
    whose backend died is thinner, and a quorum system that drops below its majority is
    finished. Both are facts about the graph rather than about one entity, because
    answering either means counting siblings.

    MEMBER_OF normally carries consequence downward: kill the cluster and its members go
    with it. Quorum is the same edge read upward. A three-member cluster shrugs off one
    loss and dies at two.

    What happens to the survivors after a quorum loss is the downward edge's business, and
    it is a modelling decision rather than a law. A quorum database really does stop
    serving from its remaining node; a control plane that loses quorum goes read-only
    while the workloads on it keep running. Declare that second case with a soft
    `MEMBER_OF` and the survivors degrade instead of dying.
    """
    if world.entity(member_id).status is not Status.DOWN:
        return []
    sets = {} if standing is None else standing
    out: list[Event] = []
    for group_id in world.out_edges(member_id, RelationKind.MEMBER_OF):
        group = world.entity(group_id)
        if group.status is Status.DOWN:
            continue
        quorum = group.attrs.get("quorum")
        if quorum:
            if group_id not in sets:
                sets[group_id] = {
                    m
                    for m in world.in_edges(group_id, RelationKind.MEMBER_OF)
                    if world.entity(m).status in _ALIVE
                }
            # Discarding is a no-op for a member that was never standing, which is the
            # whole reason this is a set: a node already dead before the call must not
            # be subtracted a second time when the walk reaches it.
            sets[group_id].discard(member_id)
            if len(sets[group_id]) < int(quorum):
                out.append(Event(group_id, "down"))
                continue
        # Below quorum, or no quorum declared: the group is thinner, not gone. Most kinds
        # have no opinion about that and ignore it; a load balancer does not.
        out.append(Event(group_id, "member_lost"))
    return out
