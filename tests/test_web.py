"""The map page: the server, the export, and that both say what the CLI says."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from orrery.cli import app
from orrery.web import blast_payload, export_html, simulate_payload, snapshot_payload, world_payload
from orrery.web.api import EXPORT_LIMIT
from orrery.web.server import make_server
from orrery.world import World


@pytest.fixture(scope="module")
def world(demo_world_file) -> World:
    return World.load(demo_world_file)


@pytest.fixture(scope="module")
def base_url(world):
    server = make_server(world, "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def test_world_payload_carries_everything_the_page_draws(world):
    p = world_payload(world)
    assert len(p["entities"]) == len(world)
    assert len(p["relations"]) == len(world.relations())
    by_id = {e["id"]: e for e in p["entities"]}
    # sized by what goes with it, sites included — the page must not draw a site smaller
    # than the rack inside it
    assert by_id["rack-a1"]["reach"] == 15
    assert by_id["site-a"]["reach"] >= by_id["rack-a1"]["reach"]
    # the demo's one declared tolerance is the one moment the clock can change an answer
    assert p["tolerances"] == [2400]
    assert {f["id"] for f in p["audit"]["findings"]} >= {"svc-checkout", "svc-search"}
    # a load balancer does not carry its death down to its members; the page's reach
    # preview has to know that or it contradicts `spof`
    assert "MEMBER_OF" not in by_id["lb-edge"]["carries"]
    assert "MEMBER_OF" in by_id["k8s-main"]["carries"]


def test_cli_json_and_page_share_one_shape(world):
    runner = CliRunner()
    out = runner.invoke(app, ["simulate", "host-a1", "--json-out"])
    assert out.exit_code == 0, out.output
    cli = json.loads(out.output)
    assert cli == {"schema": 1, **simulate_payload(world, "host-a1")}
    out = runner.invoke(app, ["blast", "host-a1", "--json-out"])
    assert json.loads(out.output) == {"schema": 1, **blast_payload(world, "host-a1")}


def test_simulate_payload_leaves_the_world_alone(world):
    simulate_payload(world, "site-a")
    assert all(e.status.value == "up" for e in world.entities())


def test_snapshot_buckets_match_live_answers(world):
    snap = snapshot_payload(world)
    buckets = [None, *snap["tolerances"]]
    for i, elapsed in enumerate(buckets):
        live = simulate_payload(world, "ext-payments", "down", elapsed)["effects"]
        assert snap["sims"][f"ext-payments|down|{i}"] == [
            [e["id"], e["status"], e["why"]] for e in live
        ]
    # and the bucket that matters differs: past forty minutes checkout stops taking orders
    before = {i: s for i, s, _ in snap["sims"]["ext-payments|down|0"]}
    after = {i: s for i, s, _ in snap["sims"]["ext-payments|down|1"]}
    assert before["svc-checkout"] == "degraded"
    assert after["svc-checkout"] == "down"


def test_snapshot_refuses_a_world_too_big_to_precompute(world, monkeypatch):
    monkeypatch.setattr("orrery.web.api.EXPORT_LIMIT", len(world) - 1)
    with pytest.raises(ValueError, match="orrery map"):
        snapshot_payload(world)
    assert EXPORT_LIMIT > 1000


def test_export_is_one_file_that_needs_nothing(world, tmp_path):
    out = tmp_path / "map.html"
    export_html(world, out)
    html = out.read_text("utf-8")
    assert 'src="app.js"' not in html and 'href="app.css"' not in html
    assert "window.ORRERY_SNAPSHOT=" in html
    # nothing fetched from anywhere: the file has to open on a laptop with no network
    assert not re.search(r'(src|href)="https?://', html)
    snap = json.loads(html.split("window.ORRERY_SNAPSHOT=", 1)[1].split(";</script>", 1)[0])
    assert len(snap["entities"]) == len(world)


def test_export_cannot_be_broken_out_of_by_a_name(tmp_path):
    from orrery.schema import Entity

    w = World()
    w.add_entity(Entity(id="x", kind="service", name="</script><script>alert(1)</script>"))
    out = tmp_path / "map.html"
    export_html(w, out)
    html = out.read_text("utf-8")
    assert "<script>alert(1)" not in html


def test_server_answers_like_the_engine(base_url, world):
    status, body, ctype = _get(f"{base_url}/api/world")
    assert status == 200 and ctype.startswith("application/json")
    data = json.loads(body)
    assert data["mode"] == "live" and len(data["entities"]) == len(world)

    status, body, _ = _get(f"{base_url}/api/simulate?id=ext-payments&elapsed_s=14400")
    got = json.loads(body)
    assert got == {"schema": 1, **simulate_payload(world, "ext-payments", "down", 14400)}

    status, body, _ = _get(f"{base_url}/api/blast?id=host-a1")
    assert json.loads(body)["impacted"] == blast_payload(world, "host-a1")["impacted"]


def test_server_says_what_went_wrong(base_url):
    status, body, _ = _get(f"{base_url}/api/simulate?id=nope")
    assert status == 404 and "nope" in json.loads(body)["error"]
    status, body, _ = _get(f"{base_url}/api/simulate?id=db-stock&event=memory_leak")
    assert status == 400 and "unknown event" in json.loads(body)["error"]


def test_server_serves_its_page_and_nothing_else(base_url):
    status, body, ctype = _get(f"{base_url}/")
    assert status == 200 and ctype.startswith("text/html") and b"app.js" in body
    for path in ("/app.js", "/app.css"):
        assert _get(base_url + path)[0] == 200
    for path in ("/../pyproject.toml", "/static/app.js", "/server.py", "/%2e%2e/cli.py"):
        assert _get(base_url + path)[0] == 404


def test_map_export_from_the_cli(tmp_path):
    out = tmp_path / "m.html"
    r = CliRunner().invoke(app, ["map", "--export", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists() and "no server" in r.output
