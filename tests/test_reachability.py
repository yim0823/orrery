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
