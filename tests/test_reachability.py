"""A service nobody can reach is broken, however healthy its process is.

This came out of a real map rather than a design session. A game's load balancers were
loaded into the world, the appliance carrying six of its VIPs was killed, and `simulate`
answered "nothing happens" — correctly, under the model as it stood: the backends really
do keep running. The answer was useless anyway, because no player could connect.

Reachability turned out to behave exactly like placement, so it is counted by the same
code: several ways in are redundancy, one is a single point of failure, none is an outage.
"""
from __future__ import annotations

import pytest

from orrery.connectors import StaticYamlConnector
from orrery.schema import Status
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius


def _world(text: str, tmp_path) -> World:
    p = tmp_path / "w.yaml"
    p.write_text(text, encoding="utf-8")
    w = World()
    w.ingest(StaticYamlConnector(str(p)).discover())
    return w


TWO_WAYS_IN = """
entities:
  - {id: site, kind: site, name: site}
  - {id: host-1, kind: host, name: h1}
  - {id: appliance-a, kind: load_balancer, name: lb-a}
  - {id: appliance-b, kind: load_balancer, name: lb-b}
  - {id: vip-a, kind: load_balancer, name: vip-a}
  - {id: vip-b, kind: load_balancer, name: vip-b}
  - {id: svc, kind: service, name: svc}
relations:
  - {src: host-1, dst: site, kind: HOSTED_IN}
  - {src: appliance-a, dst: site, kind: HOSTED_IN}
  - {src: appliance-b, dst: site, kind: HOSTED_IN}
  - {src: vip-a, dst: appliance-a, kind: RUNS_ON}
  - {src: vip-b, dst: appliance-b, kind: RUNS_ON}
  - {src: svc, dst: host-1, kind: RUNS_ON}
  - {src: svc, dst: vip-a, kind: REACHED_VIA}
  - {src: svc, dst: vip-b, kind: REACHED_VIA}
"""


def test_losing_one_way_in_degrades(tmp_path):
    w = _world(TWO_WAYS_IN, tmp_path).fork()
    propagate(w, Event("appliance-a", "down"))
    assert w.entity("svc").status is Status.DEGRADED


def test_losing_every_way_in_is_an_outage_even_though_it_is_still_running(tmp_path):
    """The process is fine. Nobody can reach it. The honest answer is down, and the note
    has to say which of the two it is, because the fix is not the same."""
    w = _world(TWO_WAYS_IN, tmp_path).fork()
    effects = propagate(w, Event("appliance-a", "down"))
    effects += propagate(w, Event("appliance-b", "down"))

    assert w.entity("svc").status is Status.DOWN
    assert w.entity("host-1").status is Status.UP  # it never stopped running


def test_the_appliance_is_in_the_blast_radius_of_the_service(tmp_path):
    """`blast` saw the appliance before this existed — it walked the edge and stopped at
    the VIP. What it could not do was explain why that mattered."""
    w = _world(TWO_WAYS_IN, tmp_path)
    assert "svc" in blast_radius(w, "appliance-a").impacted


def test_a_service_with_one_way_in_goes_down_with_it(tmp_path):
    one = TWO_WAYS_IN.replace("  - {src: svc, dst: vip-b, kind: REACHED_VIA}\n", "")
    w = _world(one, tmp_path).fork()
    propagate(w, Event("appliance-a", "down"))
    assert w.entity("svc").status is Status.DOWN


@pytest.mark.parametrize("first,second", [("appliance-a", "appliance-b"), ("appliance-b", "appliance-a")])
def test_the_answer_does_not_depend_on_which_way_in_died_first(tmp_path, first, second):
    w = _world(TWO_WAYS_IN, tmp_path).fork()
    propagate(w, Event(first, "down"))
    propagate(w, Event(second, "down"))
    assert w.entity("svc").status is Status.DOWN


def test_running_out_of_places_and_running_out_of_paths_are_told_apart(tmp_path):
    """Both end at the same status and they are not the same problem: one is answered by
    another machine, the other by another route."""
    w = _world(TWO_WAYS_IN, tmp_path).fork()
    effects = propagate(w, Event("appliance-a", "down"))
    note = next(e.note for e in effects if e.entity_id == "svc")
    assert "ways in" in note and "place" not in note


# ── quorum membership read upward ─────────────────────────────────────────────

def _consensus_world():
    """Three nodes voting in an etcd-like group (quorum 2), an API that needs it."""
    from orrery.schema.entities import Entity, Relation
    from orrery.world import World
    w = World()
    for n in ("n1", "n2", "n3"):
        w.add_entity(Entity(id=n, kind="host", name=n))
    w.add_entity(Entity(id="etcd", kind="service", name="etcd", attrs={"quorum": 2}))
    for n in ("n1", "n2", "n3"):
        w.add_relation(Relation(src=n, dst="etcd", kind="MEMBER_OF"))
    w.add_entity(Entity(id="api", kind="service", name="api"))
    w.add_relation(Relation(src="api", dst="etcd", kind="DEPENDS_ON", strength="hard"))
    w.add_entity(Entity(id="pool", kind="load_balancer", name="pool"))  # no quorum: thinner, not gone
    w.add_relation(Relation(src="n1", dst="pool", kind="MEMBER_OF"))
    return w


def test_a_quorum_member_reaches_its_group_in_spof_blast_and_reach():
    from orrery.world.audit import _reach_sizes
    from orrery.world.query import blast_radius, reach
    w = _consensus_world()
    assert _reach_sizes(w)["n1"] == 2  # etcd and the api on it
    br = blast_radius(w, "n1")
    assert br.impacted == {"etcd": 1, "api": 2} and br.paths["api"] == ["n1", "etcd", "api"]
    assert reach(w, "n1") == {"etcd", "api"}


def test_a_group_without_quorum_is_not_reached_by_its_member():
    from orrery.world.query import blast_radius, reach
    w = _consensus_world()
    assert "pool" not in blast_radius(w, "n1").impacted and "pool" not in reach(w, "n1")


def test_structural_reach_agrees_with_simulate_on_what_a_quorum_loss_takes():
    from orrery.sim.propagate import Event, propagate
    from orrery.world.query import reach
    w = _consensus_world()
    sim = w.fork()
    for n in ("n1", "n2"):
        propagate(sim, Event(n, "down"))
    stopped = {e.id for e in sim.entities() if e.status.value == "down"} - {"n1", "n2"}
    assert stopped <= reach(w, "n1") | reach(w, "n2")


def test_reach_does_not_send_a_load_balancers_death_down_to_its_pool():
    """`reach` skipped the kind check that `blast_radius` and `spof` make — a load balancer
    dying leaves its backends running."""
    from orrery.world.query import blast_radius, reach
    w = _consensus_world()
    assert reach(w, "pool") == set(blast_radius(w, "pool").impacted) == set()


def _demo_world():
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def test_blast_keeps_the_quorum_tail_apart_from_what_goes_with_the_root():
    """A cluster-kind quorum group carries its death down to its members, so without the split
    one node's host would list its two peers as victims — they are the redundancy."""
    from orrery.world.query import reach
    w = _demo_world()
    br = blast_radius(w, "host-a1")
    assert set(br.through_quorum) == {"etcd", "node-a2", "node-b1"}
    assert set(br.through_quorum.values()) == {"etcd"}
    assert set(br.impacted) == reach(w, "host-a1")  # the set is still the whole structural reach
    assert "node-a2" not in {i for i in br.impacted if i not in br.through_quorum}


def test_a_site_that_takes_two_voters_reaches_the_third_member_as_simulate_does():
    from orrery.world.query import reach
    w = _demo_world()
    sim = w.fork()
    propagate(sim, Event("site-a", "down"))
    assert sim.entity("node-b1").status is Status.DOWN
    assert "node-b1" in reach(w, "site-a") and "node-b1" in blast_radius(w, "site-a").impacted


def test_spof_ranks_by_what_goes_alone_and_shows_the_quorum_tail_as_plus():
    from orrery.schema import EntityKind
    from orrery.world import single_points_of_failure
    w = _demo_world()
    top = single_points_of_failure(w, limit=5, exclude_kinds=(EntityKind.SITE,))
    ids = [r.entity_id for r in top]
    assert ids.index("host-a3") == 3  # the hypervisor under two "independent" nodes stays fourth
    host_a1 = next(r for r in top if r.entity_id == "host-a1")
    assert (host_a1.reach, host_a1.through_quorum) == (5, 3)
    assert top[0].to_dict()["through_quorum"] == top[0].through_quorum


def test_check_validates_quorum_on_any_kind_not_only_clusters():
    from orrery.schema.entities import Entity, Relation
    from orrery.world.audit import audit
    w = World()
    w.add_entity(Entity(id="p", kind="network_segment", name="p", attrs={"quorum": 3}))
    w.add_entity(Entity(id="q", kind="service", name="q", attrs={"quorum": "one"}))
    for s_ in ("a", "b"):
        w.add_entity(Entity(id=s_, kind="network_segment", name=s_))
        w.add_relation(Relation(src=s_, dst="p", kind="MEMBER_OF"))
    w.add_relation(Relation(src="a", dst="q", kind="MEMBER_OF"))
    kinds = {(f.check, f.entity_id) for f in audit(w).findings}
    assert ("quorum unreachable", "p") in kinds and ("quorum is not a number", "q") in kinds


def test_a_voter_that_carries_nothing_itself_is_still_ranked_for_its_quorum_tail():
    from orrery.world import single_points_of_failure
    w = _consensus_world()
    n2 = next(r for r in single_points_of_failure(w, limit=50) if r.entity_id == "n2")
    assert (n2.reach, n2.through_quorum) == (0, 2)


def test_blast_json_keeps_impacted_as_what_goes_alone_and_lists_the_tail_apart(tmp_path):
    import json
    from typer.testing import CliRunner
    from orrery.cli import app
    out = CliRunner().invoke(app, ["blast", "host-a1", "--world", "fixtures/demo-world.yaml", "--json-out"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert "node-a2" not in {i["id"] for i in payload["impacted"]}
    assert {i["id"] for i in payload["through_quorum"]} == {"etcd", "node-a2", "node-b1"}
