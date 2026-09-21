"""A real incident is usually not one thing dying.

The first corpus this engine was pointed at had two of eight incidents name several hosts
each — two disks in the same hour, a process stopped on four machines at once. Converted
with the first host as `trigger` and the rest in `observed`, both scored as MISSes, and
they read as engine defects until someone looked at what the record actually said: it was
asking the engine to predict an unrelated hardware failure from another one.

Everything the record says went down is input. What followed from all of it together is
the prediction.
"""
from __future__ import annotations

from orrery.backtest import Incident, replay

WORLD = """
entities:
  - {id: site, kind: site, name: site}
  - {id: host-1, kind: host, name: h1}
  - {id: host-2, kind: host, name: h2}
  - {id: db-1, kind: database, name: db1}
  - {id: db-2, kind: database, name: db2}
  - {id: svc, kind: service, name: svc}
relations:
  - {src: host-1, dst: site, kind: HOSTED_IN}
  - {src: host-2, dst: site, kind: HOSTED_IN}
  - {src: db-1, dst: host-1, kind: RUNS_ON}
  - {src: db-2, dst: host-2, kind: RUNS_ON}
  - {src: svc, dst: host-1, kind: RUNS_ON}
  - {src: svc, dst: host-2, kind: RUNS_ON}
"""


def _world(tmp_path) -> str:
    p = tmp_path / "w.yaml"
    p.write_text(WORLD, encoding="utf-8")
    return str(p)


def _incident(tmp_path, **kw) -> Incident:
    base = {"id": "INC-X", "world": _world(tmp_path), "trigger": "host-1", "observed": {}}
    base.update(kw)
    return Incident(**base)


def test_a_co_failure_is_input_not_a_prediction(tmp_path):
    """host-2 did not go down because host-1 did. Asking the engine to predict it is
    asking for something no dependency graph contains."""
    inc = _incident(tmp_path, also_failed=["host-2"], observed={"host-2": "down", "svc": "down"})
    cmp = replay(inc)

    graded = {j.entity_id for j in cmp.judgements}
    assert "host-2" not in graded  # it was told, not asked
    assert "svc" in graded


def test_what_follows_from_all_of_them_together_is_graded(tmp_path):
    """Neither host alone takes the service down — it runs on both. Both together do,
    and that is exactly the judgement the record exists to make."""
    both = replay(_incident(tmp_path, also_failed=["host-2"], observed={"svc": "down"}))
    assert [j.outcome.value for j in both.judgements] == ["hit"]

    one = replay(_incident(tmp_path, observed={"svc": "down"}))
    assert [j.outcome.value for j in one.judgements] == ["understated"]


def test_the_order_failures_are_applied_in_does_not_change_the_answer(tmp_path):
    a = replay(_incident(tmp_path, trigger="host-1", also_failed=["host-2"],
                         observed={"svc": "down"}))
    b = replay(_incident(tmp_path, trigger="host-2", also_failed=["host-1"],
                         observed={"svc": "down"}))
    assert [j.outcome for j in a.judgements] == [j.outcome for j in b.judgements] != []


def test_structural_reach_covers_every_failure(tmp_path):
    cmp = replay(_incident(tmp_path, also_failed=["host-2"], observed={"svc": "down"}))
    assert {"db-1", "db-2"} <= cmp.structural_reach


def test_a_co_failure_that_is_not_on_the_map_is_refused(tmp_path):
    """Same rule as `observed`: a typo silently removes an input and changes every
    judgement downstream of it, so it is refused rather than skipped."""
    import pytest
    with pytest.raises(KeyError):
        replay(_incident(tmp_path, also_failed=["host-9"], observed={"svc": "down"}))
