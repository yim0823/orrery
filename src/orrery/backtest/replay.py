"""Replay an incident against the engine and compare the prediction to what happened."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from orrery.schema import Status
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius

from .incident import Incident

_SEVERITY = {Status.UP: 0, Status.UNKNOWN: 0, Status.DEGRADED: 1, Status.DOWN: 2}


class Outcome(StrEnum):
    """How a single prediction compares to the observed truth."""

    HIT = "hit"  # predicted the impact, at the right severity
    MISS = "miss"  # it broke and we said it was fine — the dangerous one
    FALSE_ALARM = "false_alarm"  # we said it breaks, it was fine
    UNDERSTATED = "understated"  # predicted degraded, actually down
    OVERSTATED = "overstated"  # predicted down, actually degraded
    CORRECT_UP = "correct_up"  # agreed it was unaffected


@dataclass
class Judgement:
    entity_id: str
    predicted: Status
    actual: Status
    outcome: Outcome


@dataclass
class Comparison:
    """The result of replaying one incident."""

    incident_id: str
    title: str
    judgements: list[Judgement] = field(default_factory=list)
    scored: int = 0
    skipped_unobserved: int = 0
    structural_reach: set[str] = field(default_factory=set)

    def by_outcome(self, outcome: Outcome) -> list[Judgement]:
        return [j for j in self.judgements if j.outcome is outcome]

    def count(self, outcome: Outcome) -> int:
        return sum(1 for j in self.judgements if j.outcome is outcome)

    @property
    def misses(self) -> list[Judgement]:
        return self.by_outcome(Outcome.MISS)

    @property
    def false_alarms(self) -> list[Judgement]:
        return self.by_outcome(Outcome.FALSE_ALARM)

    def recall(self) -> float | None:
        """Of what actually broke, how much did we predict as broken at all?"""
        actually_broken = [j for j in self.judgements if _SEVERITY[j.actual] > 0]
        if not actually_broken:
            return None
        caught = sum(1 for j in actually_broken if _SEVERITY[j.predicted] > 0)
        return caught / len(actually_broken)

    def precision(self) -> float | None:
        """Of what we predicted as broken, how much actually broke?"""
        predicted_broken = [j for j in self.judgements if _SEVERITY[j.predicted] > 0]
        if not predicted_broken:
            return None
        right = sum(1 for j in predicted_broken if _SEVERITY[j.actual] > 0)
        return right / len(predicted_broken)

    def exact_rate(self) -> float | None:
        """How often the severity was exactly right, not merely non-zero."""
        if not self.scored:
            return None
        exact = self.count(Outcome.HIT) + self.count(Outcome.CORRECT_UP)
        return exact / self.scored


def _classify(predicted: Status, actual: Status) -> Outcome:
    p, a = _SEVERITY[predicted], _SEVERITY[actual]
    if p == a:
        return Outcome.CORRECT_UP if p == 0 else Outcome.HIT
    if p == 0:
        return Outcome.MISS
    if a == 0:
        return Outcome.FALSE_ALARM
    return Outcome.UNDERSTATED if p < a else Outcome.OVERSTATED


def replay(incident: Incident) -> Comparison:
    """Run the engine over the incident's world and grade it against what was observed."""
    world = World.load(incident.world)

    reach = set(blast_radius(world, incident.trigger).impacted)

    sim = world.fork()
    propagate(sim, Event(incident.trigger, incident.event), elapsed_s=incident.elapsed_s)

    cmp = Comparison(
        incident_id=incident.id, title=incident.title, structural_reach=reach
    )

    # Which entities are we allowed to grade? Only those the record speaks to, unless it
    # claims full coverage. Grading a silence as "up" invents evidence nobody collected.
    if incident.assume_unlisted_up:
        candidates = [e.id for e in sim.entities()]
    else:
        candidates = list(incident.observed)

    for eid in candidates:
        if eid == incident.trigger:
            continue  # the trigger is the input, not a prediction
        if eid not in incident.observed and not incident.assume_unlisted_up:
            cmp.skipped_unobserved += 1
            continue
        actual = incident.observed.get(eid, Status.UP)
        predicted = sim.entity(eid).status
        cmp.judgements.append(Judgement(eid, predicted, actual, _classify(predicted, actual)))

    cmp.scored = len(cmp.judgements)
    cmp.skipped_unobserved += sum(
        1 for e in sim.entities()
        if e.id not in incident.observed and e.id != incident.trigger
        and not incident.assume_unlisted_up
    )
    return cmp
