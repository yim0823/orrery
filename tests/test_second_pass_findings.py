"""The second adversarial pass, and what it found in the first pass's fixes.

Yesterday's fixes were re-reviewed. Most held. Four of them introduced new wrongness, and
three mutations survived in exactly the code the changelog had drawn attention to — which
is the lesson worth keeping: a fix is a new place for a defect to live, and the tests
written alongside a fix tend to check that it works rather than that it cannot be removed.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest
import yaml

from orrery.backtest import Incident, format_report, replay, run
from orrery.resolve import Resolver
from orrery.resolve.resolver import Alias, normalize
from orrery.schema import Entity, EntityKind, Relation, RelationKind, Status
from orrery.sim import Event, propagate
from orrery.world import World, audit, single_points_of_failure
from orrery.world.audit import _reach_sizes
from orrery.world.query import reach

ORRERY = [sys.executable, "-m", "orrery"]


def _world_from(text: str) -> World:
    from orrery.connectors import StaticYamlConnector

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    w = World()
    w.ingest(StaticYamlConnector(path).discover())
    pathlib.Path(path).unlink()
    return w


def _demo() -> World:
    from orrery.connectors import StaticYamlConnector

    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


# ---- regressions the first pass's fixes introduced ----


def test_a_degrading_host_still_reaches_the_services_behind_its_services():
    """The hop budget counted every edge, so a brown-out spent its whole allowance on
    service→node→host before reaching anything that consumes the service. On the demo
    world a degraded host reported the checkout service as healthy while it hard-depends
    on two things that had just degraded. Hops now count only calls."""
    w = _demo().fork()
    propagate(w, Event("host-a1", "degraded"))
    assert w.entity("svc-checkout").status is Status.DEGRADED


ORDER_WORLD = """
entities:
  - {{id: R, kind: service, name: r}}
{rest}
relations:
  - {{src: A, dst: R, kind: DEPENDS_ON, strength: soft}}
  - {{src: X, dst: R, kind: DEPENDS_ON}}
  - {{src: B, dst: A, kind: DEPENDS_ON}}
  - {{src: E, dst: B, kind: DEPENDS_ON}}
  - {{src: F, dst: E, kind: DEPENDS_ON}}
  - {{src: Z, dst: X, kind: DEPENDS_ON}}
  - {{src: E, dst: Z, kind: DEPENDS_ON, strength: soft}}
"""
_NAMES = ["A", "B", "E", "F", "X", "Z"]


@pytest.mark.parametrize(
    "order", [_NAMES, ["X", "Z", "A", "B", "E", "F"], ["F", "E", "B", "A", "Z", "X"]]
)
def test_the_degrade_budget_does_not_depend_on_the_order_entities_were_written(order):
    """A degradation reaching an entity by a shorter path arrives with hops to spare, and
    was being dropped because the status had not worsened. Which path arrived first came
    down to the order of lines in the file, so the same world in two orderings gave two
    answers — including between the library and the CLI, since saving reorders relations."""
    rest = "\n".join(f"  - {{id: {n}, kind: service, name: {n.lower()}}}" for n in order)
    w = _world_from(ORDER_WORLD.format(rest=rest)).fork()
    propagate(w, Event("R", "down"))
    assert w.entity("F").status is Status.DEGRADED


def test_a_member_already_dead_is_not_subtracted_from_quorum_twice():
    """The memoized count was taken from members currently standing and then decremented
    on every death the walk reached — including a member that was already down before the
    call, which had never been in the count. A cluster with quorum met was reported down,
    and took its healthy members with it."""
    w = _world_from(
        """
entities:
  - {id: seg, kind: network_segment, name: s}
  - {id: C, kind: cluster, name: c, attrs: {quorum: 2}}
  - {id: N1, kind: node, name: n1, status: down}
  - {id: N2, kind: node, name: n2}
  - {id: N3, kind: node, name: n3}
  - {id: N4, kind: node, name: n4}
relations:
  - {src: N1, dst: seg, kind: CONNECTS_TO}
  - {src: N2, dst: seg, kind: CONNECTS_TO}
  - {src: N1, dst: C, kind: MEMBER_OF}
  - {src: N2, dst: C, kind: MEMBER_OF}
  - {src: N3, dst: C, kind: MEMBER_OF}
  - {src: N4, dst: C, kind: MEMBER_OF}
"""
    ).fork()
    propagate(w, Event("seg", "down"))
    assert w.entity("C").status is not Status.DOWN  # N3 and N4 still standing
    assert w.entity("N3").status is Status.UP


LB_WORLD = """
entities:
  - {id: lb, kind: load_balancer, name: lb}
  - {id: b1, kind: host, name: b1}
  - {id: b2, kind: host, name: b2}
  - {id: db, kind: database, name: db}
relations:
  - {src: b1, dst: lb, kind: MEMBER_OF}
  - {src: b2, dst: lb, kind: MEMBER_OF}
  - {src: db, dst: b2, kind: RUNS_ON}
"""


def test_one_backend_dying_does_not_degrade_its_siblings():
    """`member_lost` reached the load balancer, which then emitted a degradation back down
    to every remaining pool member and onward to their dependents. A surviving backend is
    not slower because its sibling died."""
    w = _world_from(LB_WORLD).fork()
    propagate(w, Event("b1", "down"))
    assert w.entity("lb").status is Status.DEGRADED
    assert w.entity("b2").status is Status.UP
    assert w.entity("db").status is Status.UP


def test_a_load_balancer_dying_does_not_power_off_its_backends():
    """MEMBER_OF carries consequence downward for a cluster — its nodes stop being nodes.
    A VIP is different: losing it makes the backends unreachable by that path, not off."""
    w = _world_from(LB_WORLD).fork()
    propagate(w, Event("lb", "down"))
    assert w.entity("b2").status is Status.UP
    assert w.entity("db").status is Status.UP


def test_a_fork_does_not_grow_the_parents_provenance():
    """The copy taken on write duplicated `attrs` and left `provenance` shared, so merging
    a second source into an entity on a fork appended to the parent's list. The earlier fix
    covered exactly one mutable field."""
    parent = _demo()
    before = len(parent.entity("host-a1").provenance)
    child = parent.fork()
    child.add_entity(Entity(id="host-a1", kind=EntityKind.HOST, name="a1"))
    assert len(parent.entity("host-a1").provenance) == before


# ---- mutations that survived the first round of killers ----


def test_a_degraded_node_is_still_somewhere_to_run():
    """Mutation: `_ALIVE` narrowed to `(UP,)`. Slow is not gone — a service whose other
    node is merely degraded has not run out of places."""
    w = _world_from(
        """
entities:
  - {id: svc, kind: service, name: svc}
  - {id: n1, kind: node, name: n1}
  - {id: n2, kind: node, name: n2, status: degraded}
relations:
  - {src: svc, dst: n1, kind: RUNS_ON}
  - {src: svc, dst: n2, kind: RUNS_ON}
"""
    ).fork()
    propagate(w, Event("n1", "down"))
    assert w.entity("svc").status is Status.DEGRADED


def test_ranking_handles_a_cycle():
    """Mutation: skipping the SCC condensation. No ranking test had a cycle, so a DP that
    cannot terminate on one passed. Mutual dependencies are common and this must not hang
    or miscount."""
    w = _world_from(
        """
entities:
  - {id: a, kind: service, name: a}
  - {id: b, kind: service, name: b}
  - {id: c, kind: service, name: c}
relations:
  - {src: a, dst: b, kind: DEPENDS_ON}
  - {src: b, dst: a, kind: DEPENDS_ON}
  - {src: c, dst: a, kind: DEPENDS_ON}
"""
    )
    sizes = _reach_sizes(w)
    assert sizes["a"] == len(reach(w, "a"))
    assert sizes["b"] == len(reach(w, "b"))
    assert "c" in reach(w, "a")  # c depends on a, through the cycle


def test_severity_accuracy_is_not_the_padded_number():
    """Mutation: `exact_on_impacted` returning `exact_rate`. The old test only asserted it
    was not None, so returning the padded number passed. Here the two must differ."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        World.load("fixtures/incidents/world.yaml").save(root / "world.yaml")
        (root / "INC-X.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "INC-X",
                    "title": "padded",
                    "world": "world.yaml",
                    "trigger": "db-stock",
                    "event": "down",
                    # one thing broke and was over-called; two were fine and predicted fine
                    "observed": {
                        "svc-inventory": "degraded",
                        "db-orders": "up",
                        "host-b1": "up",
                    },
                }
            )
        )
        report = run(Incident.load_dir(root))
    assert report.exact_on_impacted() < report.exact_rate()


# ---- errors a person causes, checked outside CliRunner ----
#
# `CliRunner` captures exceptions into `result.exception` rather than printing them, so
# asserting "Traceback" is absent from its output is always true. These run the real
# command in a real process, which is the only way to see what a user sees.


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*ORRERY, *args], capture_output=True, text=True, check=False)


def test_a_directory_where_a_file_belongs_says_so():
    out = _run("ingest", "fixtures")
    assert out.returncode != 0
    assert "Traceback" not in out.stderr
    assert "is a directory" in out.stdout + out.stderr


def test_malformed_yaml_says_so():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("entities: [unclosed\n")
        path = f.name
    out = _run("ingest", path)
    pathlib.Path(path).unlink()
    assert out.returncode != 0
    assert "Traceback" not in out.stderr


def test_a_relation_to_a_missing_entity_says_which_one():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(
            "entities:\n  - {id: a, kind: host, name: a}\n"
            "relations:\n  - {src: a, dst: ghost, kind: HOSTED_IN}\n"
        )
        path = f.name
    out = _run("ingest", path)
    pathlib.Path(path).unlink()
    assert out.returncode != 0
    assert "Traceback" not in out.stderr
    assert "ghost" in out.stdout + out.stderr


def test_a_typo_in_an_incident_trigger_says_so():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        World.load("fixtures/incidents/world.yaml").save(root / "world.yaml")
        (root / "INC-T.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "INC-T",
                    "title": "typo",
                    "world": "world.yaml",
                    "trigger": "db-stok",
                    "event": "down",
                    "observed": {"svc-inventory": "down"},
                }
            )
        )
        out = _run("backtest", str(root))
    assert out.returncode != 0
    assert "Traceback" not in out.stderr


# ---- a typo in a field name must not be read as a default ----


def test_a_misspelled_field_is_refused():
    """`strenght: soft` used to ingest as hard, silently. A field-name typo is the one
    mistake that produces a confident wrong answer rather than an error."""
    with pytest.raises(Exception, match="strenght|extra"):
        Relation(src="a", dst="b", kind=RelationKind.DEPENDS_ON, strenght="soft")


def test_a_misspelled_entity_field_is_refused():
    with pytest.raises(Exception, match="staus|extra"):
        Entity(id="a", kind=EntityKind.HOST, name="a", staus="down")


# ---- resolution ----


def test_environment_suffixes_strip_in_any_order():
    """Single-pass stripping in a fixed order left `billing-svc-prod` and
    `billing-prod-svc` as different strings, so they were never proposed as the same
    thing — the exact case entity resolution exists for."""
    assert normalize("billing-svc-prod") == normalize("billing-prod-svc") == "billing"


def test_an_alias_cycle_is_refused_rather_than_resolved_to_itself():
    r = Resolver([Alias(canonical="b", alias="a", confirmed_by="someone")])
    with pytest.raises(ValueError, match="cycle"):
        r.confirm(Alias(canonical="a", alias="b", confirmed_by="someone else"))


# ---- what a scenario declares is now enforced ----


def test_acting_outside_an_allowed_namespace_is_a_boundary_violation():
    """The briefing told the agent which namespaces it could act in and nothing checked.
    Telling someone a rule and not enforcing it makes the sentence decoration."""
    from orrery.scenarios import run_scenario
    from orrery.scenarios.schema import Answer, Boundary, Injection, Scenario

    class Tool:
        name = "kubectl"
        readonly = False

        def call(self, action, **kw):
            return "ok"

    class Agent:
        def run(self, tools, briefing):
            tools["kubectl"].call("rollback", namespace="payments")
            next(iter(tools.values()))._session.submit_root_cause("db-stock")

    sc = Scenario(
        id="S",
        title="t",
        world="unused",
        injections=[Injection(entity_id="db-stock", event="down")],
        boundary=Boundary(allowed_namespaces=["shop"]),
        answer=Answer(root_cause="db-stock", safe_action="rollback"),
    )
    result = run_scenario(sc, Agent(), {"kubectl": Tool()}, world=_demo())
    assert "kubectl.rollback" in result.boundary_violations


def test_a_readonly_tool_counts_as_evidence_whatever_its_actions_are_called():
    """`ToolSurface.readonly` was copied and never read, so a tool that declares itself
    read-only still had to follow a naming convention to count as looking at something."""
    from orrery.scenarios import run_scenario
    from orrery.scenarios.schema import Answer, Injection, Scenario

    class Metrics:
        name = "metrics"
        readonly = True

        def call(self, action, **kw):
            return "ok"

    class Agent:
        def run(self, tools, briefing):
            tools["metrics"].call("series")  # no read_ prefix
            next(iter(tools.values()))._session.submit_root_cause("db-stock")

    sc = Scenario(
        id="S",
        title="t",
        world="unused",
        injections=[Injection(entity_id="db-stock", event="down")],
        answer=Answer(root_cause="db-stock", safe_action="series"),
    )
    result = run_scenario(sc, Agent(), {"metrics": Metrics()}, world=_demo())
    assert result.trace.actions[0].evidence_before


# ---- backtest honesty, continued ----


def test_the_unverified_count_only_counts_what_the_engine_changed():
    """It compared against UP, so a snapshot legitimately recording something as already
    degraded was reported as a prediction nobody checked — inflating the warning with
    entities the simulation never touched."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        w = World.load("fixtures/incidents/world.yaml")
        w.set_status("svc-web", Status.DEGRADED)  # already unhealthy before anything ran
        w.save(root / "world.yaml")
        (root / "INC-U.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "INC-U",
                    "title": "pre-existing damage",
                    "world": "world.yaml",
                    "trigger": "db-orders",
                    "event": "down",
                    "observed": {"svc-checkout": "down"},
                }
            )
        )
        cmp = replay(Incident.load(root / "INC-U.yaml"))
    assert "svc-web" not in cmp.unverified_predictions


def test_the_json_report_carries_the_honesty_numbers():
    """They were text-only, so anything reading the machine output saw precision without
    the warning that makes it an upper bound."""
    out = subprocess.run(
        [*ORRERY, "backtest", "fixtures/incidents", "--json-out"],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(out.stdout)
    assert "unverified_predictions" in payload
    assert "exact_on_impacted" in payload


def test_the_report_still_says_what_it_cannot_see():
    text = format_report(run(Incident.load_dir("fixtures/incidents")))
    assert "nobody checked" in text
    assert "on breaks" in text


# ---- ranking at scale stays correct ----


def test_the_frontier_optimization_did_not_change_any_answer():
    """Bitsets are now freed once their predecessors have folded them in, which halved
    peak memory. Halving memory is not worth a different answer."""
    w = _demo()
    for risk in single_points_of_failure(w, limit=50):
        assert risk.reach == len(reach(w, risk.entity_id))


# ---- virtualization makes redundancy easy to fake ----


VIRT = """
entities:
  - {id: site, kind: site, name: site}
  - {id: phys-1, kind: host, name: phys1}
  - {id: phys-2, kind: host, name: phys2}
  - {id: vm-1, kind: vm, name: vm1}
  - {id: vm-2, kind: vm, name: vm2}
  - {id: vm-3, kind: vm, name: vm3}
  - {id: n1, kind: node, name: n1}
  - {id: n2, kind: node, name: n2}
  - {id: n3, kind: node, name: n3}
  - {id: svc, kind: service, name: svc}
relations:
  - {src: phys-1, dst: site, kind: HOSTED_IN}
  - {src: phys-2, dst: site, kind: HOSTED_IN}
  - {src: vm-1, dst: phys-1, kind: RUNS_ON}
  - {src: vm-2, dst: phys-1, kind: RUNS_ON}
  - {src: vm-3, dst: PHYS, kind: RUNS_ON}
  - {src: n1, dst: vm-1, kind: RUNS_ON}
  - {src: n2, dst: vm-2, kind: RUNS_ON}
  - {src: n3, dst: vm-3, kind: RUNS_ON}
  - {src: svc, dst: n1, kind: RUNS_ON}
  - {src: svc, dst: n2, kind: RUNS_ON}
  - {src: svc, dst: n3, kind: RUNS_ON}
"""


def test_three_nodes_on_one_physical_server_is_not_redundancy():
    """A Kubernetes cluster cannot see what it is standing on. Three nodes that are three
    virtual machines on one physical server look like three places to fail and are one,
    and the map is the only place that question can be asked at all."""
    w = _world_from(VIRT.replace("PHYS", "phys-1"))
    flagged = {f.check for f in audit(w).findings if f.entity_id == "svc"}
    assert "redundancy on one machine" in flagged


def test_the_same_service_spread_across_two_machines_is_not_flagged():
    w = _world_from(VIRT.replace("PHYS", "phys-2"))
    flagged = {f.check for f in audit(w).findings if f.entity_id == "svc"}
    assert "redundancy on one machine" not in flagged


def test_sharing_a_site_is_not_reported_as_concentration():
    """Everything in one datacentre is a fact about the estate, not a defect. Reporting it
    would put a finding on every service and teach people to skip the report."""
    w = _world_from(VIRT.replace("PHYS", "phys-2"))
    assert all(
        "site" not in f.detail for f in audit(w).findings if f.check == "redundancy on one machine"
    )


def test_losing_the_shared_machine_takes_the_whole_service():
    w = _world_from(VIRT.replace("PHYS", "phys-1")).fork()
    propagate(w, Event("phys-1", "down"))
    assert w.entity("svc").status is Status.DOWN


def test_losing_one_of_two_machines_only_degrades():
    w = _world_from(VIRT.replace("PHYS", "phys-2")).fork()
    propagate(w, Event("phys-2", "down"))
    assert w.entity("svc").status is Status.DEGRADED
