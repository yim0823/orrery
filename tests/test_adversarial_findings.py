"""Every defect an adversarial review found, kept fixed.

Four reviewers were pointed at this engine and told to break it. Two independently found
the same top defect. These are their reproductions, turned into tests, because a fix
without one is a fix that comes back.

The pattern worth noticing: the suite had 142 passing tests when every one of these was
live. Test count is not evidence.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest
from typer.testing import CliRunner

from orrery.cli import app
from orrery.connectors import StaticYamlConnector
from orrery.schema import Entity, EntityKind, Provenance, Relation, RelationKind, Status
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius

runner = CliRunner()


def _world_from(yaml_text: str) -> World:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_text)
        path = f.name
    w = World()
    w.ingest(StaticYamlConnector(path).discover())
    pathlib.Path(path).unlink()
    return w


def _demo() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


# ---- the defect two reviewers found independently ----


def test_losing_one_of_many_nodes_does_not_kill_a_service():
    """It used to. `_translate` counted survivors, then `ServiceModel` threw the count
    away and re-derived redundancy from `attrs["replicas"]`, defaulting to 1. A service
    on forty-nine healthy nodes was reported down when one rebooted — and the README and
    CHANGELOG both claimed this had been fixed."""
    entities = "\n".join(f"  - {{id: n{i}, kind: node, name: n{i}}}" for i in range(49))
    runs = "\n".join(f"  - {{src: svc, dst: n{i}, kind: RUNS_ON}}" for i in range(49))
    w = _world_from(
        f"entities:\n  - {{id: svc, kind: service, name: svc}}\n{entities}\n"
        f"relations:\n{runs}\n"
    ).fork()
    propagate(w, Event("n0", "down"))
    assert w.entity("svc").status is Status.DEGRADED


def test_a_declared_replica_count_cannot_rescue_a_service_with_one_place_to_run():
    """The mirror image, and the reason the count is the graph's business: three replicas
    pinned to one node is one place to lose."""
    w = _world_from(
        "entities:\n"
        "  - {id: svc, kind: service, name: svc, attrs: {replicas: 3}}\n"
        "  - {id: n1, kind: node, name: n1}\n"
        "relations:\n"
        "  - {src: svc, dst: n1, kind: RUNS_ON}\n"
    ).fork()
    propagate(w, Event("n1", "down"))
    assert w.entity("svc").status is Status.DOWN


# ---- the network layer was invisible ----


NET = """
entities:
  - {id: vlan, kind: network_segment, name: vlan}
  - {id: h1, kind: host, name: h1}
  - {id: db, kind: database, name: db}
relations:
  - {src: h1, dst: vlan, kind: CONNECTS_TO}
  - {src: db, dst: h1, kind: RUNS_ON}
"""


def test_a_network_segment_failing_reaches_what_is_attached_to_it():
    """CONNECTS_TO was excluded from impact on the reasoning that communication is
    symmetric. A host's sole attachment to a segment is not symmetric, and the exclusion
    made an entire class of outage — a VLAN or top-of-rack failure — compute as affecting
    nothing at all."""
    w = _world_from(NET).fork()
    propagate(w, Event("vlan", "down"))
    assert w.entity("h1").status is Status.DOWN
    assert w.entity("db").status is Status.DOWN


def test_blast_and_simulate_agree_about_the_network():
    """They disagreed, because the edge list was defined twice. Two answers from one tool
    that contradict each other cost more trust than either one being wrong."""
    w = _world_from(NET)
    assert set(blast_radius(w, "vlan").impacted) == {"h1", "db"}


# ---- redundancy asserted versus redundancy placed ----


def test_telling_the_engine_a_database_is_down_means_down():
    """It answered "degraded" — because `attrs["replica"]` claimed a replica existed
    somewhere the graph did not record. The engine's own audit flags that shape as
    redundancy on paper only; believing it here had the engine contradict itself, in the
    direction that hides an outage."""
    w = _demo().fork()
    propagate(w, Event("db-orders", "down"))
    assert w.entity("db-orders").status is Status.DOWN


def test_a_database_on_a_degraded_host_notices():
    """`DatabaseModel` returned no status at all for degradation, so a database on a host
    with a dying disk — the most common slow incident there is — was reported healthy."""
    w = _demo().fork()
    propagate(w, Event("host-a2", "degraded"))
    assert w.entity("db-orders").status is Status.DEGRADED


# ---- survivors, counted honestly ----


def test_an_unreachable_node_is_not_counted_as_a_survivor():
    """Survivors were "not DOWN", which let UNKNOWN count. A node the inventory could not
    reach during the incident you are simulating is not evidence of a place to run."""
    w = _world_from(
        "entities:\n"
        "  - {id: svc, kind: service, name: svc}\n"
        "  - {id: n1, kind: node, name: n1}\n"
        "  - {id: n2, kind: node, name: n2, status: unknown}\n"
        "relations:\n"
        "  - {src: svc, dst: n1, kind: RUNS_ON}\n"
        "  - {src: svc, dst: n2, kind: RUNS_ON}\n"
    ).fork()
    propagate(w, Event("n1", "down"))
    assert w.entity("svc").status is Status.DOWN


# ---- degradation had no bound ----


def test_degradation_does_not_travel_forever():
    """Every soft edge after the first was a no-op, so one shared optional sink — a
    metrics endpoint, a feature-flag service — painted every service that transitively
    touched it. A report where everything is yellow is a report nobody reads."""
    n = 9
    ents = "\n".join(f"  - {{id: s{i}, kind: service, name: s{i}}}" for i in range(n))
    rels = "\n".join(
        f"  - {{src: s{i}, dst: s{i + 1}, kind: DEPENDS_ON, strength: soft}}"
        for i in range(n - 1)
    )
    w = _world_from(
        f"entities:\n  - {{id: sink, kind: external, name: sink}}\n{ents}\n"
        f"relations:\n  - {{src: s{n - 1}, dst: sink, kind: DEPENDS_ON, strength: soft}}\n{rels}\n"
    ).fork()
    propagate(w, Event("sink", "down"))
    touched = [e.id for e in w.entities() if e.status is not Status.UP and e.id != "sink"]
    assert len(touched) < n, f"degradation reached {len(touched)} of {n} services"


def test_an_outage_still_travels_the_whole_way():
    """The bound is on degradation only. A hard failure has no horizon."""
    n = 9
    ents = "\n".join(f"  - {{id: s{i}, kind: service, name: s{i}}}" for i in range(n))
    rels = "\n".join(f"  - {{src: s{i}, dst: s{i + 1}, kind: DEPENDS_ON}}" for i in range(n - 1))
    w = _world_from(f"entities:\n{ents}\nrelations:\n{rels}\n").fork()
    propagate(w, Event(f"s{n - 1}", "down"))
    assert all(w.entity(f"s{i}").status is Status.DOWN for i in range(n))


# ---- a load balancer is not a cluster ----


def test_losing_a_backend_thins_the_pool_rather_than_killing_the_vip():
    w = _world_from(
        "entities:\n"
        "  - {id: lb, kind: load_balancer, name: lb}\n"
        "  - {id: svc, kind: service, name: svc}\n"
        "  - {id: n1, kind: node, name: n1}\n"
        "relations:\n"
        "  - {src: svc, dst: n1, kind: RUNS_ON}\n"
        "  - {src: svc, dst: lb, kind: MEMBER_OF}\n"
    ).fork()
    propagate(w, Event("n1", "down"))
    assert w.entity("svc").status is Status.DOWN
    assert w.entity("lb").status is Status.DEGRADED  # not down: the VIP still answers


# ---- the file format reads what it writes ----


def test_a_saved_world_can_be_ingested_again():
    """It crashed with `Entity() got multiple values for keyword argument 'provenance'`.
    Editing a snapshot and re-ingesting it is the fastest way to try a change, and it
    was the one thing the format could not do."""
    with tempfile.TemporaryDirectory() as d:
        first = pathlib.Path(d) / "a.yaml"
        _demo().save(first)
        again = World()
        again.ingest(StaticYamlConnector(first).discover())
    assert len(again) == len(_demo())


def test_provenance_survives_a_round_trip():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "a.yaml"
        _demo().save(path)
        again = World()
        again.ingest(StaticYamlConnector(path).discover())
    sources = {p.source for p in again.entity("host-a1").provenance}
    assert "static_yaml" in sources
    assert len(again.entity("host-a1").provenance) >= 2  # original plus the re-read


# ---- errors a person can cause get a sentence ----


@pytest.mark.parametrize(
    "args,expected",
    [
        (["blast", "hots-a1"], "Did you mean"),
        (["simulate", "hots-a1"], "Did you mean"),
        (["simulate", "host-a1", "--event", "exploded"], "unknown event"),
        (["spof", "--kind", "hots"], "unknown kind"),
        (["ingest", "definitely-not-here.yaml"], "no such file"),
    ],
)
def test_user_errors_produce_a_message_not_a_traceback(args, expected):
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert expected in result.output
    assert "Traceback" not in result.output


def test_merging_two_things_under_one_id_is_recorded():
    """`add_entity` merges silently. Merging is right when two sources describe one thing
    and wrong when one source describes two, and the two are indistinguishable from
    inside — so the collision is recorded and reported rather than swallowed."""
    w = _world_from(
        "entities:\n"
        "  - {id: h1, kind: host, name: first}\n"
        "  - {id: h1, kind: host, name: second}\n"
    )
    assert len(w) == 1
    assert w.collisions == [("h1", "first", "second")]


# ---- the backtest could not see its own blind spot ----


def test_predictions_nobody_checked_are_counted_and_reported():
    """Precision skipped entities the record said nothing about, so over-prediction was
    free: an engine painting half the estate red is never wrong about the half nobody
    looked at. The count is now reported alongside precision."""
    from orrery.backtest import Incident, run

    report = run(Incident.load_dir("fixtures/incidents"))
    assert report.unverified > 0
    from orrery.backtest import format_report

    assert "nobody checked" in format_report(report)


def test_a_typo_in_an_observed_id_is_refused():
    """It raised a bare KeyError from deep inside. Worse, a typo silently removes a
    judgement, which flatters the score."""
    from orrery.backtest import Incident, replay

    inc = Incident.load("fixtures/incidents/INC-0001.yaml")
    inc.observed["svc-inventroy"] = Status.DOWN
    with pytest.raises(KeyError, match="missing from"):
        replay(inc)


def test_severity_accuracy_is_reported_separately_from_padding():
    """`exact` counts `correct_up`, so a record listing forty healthy entities lifts the
    score without the engine getting anything hard right."""
    from orrery.backtest import Incident, run

    report = run(Incident.load_dir("fixtures/incidents"))
    assert report.exact_on_impacted() is not None
    assert "on breaks" in __import__(
        "orrery.backtest", fromlist=["format_report"]
    ).format_report(report)


def test_replaying_every_incident_against_one_snapshot_is_flagged():
    """An edge learned from a postmortem and then added to the map being graded turns the
    exercise into hindsight. Nothing can detect that mechanically, so it is said out loud."""
    from orrery.backtest import Incident, format_report, run

    text = format_report(run(Incident.load_dir("fixtures/incidents")))
    assert "hindsight" in text


# ---- the taxonomy had holes that forced ugly modelling ----


@pytest.mark.parametrize("kind", ["storage", "queue", "dns", "certificate", "cdn", "job"])
def test_kinds_that_fail_differently_exist(kind):
    """Each of these was previously a `host` or a `database`, inheriting failure semantics
    that were wrong for it."""
    e = Entity(
        id=f"x-{kind}",
        kind=EntityKind(kind),
        name=kind,
        provenance=[Provenance(source="t", source_id="1")],
    )
    assert e.kind.value == kind


def test_a_relation_to_a_missing_entity_is_refused():
    w = World()
    w.add_entity(Entity(id="a", kind=EntityKind.HOST, name="a"))
    with pytest.raises(KeyError, match="unknown entity"):
        w.add_relation(Relation(src="a", dst="ghost", kind=RelationKind.HOSTED_IN))
