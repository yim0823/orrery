from orrery.connectors import StaticYamlConnector
from orrery.schema import Status
from orrery.sim import Clock, Event, propagate
from orrery.world import World


def _world():
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def test_telling_it_a_database_is_down_means_down():
    # It used to answer "degraded" here, because an attribute claimed a replica existed.
    # You say the database is down; the only honest answer is down.
    w = _world()
    propagate(w, Event("db-orders", "down"))
    assert w.entity("db-orders").status == Status.DOWN


def test_losing_one_of_a_databases_two_hosts_is_a_failover():
    # host-b1 carries the orders replica and nothing else that checkout needs, so this
    # isolates the failover from the "lost my only node" case that host-a2 would mix in.
    w = _world()
    propagate(w, Event("host-b1", "down"))
    assert w.entity("db-orders").status == Status.DEGRADED  # still running on host-a2
    assert w.entity("svc-checkout").status == Status.DEGRADED  # reads-only database


def test_db_without_replica_kills_single_replica_service():
    w = _world()
    propagate(w, Event("db-stock", "down"))
    assert w.entity("db-stock").status == Status.DOWN
    assert w.entity("svc-inventory").status == Status.DOWN
    assert w.entity("svc-checkout").status == Status.DOWN  # hard dep down
    # The storefront depends on checkout softly: it keeps serving pages, you just
    # cannot buy. Before soft dependencies existed this asserted DOWN.
    assert w.entity("svc-web").status == Status.DEGRADED


def test_losing_one_node_degrades_multi_replica_service():
    w = _world()
    propagate(w, Event("node-b1", "down"))
    assert w.entity("svc-web").status == Status.DEGRADED


def test_clock_runs_scheduled_events():
    fired = []
    c = Clock()
    c.at(5, lambda: fired.append(c.now))
    c.run(until=10)
    assert fired == [5]
