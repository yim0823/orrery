"""A fork shares structure with its parent. These are the seams where that could leak.

Forking is on the path of every simulation, so it is cheap by design: the fork shares the
graph and keeps private copies only of entities it writes to. Cheap sharing is exactly the
kind of optimization that is correct until it quietly is not, which is what this file is
for.
"""
from __future__ import annotations

import pathlib
import tempfile

from orrery.connectors import StaticYamlConnector
from orrery.schema import Entity, EntityKind, Relation, RelationKind, Status
from orrery.sim import Event, propagate
from orrery.world import World


def _world() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def test_damage_on_a_fork_does_not_reach_the_parent():
    parent = _world()
    child = parent.fork()
    propagate(child, Event("site-a", "down"))
    assert child.entity("svc-inventory").status is Status.DOWN
    assert parent.entity("svc-inventory").status is Status.UP


def test_two_forks_do_not_see_each_other():
    parent = _world()
    a, b = parent.fork(), parent.fork()
    propagate(a, Event("db-stock", "down"))
    assert a.entity("svc-inventory").status is Status.DOWN
    assert b.entity("svc-inventory").status is Status.UP


def test_attributes_written_on_a_fork_stay_there():
    parent = _world()
    child = parent.fork()
    child._own("svc-web").attrs["replicas"] = 99
    assert parent.entity("svc-web").attrs["replicas"] == 3


def test_entities_listing_reflects_the_fork_not_the_parent():
    parent = _world()
    child = parent.fork()
    propagate(child, Event("db-stock", "down"))
    broken = {e.id for e in child.entities() if e.status is not Status.UP}
    assert "svc-inventory" in broken
    assert not [e for e in parent.entities() if e.status is not Status.UP]


def test_a_fork_of_a_fork_still_isolates():
    parent = _world()
    child = parent.fork()
    grandchild = child.fork()
    propagate(grandchild, Event("db-stock", "down"))
    assert grandchild.entity("svc-inventory").status is Status.DOWN
    assert child.entity("svc-inventory").status is Status.UP
    assert parent.entity("svc-inventory").status is Status.UP


def test_adding_an_entity_to_a_fork_does_not_grow_the_parent():
    # Structural writes change the graph rather than one entity, so the fork has to stop
    # sharing first. Writing through to the parent would surface in whatever ran next.
    parent = _world()
    before = len(parent)
    child = parent.fork()
    child.add_entity(Entity(id="host-new", kind=EntityKind.HOST, name="new"))
    assert len(child) == before + 1
    assert len(parent) == before


def test_adding_a_relation_to_a_fork_does_not_rewire_the_parent():
    parent = _world()
    before = len(parent.relations())
    child = parent.fork()
    child.add_relation(Relation(src="svc-web", dst="db-orders", kind=RelationKind.DEPENDS_ON))
    assert len(child.relations()) == before + 1
    assert len(parent.relations()) == before


def test_detaching_keeps_damage_already_done():
    parent = _world()
    child = parent.fork()
    propagate(child, Event("db-stock", "down"))
    child.add_entity(Entity(id="host-new", kind=EntityKind.HOST, name="new"))
    # the add forced a detach; the statuses set before it must survive
    assert child.entity("svc-inventory").status is Status.DOWN
    assert parent.entity("svc-inventory").status is Status.UP


def test_a_deep_fork_shares_nothing():
    parent = _world()
    child = parent.fork(deep=True)
    rel = next(
        r for r in child.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    )
    rel.attrs["tolerance_s"] = 1
    original = next(
        r for r in parent.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    )
    assert original.attrs.get("tolerance_s") != 1


def test_saving_a_fork_writes_the_forks_state():
    parent = _world()
    child = parent.fork()
    propagate(child, Event("db-stock", "down"))
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "w.yaml"
        child.save(p)
        reloaded = World.load(p)
    assert reloaded.entity("svc-inventory").status is Status.DOWN
