from __future__ import annotations

import pathlib

import pytest

from orrery.backtest import Incident, Outcome, format_report, replay, run
from orrery.backtest.replay import _classify
from orrery.schema import Status

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "incidents"


def test_classify_covers_the_matrix():
    assert _classify(Status.DOWN, Status.DOWN) is Outcome.HIT
    assert _classify(Status.DEGRADED, Status.DEGRADED) is Outcome.HIT
    assert _classify(Status.UP, Status.UP) is Outcome.CORRECT_UP
    assert _classify(Status.UP, Status.DOWN) is Outcome.MISS
    assert _classify(Status.DOWN, Status.UP) is Outcome.FALSE_ALARM
    assert _classify(Status.DEGRADED, Status.DOWN) is Outcome.UNDERSTATED
    assert _classify(Status.DOWN, Status.DEGRADED) is Outcome.OVERSTATED


def test_unknown_is_not_treated_as_broken():
    # An entity nobody looked at must not be scored as a failure.
    assert _classify(Status.UP, Status.UNKNOWN) is Outcome.CORRECT_UP


def test_loads_incident_and_resolves_world_relative_to_the_record():
    inc = Incident.load(FIXTURES / "INC-0001.yaml")
    assert inc.id == "INC-0001"
    assert inc.trigger == "host-a1"
    assert pathlib.Path(inc.world).is_absolute()
    assert pathlib.Path(inc.world).exists()


def test_load_dir_skips_the_world_snapshot():
    incidents = Incident.load_dir(FIXTURES)
    ids = [i.id for i in incidents]
    assert ids == ["INC-0001", "INC-0002", "INC-0003"]


def test_replay_scores_only_observed_entities_by_default():
    inc = Incident.load(FIXTURES / "INC-0001.yaml")
    cmp = replay(inc)
    # five observed, minus nothing: the trigger is not in `observed`
    assert cmp.scored == 5
    assert cmp.skipped_unobserved > 0
    graded = {j.entity_id for j in cmp.judgements}
    assert graded == set(inc.observed)
    assert inc.trigger not in graded


def test_trigger_is_never_graded_as_a_prediction():
    inc = Incident.load(FIXTURES / "INC-0002.yaml")
    inc.observed[inc.trigger] = Status.DOWN
    cmp = replay(inc)
    assert all(j.entity_id != inc.trigger for j in cmp.judgements)


def test_assume_unlisted_up_widens_the_graded_set():
    inc = Incident.load(FIXTURES / "INC-0001.yaml")
    narrow = replay(inc).scored
    inc.assume_unlisted_up = True
    wide = replay(inc).scored
    assert wide > narrow


def test_known_engine_gap_shows_up_as_overstated_not_as_a_pass():
    # INC-0003 exists because the engine has no notion of a soft dependency.
    # If this ever becomes a HIT, the engine learned something — update the fixture.
    cmp = replay(Incident.load(FIXTURES / "INC-0003.yaml"))
    checkout = next(j for j in cmp.judgements if j.entity_id == "svc-checkout")
    assert checkout.actual is Status.DEGRADED
    assert checkout.predicted is Status.DOWN
    assert checkout.outcome is Outcome.OVERSTATED


def test_no_misses_on_the_demo_fixtures():
    report = run(Incident.load_dir(FIXTURES))
    assert report.total(Outcome.MISS) == 0
    assert report.recall() == 1.0


def test_recall_and_precision_are_pooled_not_averaged():
    report = run(Incident.load_dir(FIXTURES))
    # pooled recall must equal caught/broken across every judgement, not a mean of rates
    broken = [j for c in report.comparisons for j in c.judgements if j.actual is not Status.UP]
    caught = [j for j in broken if j.predicted is not Status.UP]
    assert report.recall() == pytest.approx(len(caught) / len(broken))


def test_exact_rate_penalizes_wrong_severity():
    report = run(Incident.load_dir(FIXTURES))
    assert report.total(Outcome.OVERSTATED) > 0
    assert report.exact_rate() is not None
    assert report.exact_rate() < 1.0


def test_report_warns_when_the_sample_is_too_small():
    report = run(Incident.load_dir(FIXTURES))
    text = format_report(report)
    assert "smoke test" in text
    assert "recall" in text and "precision" in text


def test_empty_report_does_not_crash():
    from orrery.backtest.report import Report

    assert format_report(Report()) == "no incidents to replay"
