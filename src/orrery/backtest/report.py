"""Aggregate replays into a report you can put in front of someone."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from orrery.world import World

from .incident import Incident
from .replay import Comparison, Outcome, replay


@dataclass
class Report:
    comparisons: list[Comparison] = field(default_factory=list)

    @property
    def incidents(self) -> int:
        return len(self.comparisons)

    @property
    def scored(self) -> int:
        return sum(c.scored for c in self.comparisons)

    @property
    def skipped(self) -> int:
        return sum(c.skipped_unobserved for c in self.comparisons)

    @property
    def unverified(self) -> int:
        """Predictions of breakage that no record confirms or denies.

        Precision cannot see these, so without the number an engine that over-predicts
        looks identical to one that does not.
        """
        return sum(len(c.unverified_predictions) for c in self.comparisons)

    def total(self, outcome: Outcome) -> int:
        return sum(c.count(outcome) for c in self.comparisons)

    def recall(self) -> float | None:
        """Pooled across incidents, not an average of averages.

        Averaging per-incident rates lets a one-entity incident weigh as much as a
        forty-entity one, which flatters small incidents. Pool the judgements instead.
        """
        broken = self.total(Outcome.HIT) + self.total(Outcome.UNDERSTATED) \
            + self.total(Outcome.OVERSTATED) + self.total(Outcome.MISS)
        if not broken:
            return None
        caught = broken - self.total(Outcome.MISS)
        return caught / broken

    def precision(self) -> float | None:
        predicted = self.total(Outcome.HIT) + self.total(Outcome.UNDERSTATED) \
            + self.total(Outcome.OVERSTATED) + self.total(Outcome.FALSE_ALARM)
        if not predicted:
            return None
        right = predicted - self.total(Outcome.FALSE_ALARM)
        return right / predicted

    def exact_rate(self) -> float | None:
        if not self.scored:
            return None
        return (self.total(Outcome.HIT) + self.total(Outcome.CORRECT_UP)) / self.scored

    def exact_on_impacted(self) -> float | None:
        """Exact severity, counted only over what actually broke.

        `exact_rate` includes `correct_up`, so a record listing forty healthy entities
        lifts the score without the engine getting anything hard right.
        """
        broken = (
            self.total(Outcome.HIT)
            + self.total(Outcome.UNDERSTATED)
            + self.total(Outcome.OVERSTATED)
            + self.total(Outcome.MISS)
        )
        if not broken:
            return None
        return self.total(Outcome.HIT) / broken

def run(incidents: list[Incident]) -> Report:
    """Replay each incident, loading each distinct world once.

    A corpus usually names one snapshot for every incident, and `replay` only reads and
    forks the world it is given, so sharing it cannot let one incident's damage leak into
    the next. The key is the resolved path: two spellings of one file are one world.
    """
    worlds: dict[str, World] = {}
    comparisons = []
    for i in incidents:
        key = str(pathlib.Path(i.world).resolve())
        if key not in worlds:
            worlds[key] = World.load(i.world)
        comparisons.append(replay(i, worlds[key]))
    return Report(comparisons=comparisons)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def format_report(report: Report) -> str:
    """A plain-text report. No colour, no spinner — this gets pasted into documents."""
    if not report.incidents:
        return "no incidents to replay"

    lines: list[str] = []
    lines.append(f"backtest: {report.incidents} incident(s), {report.scored} prediction(s) scored")
    if report.skipped:
        lines.append(
            f"  {report.skipped} entit(ies) skipped — the records say nothing about them"
        )
    lines.append("")

    lines.append(
        f"  recall    {_pct(report.recall())}   of what broke, we called broken at all"
    )
    lines.append(
        f"  precision {_pct(report.precision())}   of the predictions someone checked, right"
    )
    lines.append(f"  exact     {_pct(report.exact_rate())}   severity exactly right")
    lines.append(
        f"  on breaks {_pct(report.exact_on_impacted())}   severity exactly right, "
        f"counting only what broke"
    )
    lines.append("")
    if report.unverified:
        lines.append(
            f"  ⚠ {report.unverified} prediction(s) of breakage nobody checked. Precision "
            f"cannot see them,"
        )
        lines.append(
            "    so it is an upper bound: over-predicting is free until the records "
            "say otherwise."
        )
        lines.append("")

    rows = [
        ("hit", Outcome.HIT, "predicted, right severity"),
        ("correct up", Outcome.CORRECT_UP, "agreed it was unaffected"),
        ("understated", Outcome.UNDERSTATED, "said degraded, was down"),
        ("overstated", Outcome.OVERSTATED, "said down, was degraded"),
        ("false alarm", Outcome.FALSE_ALARM, "said broken, was fine"),
        ("MISS", Outcome.MISS, "said fine, was broken"),
    ]
    for label, outcome, meaning in rows:
        lines.append(f"  {label:<12} {report.total(outcome):>4}   {meaning}")
    lines.append("")

    misses = [(c, j) for c in report.comparisons for j in c.misses]
    if misses:
        lines.append(f"misses ({len(misses)}) — these are the ones that matter:")
        for c, j in misses[:20]:
            lines.append(f"  {c.incident_id:<12} {j.entity_id:<22} was {j.actual}, predicted {j.predicted}")
        if len(misses) > 20:
            lines.append(f"  ... and {len(misses) - 20} more")
        lines.append("")

    alarms = [(c, j) for c in report.comparisons for j in c.false_alarms]
    if alarms:
        lines.append(f"false alarms ({len(alarms)}):")
        for c, j in alarms[:10]:
            lines.append(f"  {c.incident_id:<12} {j.entity_id:<22} was up, predicted {j.predicted}")
        if len(alarms) > 10:
            lines.append(f"  ... and {len(alarms) - 10} more")
        lines.append("")

    if report.scored < 30:
        lines.append(
            "⚠ fewer than 30 scored predictions. Treat these rates as a smoke test, "
            "not a measurement."
        )

    worlds = {c.world for c in report.comparisons if c.world}
    if len(worlds) == 1 and report.incidents > 1:
        lines.append(
            "⚠ every incident replays against one snapshot. If that snapshot was written "
            "after the"
        )
        lines.append(
            "  incidents, this measures hindsight rather than prediction — an edge learned "
            "from a"
        )
        lines.append("  postmortem is already in the map being graded.")
    return "\n".join(lines)
