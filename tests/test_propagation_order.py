"""The result must not depend on which path an event travelled first."""
from __future__ import annotations

import pytest

from orrery.connectors import StaticYamlConnector
from orrery.schema import (
    Entity,
    EntityKind,
    Relation,
    RelationKind,
    RelationStrength,
    Status,
)
from orrery.sim import Event, propagate
from orrery.world import World


def _converging_world(order: str) -> World:
    """A service reachable by both a soft path (degrade) and a hard path (kill).

        db-soft  <--soft--  svc-target  --hard-->  db-hard

    Both databases die. Whatever order the engine walks them in, the target must end up
    down: a hard dependency died, and no amount of arriving-degraded-first changes that.
    `order` flips which relation is registered first, which is what decides traversal
    order in the underlying graph.
    """
    w = World()
    w.add_entity(Entity(id="db-soft", kind=EntityKind.DATABASE, name="soft"))
    w.add_entity(Entity(id="db-hard", kind=EntityKind.DATABASE, name="hard"))
    w.add_entity(Entity(id="site", kind=EntityKind.SITE, name="site"))
    w.add_entity(
        Entity(id="svc-target", kind=EntityKind.SERVICE, name="target", attrs={"replicas": 3})
    )
    rels = [
        Relation(
            src="svc-target", dst="db-soft",
            kind=RelationKind.DEPENDS_ON, strength=RelationStrength.SOFT,
        ),
        Relation(src="svc-target", dst="db-hard", kind=RelationKind.DEPENDS_ON),
    ]
    if order == "hard-first":
        rels.reverse()
    w.add_relation(Relation(src="db-soft", dst="site", kind=RelationKind.HOSTED_IN))
    w.add_relation(Relation(src="db-hard", dst="site", kind=RelationKind.HOSTED_IN))
    for r in rels:
        w.add_relation(r)
    return w


@pytest.mark.parametrize("order", ["soft-first", "hard-first"])
def test_the_stronger_consequence_wins_regardless_of_arrival_order(order: str):
    w = _converging_world(order)
    propagate(w, Event("site", "down"))
    assert w.entity("svc-target").status is Status.DOWN


def test_a_later_weaker_event_cannot_improve_a_status():
    w = _converging_world("hard-first")
    propagate(w, Event("db-hard", "down"))
    assert w.entity("svc-target").status is Status.DOWN
    # the soft one dying afterwards must not walk the service back to merely degraded
    propagate(w, Event("db-soft", "down"))
    assert w.entity("svc-target").status is Status.DOWN


def test_cycles_terminate():
    w = World()
    for i in range(3):
        w.add_entity(Entity(id=f"svc-{i}", kind=EntityKind.SERVICE, name=str(i)))
    w.add_relation(Relation(src="svc-0", dst="svc-1", kind=RelationKind.DEPENDS_ON))
    w.add_relation(Relation(src="svc-1", dst="svc-2", kind=RelationKind.DEPENDS_ON))
    w.add_relation(Relation(src="svc-2", dst="svc-0", kind=RelationKind.DEPENDS_ON))
    propagate(w, Event("svc-0", "down"))
    assert all(w.entity(f"svc-{i}").status is Status.DOWN for i in range(3))


def test_each_entity_appears_once_in_the_result():
    w = _converging_world("soft-first")
    effects = propagate(w, Event("site", "down"))
    ids = [e.entity_id for e in effects]
    assert len(ids) == len(set(ids))


def test_result_reports_the_final_state_not_the_first_guess():
    w = _converging_world("soft-first")
    effects = propagate(w, Event("site", "down"))
    target = next(e for e in effects if e.entity_id == "svc-target")
    assert target.status is Status.DOWN


@pytest.mark.parametrize("order", ["soft-first", "hard-first"])
def test_the_reason_agrees_with_the_status_whichever_path_arrived_first(order: str):
    """The status is order-independent because it may only worsen. The reason beside it
    has to travel with it: carrying the status over from a stronger effect while taking
    the note from a weaker one printed `down  dep degraded`, a row whose two halves deny
    each other, and which half you got depended on traversal order."""
    effects = propagate(_converging_world(order), Event("site", "down"))
    target = next(e for e in effects if e.entity_id == "svc-target")
    assert target.status is Status.DOWN
    assert "degraded" not in target.note


# ---- time: a soft dependency is soft only for a while ----


def _demo() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def test_without_a_clock_soft_stays_soft():
    w = _demo().fork()
    propagate(w, Event("ext-payments", "down"))
    assert w.entity("svc-checkout").status is Status.DEGRADED


def test_a_tolerance_only_bites_once_declared():
    # Without a declared tolerance a soft edge stays soft however long the outage runs.
    # The engine has no basis for inventing a deadline nobody gave it.
    w = _demo().fork()
    rel = next(
        r for r in w.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    )
    rel.attrs.pop("tolerance_s", None)
    propagate(w, Event("ext-payments", "down"), elapsed_s=4 * 3600)
    assert w.entity("svc-checkout").status is Status.DEGRADED


def test_the_declared_tolerance_decides_the_outcome():
    w = _demo().fork()
    propagate(w, Event("ext-payments", "down"), elapsed_s=4 * 3600)
    assert w.entity("svc-checkout").status is Status.DOWN


def test_past_its_tolerance_a_soft_dependency_behaves_as_hard():
    w = _demo().fork()
    rel = next(
        r for r in w.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    )
    rel.attrs["tolerance_s"] = 40 * 60  # the retry queue holds about forty minutes

    early = _demo().fork()
    next(
        r for r in early.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    ).attrs["tolerance_s"] = 40 * 60

    propagate(early, Event("ext-payments", "down"), elapsed_s=10 * 60)
    assert early.entity("svc-checkout").status is Status.DEGRADED

    propagate(w, Event("ext-payments", "down"), elapsed_s=4 * 3600)
    assert w.entity("svc-checkout").status is Status.DOWN
