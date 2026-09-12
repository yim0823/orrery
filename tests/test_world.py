from orrery.connectors import StaticYamlConnector
from orrery.resolve import Alias, Resolver
from orrery.world import World, blast_radius

FIX = "fixtures/demo-world.yaml"


def _world(resolver=None):
    w = World()
    w.ingest(StaticYamlConnector(FIX).discover(), resolver)
    return w


def test_ingest_counts():
    w = _world()
    assert len(w) == 17
    assert len(w.relations()) == 22


def test_blast_radius_site_kills_everything_hosted_there():
    w = _world()
    br = blast_radius(w, "site-a")
    # hosts in site-a, their nodes, dbs on those hosts, services on those nodes, and dependents
    for eid in ("host-a1", "host-a2", "node-a1", "node-a2", "db-orders", "db-stock",
                "svc-inventory", "svc-checkout", "svc-web"):
        assert eid in br.impacted, eid
    assert "host-b1" not in br.impacted
    assert br.impacted["host-a1"] == 1
    assert br.impacted["node-a1"] == 2


def test_blast_radius_db_reaches_web_through_checkout():
    w = _world()
    br = blast_radius(w, "db-orders")
    assert br.paths["svc-web"] == ["db-orders", "svc-checkout", "svc-web"]


def test_resolver_proposes_and_confirmed_alias_merges():
    d = StaticYamlConnector(FIX).discover()
    groups = Resolver().propose(d.entities)
    ids = {e.id for g in groups for e in g}
    assert {"svc-inventory", "svc-inventory-prod"} <= ids
    w = _world(Resolver([Alias("svc-inventory", "svc-inventory-prod", confirmed_by="human")]))
    assert "svc-inventory-prod" not in w.g
    assert len(w) == 16


def test_fork_is_independent():
    w = _world()
    f = w.fork()
    from orrery.schema import Status
    f.set_status("svc-web", Status.DOWN)
    assert w.entity("svc-web").status == Status.UP
