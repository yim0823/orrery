"""`diff` answers what changed in the map. This answers what changed about whether the
map is alright, and the two are not the same sentence.

The team that first looked at a real map said the findings were new to them — and then
said the thing that mattered more: nothing watches for them. So the product is not the
report, it is the line that appears the morning something becomes a single point of
failure. A service moving from two racks to one shows up in the structural diff as one
host changing rack, which reads as routine maintenance and gets scrolled past.
"""
from __future__ import annotations

from orrery.connectors import StaticYamlConnector
from orrery.world import World, audit, audit_diff, diff
from orrery.world.audit import PERVASIVE_MIN

TWO_RACKS = """
entities:
  - {id: site, kind: site, name: site}
  - {id: rack-1, kind: rack, name: rack1}
  - {id: rack-2, kind: rack, name: rack2}
  - {id: h1, kind: host, name: h1}
  - {id: h2, kind: host, name: h2}
  - {id: svc, kind: service, name: lobby}
relations:
  - {src: rack-1, dst: site, kind: HOSTED_IN}
  - {src: rack-2, dst: site, kind: HOSTED_IN}
  - {src: h1, dst: rack-1, kind: HOSTED_IN}
  - {src: h2, dst: RACK, kind: HOSTED_IN}
  - {src: svc, dst: h1, kind: RUNS_ON}
  - {src: svc, dst: h2, kind: RUNS_ON}
"""


def _world(text: str, tmp_path, name: str = "w") -> World:
    p = tmp_path / f"{name}.yaml"
    p.write_text(text, encoding="utf-8")
    w = World()
    w.ingest(StaticYamlConnector(str(p)).discover())
    return w


def _spread(tmp_path) -> World:
    return _world(TWO_RACKS.replace("RACK", "rack-2"), tmp_path, "spread")


def _squeezed(tmp_path) -> World:
    return _world(TWO_RACKS.replace("RACK", "rack-1"), tmp_path, "squeezed")


def test_a_service_becoming_single_racked_is_reported_as_news(tmp_path):
    before, after = _spread(tmp_path), _squeezed(tmp_path)
    ad = audit_diff(audit(before), audit(after))

    assert [(c.check, c.entity_id) for c in ad.appeared] == [("redundancy in one rack", "svc")]
    assert not ad.resolved


def test_the_structural_diff_alone_does_not_say_it(tmp_path):
    """Kept as a test rather than a comment: this is the reason the other one exists. One
    host changed rack. Nothing in those two lines says the service is now a single point
    of failure."""
    text = diff(_spread(tmp_path), _squeezed(tmp_path)).summary()
    assert "rack-1" in text and "rack-2" in text
    assert "redundancy" not in text


def test_a_service_spreading_back_out_is_reported_too(tmp_path):
    ad = audit_diff(audit(_squeezed(tmp_path)), audit(_spread(tmp_path)))
    assert [c.entity_id for c in ad.resolved] == ["svc"]
    assert not ad.appeared


def test_the_same_map_twice_says_nothing(tmp_path):
    a = audit(_spread(tmp_path))
    ad = audit_diff(a, a)
    assert ad.empty
    assert "same things" in ad.summary()


def test_a_finding_that_got_worse_is_its_own_line(tmp_path):
    """Three machines in one rack becoming six is not a new problem. It is a bigger one,
    and a watcher who only sees appeared/resolved would never hear about it."""
    bigger = TWO_RACKS.replace("RACK", "rack-1").replace(
        "  - {id: svc, kind: service, name: lobby}",
        "  - {id: h3, kind: host, name: h3}\n  - {id: svc, kind: service, name: lobby}",
    ) + "  - {src: h3, dst: rack-1, kind: HOSTED_IN}\n  - {src: svc, dst: h3, kind: RUNS_ON}\n"
    ad = audit_diff(audit(_squeezed(tmp_path)), audit(_world(bigger, tmp_path, "bigger")))

    assert [c.entity_id for c in ad.changed] == ["svc"]
    assert "2 places" in ad.changed[0].before
    assert "3 places" in ad.changed[0].after


def test_a_check_crossing_the_folding_line_is_reported_as_the_fold_itself(tmp_path):
    """A folded check has no individual findings, so every one of them would otherwise
    read as resolved the day it folds, and as a flood of new ones the day it unfolds.
    Neither happened."""
    def placeless(n: int) -> str:
        head = ["entities:", "  - {id: site, kind: site, name: site}"]
        head += [f"  - {{id: vm-{i}, kind: vm, name: vm{i}}}" for i in range(n)]
        head.append("relations:")
        head += [f"  - {{src: vm-{i}, dst: site, kind: HOSTED_IN}}" for i in range(n)]
        return "\n".join(head) + "\n"

    small = audit(_world(placeless(3), tmp_path, "small"))
    big = audit(_world(placeless(PERVASIVE_MIN + 5), tmp_path, "big"))
    ad = audit_diff(small, big)

    assert ad.folding_changed == ["no recorded placement"]
    assert not ad.appeared and not ad.resolved  # not 3 resolved, not 20 appeared
    assert "compare the counts" in ad.summary()
