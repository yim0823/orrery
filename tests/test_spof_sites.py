"""`spof` leaves sites out of the command's default, and only the command's.

On the first real estate it ran on, nearly the whole top of the list was datacentres in
order of size. True, and it buried the hypervisor carrying six services that someone could move this
week — the same reason `check` has never reported a shared site.
"""
from __future__ import annotations

import json
import subprocess

from orrery.connectors import StaticYamlConnector
from orrery.schema.entities import EntityKind
from orrery.world import World, single_points_of_failure


def _demo() -> World:
    w = World()
    w.ingest(StaticYamlConnector("fixtures/demo-world.yaml").discover())
    return w


def test_the_library_ranks_everything_unless_told_otherwise():
    kinds = {r.kind for r in single_points_of_failure(_demo(), limit=50)}
    assert "site" in kinds


def test_exclude_kinds_removes_only_those_kinds():
    w = _demo()
    full = single_points_of_failure(w, limit=50)
    trimmed = single_points_of_failure(w, limit=50, exclude_kinds=(EntityKind.SITE,))
    assert [r for r in full if r.kind != "site"] == trimmed


def _cli(tmp_path, *args):
    subprocess.run(["orrery", "ingest", "fixtures/demo-world.yaml"], check=True,
                   capture_output=True, cwd=tmp_path)
    return subprocess.run(["orrery", "spof", *args], check=True, capture_output=True,
                          text=True, cwd=tmp_path).stdout


def test_the_command_leaves_sites_out_and_says_so(tmp_path, monkeypatch):
    import shutil
    shutil.copytree("fixtures", tmp_path / "fixtures")
    out = _cli(tmp_path, "--json-out")
    d = json.loads(out)
    assert all(r["kind"] != "site" for r in d["risks"])
    assert d["sites_left_out"] == 2
    text = _cli(tmp_path)
    assert "site(s) left out" in text


def test_asking_for_sites_gets_them(tmp_path):
    import shutil
    shutil.copytree("fixtures", tmp_path / "fixtures")
    assert any(json.loads(_cli(tmp_path, "--include-sites", "--json-out"))["risks"][i]["kind"] == "site"
               for i in range(2))
    only = json.loads(_cli(tmp_path, "--kind", "site", "--json-out"))
    assert only["risks"] and all(r["kind"] == "site" for r in only["risks"])
    assert only["sites_left_out"] == 0


def test_when_only_sites_reach_anything_the_note_survives():
    from orrery.world.audit import format_risks
    out = format_risks([], 3, "1 site(s) left out")
    assert "left out" in out and "no dependency edges" not in out
