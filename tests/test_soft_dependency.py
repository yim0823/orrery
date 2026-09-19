from __future__ import annotations

from orrery.connectors import StaticYamlConnector
from orrery.schema import EntityKind, Relation, RelationKind, RelationStrength, Status
from orrery.sim import Event, propagate
from orrery.world import World

FIXTURE = "fixtures/demo-world.yaml"


def _world() -> World:
    w = World()
    w.ingest(StaticYamlConnector(FIXTURE).discover())
    return w


def test_relations_are_hard_unless_stated():
    # Guessing soft would hide outages; guessing hard only annoys people.
    r = Relation(src="a", dst="b", kind=RelationKind.DEPENDS_ON)
    assert r.strength is RelationStrength.HARD


def test_strength_parses_from_yaml():
    d = StaticYamlConnector(FIXTURE).discover()
    soft = [r for r in d.relations if r.strength is RelationStrength.SOFT]
    assert {(r.src, r.dst) for r in soft} == {
        ("svc-checkout", "ext-payments"),
        ("svc-web", "svc-checkout"),
    }


def test_soft_dependency_dying_degrades_instead_of_killing():
    w = _world().fork()
    propagate(w, Event("ext-payments", "down"))
    assert w.entity("svc-checkout").status is Status.DEGRADED


def test_hard_dependency_dying_still_kills():
    w = _world().fork()
    propagate(w, Event("db-stock", "down"))
    assert w.entity("svc-inventory").status is Status.DOWN


def test_soft_edge_weakens_but_does_not_silence():
    # A storefront whose checkout is slow is itself slow. Absorbing the event here
    # produced misses in backtesting, which is the worse error to make.
    w = _world().fork()
    propagate(w, Event("ext-payments", "down"))
    assert w.entity("svc-web").status is Status.DEGRADED


def test_soft_edge_caps_severity_it_does_not_invert_it():
    w = _world().fork()
    propagate(w, Event("ext-payments", "down"))
    # nothing downstream of a soft edge may end up worse than degraded
    downstream = [w.entity("svc-checkout"), w.entity("svc-web")]
    assert all(e.status is not Status.DOWN for e in downstream)


def test_unrelated_service_is_untouched():
    w = _world().fork()
    propagate(w, Event("ext-payments", "down"))
    assert w.entity("svc-inventory").status is Status.UP


def test_degraded_dependency_does_not_consult_your_own_replicas():
    # Every replica of yours talks to the same slow thing, so replica count is irrelevant
    # here. It matters for node_lost, which is a different event.
    from orrery.sim.models import default_models

    models = default_models()
    w = _world()
    single = w.entity("svc-inventory")
    assert int(single.attrs.get("replicas", 1)) == 1
    eff = models[EntityKind.SERVICE].react(single, "dependency_degraded")
    assert eff.status is Status.DEGRADED
