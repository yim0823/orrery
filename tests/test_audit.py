"""Map quality and risk ranking.

These two answer the questions a team has on day one, before there is any incident
history to calibrate against, so getting them wrong is expensive: a quality report that
flags nothing teaches people the map is fine, and a risk list that ranks by the wrong
thing sends them to reinforce something that was never the problem.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from orrery.cli import app
from orrery.connectors import StaticYamlConnector
from orrery.schema import (
    Entity,
    EntityKind,
    Provenance,
    Relation,
    RelationKind,
)
from orrery.world import World, audit, single_points_of_failure

runner = CliRunner()


def _demo() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def _checks(world: World) -> set[str]:
    return {f.check for f in audit(world).findings}


def _for(world: World, entity_id: str) -> set[str]:
    return {f.check for f in audit(world).findings if f.entity_id == entity_id}


# ---- map quality ----


def test_it_counts_what_it_looked_at():
    # Asserting against a second copy of the same call proves nothing, so the numbers are
    # pinned to the fixture. They change when the demo world does, which is the point.
    a = audit(_demo())
    assert a.entities == 18
    assert a.relations == 25


def test_an_isolated_entity_is_flagged():
    # The demo carries an unresolved alias that nothing points at — exactly the shape a
    # failed join leaves behind.
    assert "isolated" in _for(_demo(), "svc-inventory-prod")


def test_redundancy_that_exists_only_on_paper_is_flagged():
    # replicas=2 with one place to run is not redundancy, and this is the cheapest
    # moment anyone will ever have to notice that.
    assert "redundancy on paper only" in _for(_demo(), "svc-checkout")


def test_a_service_with_nowhere_to_run_is_flagged():
    w = _demo()
    w.add_entity(
        Entity(
            id="svc-orphan", kind=EntityKind.SERVICE, name="orphan",
            provenance=[Provenance(source="test", source_id="x")],
        )
    )
    w.add_relation(Relation(src="svc-orphan", dst="db-orders", kind=RelationKind.DEPENDS_ON))
    assert "no recorded placement" in _for(w, "svc-orphan")


def test_something_that_depends_but_is_hosted_nowhere_is_flagged():
    w = _demo()
    w.add_entity(
        Entity(
            id="svc-floating", kind=EntityKind.SERVICE, name="floating",
            provenance=[Provenance(source="test", source_id="x")],
        )
    )
    w.add_relation(Relation(src="svc-floating", dst="db-orders", kind=RelationKind.DEPENDS_ON))
    assert "floating" in _for(w, "svc-floating")


def test_a_site_is_not_expected_to_be_hosted_somewhere():
    # Roots legitimately have nothing above them; flagging them would train people to
    # ignore the report.
    assert not _for(_demo(), "site-a")


def test_an_external_dependency_is_not_expected_to_be_hosted_either():
    assert "floating" not in _for(_demo(), "ext-payments")


def test_an_unreachable_quorum_is_flagged():
    w = _demo()
    w.entity("etcd").attrs["quorum"] = 9
    assert "quorum unreachable" in _for(w, "etcd")


def test_a_reachable_quorum_is_not_flagged():
    assert "quorum unreachable" not in _for(_demo(), "etcd")


def test_missing_provenance_is_flagged():
    w = World()
    w.add_entity(Entity(id="nowhere-from", kind=EntityKind.HOST, name="?"))
    assert "no provenance" in _for(w, "nowhere-from")


def test_it_reports_how_much_rests_on_one_source():
    a = audit(_demo())
    assert a.sources == {"static_yaml": 18}
    assert a.single_sourced == 18
    assert a.cross_confirmed == 0


def test_an_entity_seen_by_two_sources_counts_as_confirmed():
    w = _demo()
    w.add_entity(
        Entity(
            id="host-a1", kind=EntityKind.HOST, name="a1",
            provenance=[Provenance(source="monitoring", source_id="a1")],
        )
    )
    a = audit(w)
    assert a.cross_confirmed == 1
    assert "monitoring" in a.sources


def test_a_clean_map_says_so():
    w = World()
    w.add_entity(
        Entity(id="site-1", kind=EntityKind.SITE, name="s",
               provenance=[Provenance(source="t", source_id="1")])
    )
    w.add_entity(
        Entity(id="host-1", kind=EntityKind.HOST, name="h",
               provenance=[Provenance(source="t", source_id="2")])
    )
    w.add_relation(Relation(src="host-1", dst="site-1", kind=RelationKind.HOSTED_IN))
    assert audit(w).summary().endswith("nothing to flag")


# ---- risk ranking ----


def test_the_worst_thing_is_ranked_first():
    # `a > b or a == b` is always true after sorting by -reach. Assert the order itself.
    risks = single_points_of_failure(_demo(), limit=5)
    assert risks[0].entity_id == "site-a"
    assert [r.reach for r in risks] == sorted((r.reach for r in risks), reverse=True)
    assert risks[0].reach > risks[-1].reach


def test_reach_is_reported_as_a_share_of_the_estate():
    top = single_points_of_failure(_demo(), limit=1)[0]
    assert 0 < top.share <= 1
    assert top.reach == round(top.share * (len(_demo()) - 1))


def test_a_leaf_that_takes_nothing_with_it_is_left_out():
    ids = {r.entity_id for r in single_points_of_failure(_demo(), limit=50)}
    assert "svc-web" not in ids  # nothing depends on the storefront


def test_ranking_ignores_replicas_on_purpose():
    # Redundancy recorded but not real is what this list exists to surface, so scoring
    # with replicas in mind would forgive exactly the wrong entity.
    w = _demo()
    before = {r.entity_id: r.reach for r in single_points_of_failure(w, limit=50)}
    w.entity("svc-inventory").attrs["replicas"] = 99
    after = {r.entity_id: r.reach for r in single_points_of_failure(w, limit=50)}
    assert before == after


def test_it_can_be_narrowed_to_one_kind():
    risks = single_points_of_failure(_demo(), limit=50, kinds=(EntityKind.DATABASE,))
    assert risks
    assert all(r.kind == "database" for r in risks)


def test_the_limit_is_respected():
    assert len(single_points_of_failure(_demo(), limit=2)) == 2


def test_a_map_with_no_edges_says_so_rather_than_printing_nothing():
    from orrery.world import format_risks

    w = World()
    w.add_entity(Entity(id="lonely", kind=EntityKind.HOST, name="h"))
    assert "no dependency edges" in format_risks(single_points_of_failure(w), len(w))


# ---- through the CLI ----


def test_check_and_spof_are_reachable_and_machine_readable():
    for args, key in ((["check", "--json-out"], "findings"), (["spof", "--json-out"], "risks")):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["schema"] == 1
        assert key in payload


def test_spof_kind_filter_works_through_the_cli():
    result = runner.invoke(app, ["spof", "--kind", "site", "--json-out"])
    assert result.exit_code == 0, result.output
    assert all(r["kind"] == "site" for r in json.loads(result.stdout)["risks"])
