"""A finding on half the estate is a fact about the estate, not a list of defects.

`check` already refuses to report two of those: a shared site, and a shared rack where
there is only one rack. This is the same argument arriving from the other direction, and
it was found by running the audit on a real map rather than by thinking about it. On a
production estate, `no recorded placement` fired on more than half of the virtual
machines — not because thousands of things are broken, but because nobody records which
physical machine a cloud VM runs on. Printing every one of them does not say that. It
buries the few findings that were worth acting on.
"""
from __future__ import annotations

from orrery.connectors import StaticYamlConnector
from orrery.world import World, audit
from orrery.world.audit import PERVASIVE_MIN


def _world(text: str, tmp_path) -> World:
    p = tmp_path / "w.yaml"
    p.write_text(text, encoding="utf-8")
    w = World()
    w.ingest(StaticYamlConnector(str(p)).discover())
    return w


def _placeless_vms(n: int) -> str:
    lines = ["entities:", "  - {id: site, kind: site, name: site}"]
    lines += [f"  - {{id: vm-{i}, kind: vm, name: vm{i}}}" for i in range(n)]
    lines.append("relations:")
    # Hosted in the site but standing on nothing — exactly a cloud VM whose hypervisor
    # nobody records.
    lines += [f"  - {{src: vm-{i}, dst: site, kind: HOSTED_IN}}" for i in range(n)]
    return "\n".join(lines) + "\n"


def test_a_check_that_fires_on_everything_is_folded_into_one_line(tmp_path):
    a = audit(_world(_placeless_vms(PERVASIVE_MIN + 5), tmp_path))

    assert "no recorded placement" in a.pervasive
    assert not [f for f in a.findings if f.check == "no recorded placement"]
    hits, eligible = a.pervasive["no recorded placement"]
    assert hits == eligible == PERVASIVE_MIN + 5


def test_nothing_is_hidden_the_count_and_the_share_are_still_reported(tmp_path):
    a = audit(_world(_placeless_vms(PERVASIVE_MIN + 5), tmp_path))
    text = a.summary()
    assert "no recorded placement" in text
    assert f"{PERVASIVE_MIN + 5} of {PERVASIVE_MIN + 5}" in text
    assert "100%" in text
    assert a.to_dict()["pervasive"]["no recorded placement"]["hits"] == PERVASIVE_MIN + 5


def test_a_short_list_is_left_alone_because_the_list_is_the_summary(tmp_path):
    """A share is meaningless on a small map: one finding out of two is 50%. The first
    version of this folded seven test fixtures into "a gap in the data" for that reason.
    The floor is the length at which the report stops printing names."""
    a = audit(_world(_placeless_vms(3), tmp_path))

    assert a.pervasive == {}
    assert len([f for f in a.findings if f.check == "no recorded placement"]) == 3


def test_a_minority_finding_is_still_listed_however_big_the_map(tmp_path):
    """Folding keys off the share, not the count. Twenty findings among a thousand
    eligible entities are twenty exceptions, and exceptions are the product."""
    n = PERVASIVE_MIN + 5
    text = _placeless_vms(n)
    # Give most of the VMs somewhere to run, leaving the same absolute number placeless.
    extra = ["  - {id: host-1, kind: host, name: h1}"]
    rels = [f"  - {{src: vm-ok-{i}, dst: host-1, kind: RUNS_ON}}" for i in range(n * 3)]
    extra += [f"  - {{id: vm-ok-{i}, kind: vm, name: ok{i}}}" for i in range(n * 3)]
    text = text.replace("relations:", "\n".join(extra) + "\nrelations:")
    text += "\n".join(rels) + "\n  - {src: host-1, dst: site, kind: HOSTED_IN}\n"

    a = audit(_world(text, tmp_path))
    assert a.pervasive == {}
    assert len([f for f in a.findings if f.check == "no recorded placement"]) == n


def test_the_denominator_is_the_kinds_the_check_looked_at(tmp_path):
    """Counting against the whole estate would let a pervasive gap hide under a big
    denominator — ten thousand hosts would dilute a check that only reads virtual
    machines, which is the opposite of the point."""
    n = PERVASIVE_MIN + 5
    text = _placeless_vms(n)
    hosts = [f"  - {{id: host-{i}, kind: host, name: h{i}}}" for i in range(n * 10)]
    rels = [f"  - {{src: host-{i}, dst: site, kind: HOSTED_IN}}" for i in range(n * 10)]
    text = text.replace("relations:", "\n".join(hosts) + "\nrelations:") + "\n".join(rels) + "\n"

    a = audit(_world(text, tmp_path))
    assert "no recorded placement" in a.pervasive  # hosts do not dilute it
