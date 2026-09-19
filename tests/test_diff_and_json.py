from __future__ import annotations

import json
import subprocess
import sys

from typer.testing import CliRunner

from orrery.cli import SCHEMA_VERSION, app
from orrery.connectors import StaticYamlConnector
from orrery.schema import Entity, EntityKind, Relation, RelationKind, RelationStrength, Status
from orrery.world import World, diff

runner = CliRunner()


def _world() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


# ---- diff ----


def test_identical_snapshots_report_no_change():
    d = diff(_world(), _world())
    assert d.empty
    assert d.summary() == "no change"


def test_an_appearing_entity_is_reported():
    a, b = _world(), _world()
    b.add_entity(Entity(id="host-new", kind=EntityKind.HOST, name="new"))
    d = diff(a, b)
    assert [e.id for e in d.added_entities] == ["host-new"]
    assert not d.removed_entities


def test_a_vanished_entity_is_reported():
    a, b = _world(), _world()
    a.add_entity(Entity(id="host-gone", kind=EntityKind.HOST, name="gone"))
    d = diff(a, b)
    assert [e.id for e in d.removed_entities] == ["host-gone"]


def test_attribute_drift_is_reported_per_key():
    a, b = _world(), _world()
    b.entity("svc-web").attrs["replicas"] = 5
    d = diff(a, b)
    changed = [(c.id, c.field, c.before, c.after) for c in d.changed_entities]
    assert ("svc-web", "attrs.replicas", 3, 5) in changed


def test_new_wiring_is_reported():
    a, b = _world(), _world()
    b.add_relation(Relation(src="svc-web", dst="db-orders", kind=RelationKind.DEPENDS_ON))
    d = diff(a, b)
    assert [(r.src, r.dst) for r in d.added_relations] == [("svc-web", "db-orders")]


def test_a_dependency_turning_hard_is_reported():
    # The quietest and most dangerous kind of drift: same edge, different meaning.
    a, b = _world(), _world()
    rel = next(
        r for r in b.relations(RelationKind.DEPENDS_ON)
        if r.src == "svc-checkout" and r.dst == "ext-payments"
    )
    rel.strength = RelationStrength.HARD
    d = diff(a, b)
    assert any(c.field == "strength" for c in d.changed_relations)


def test_status_is_not_treated_as_drift():
    # Runtime state changes every minute; mixing it in would bury structural drift.
    a, b = _world(), _world()
    b.set_status("svc-web", Status.DOWN)
    assert diff(a, b).empty


def test_diff_is_directional():
    a, b = _world(), _world()
    b.add_entity(Entity(id="host-new", kind=EntityKind.HOST, name="new"))
    assert diff(a, b).added_entities and not diff(a, b).removed_entities
    assert diff(b, a).removed_entities and not diff(b, a).added_entities


# ---- machine-readable output ----


def _json(*args: str) -> dict:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_every_json_payload_declares_a_schema_version():
    for args in (
        ["blast", "db-stock", "--json-out"],
        ["simulate", "db-stock", "--json-out"],
        ["resolve", "fixtures/demo-world.yaml", "--json-out"],
        ["backtest", "fixtures/incidents", "--json-out"],
    ):
        assert _json(*args)["schema"] == SCHEMA_VERSION


def test_blast_json_carries_the_path_not_just_the_verdict():
    payload = _json("blast", "db-stock", "--json-out")
    checkout = next(i for i in payload["impacted"] if i["id"] == "svc-checkout")
    assert checkout["hop"] == 2
    # a bare list of names is unarguable-with; the path is what lets someone check it
    assert checkout["path"][0] == "db-stock"
    assert checkout["path"][-1] == "svc-checkout"


def test_simulate_json_explains_each_status():
    payload = _json("simulate", "host-a1", "--json-out")
    by_id = {e["id"]: e for e in payload["effects"]}
    assert by_id["svc-inventory"]["status"] == "down"
    assert by_id["svc-inventory"]["why"]


def test_simulate_json_reflects_the_clock():
    short = _json("simulate", "ext-payments", "--json-out", "--elapsed-s", "600")
    long_ = _json("simulate", "ext-payments", "--json-out", "--elapsed-s", "14400")
    assert {e["id"]: e["status"] for e in short["effects"]}["svc-checkout"] == "degraded"
    assert {e["id"]: e["status"] for e in long_["effects"]}["svc-checkout"] == "down"


def test_backtest_json_exposes_every_judgement():
    payload = _json("backtest", "fixtures/incidents", "--json-out")
    assert payload["incidents"] >= 6
    assert payload["outcomes"]["miss"] == 0
    judgements = [j for c in payload["comparisons"] for j in c["judgements"]]
    assert len(judgements) == payload["scored"]


def test_json_output_is_parseable_from_a_pipe():
    # The point of this flag is another program reading it, so check the real thing.
    out = subprocess.run(
        [sys.executable, "-m", "orrery.cli", "blast", "site-a", "--json-out"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:  # module entrypoint is optional; the installed script is the contract
        out = subprocess.run(
            ["uv", "run", "orrery", "blast", "site-a", "--json-out"],
            capture_output=True, text=True, check=True,
        )
    assert json.loads(out.stdout)["root"] == "site-a"
