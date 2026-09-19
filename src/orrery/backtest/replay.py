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
    world: str = ""
    judgements: list[Judgement] = field(default_factory=list)
    scored: int = 0
    skipped_unobserved: int = 0
    unverified_predictions: list[str] = field(default_factory=list)
    """Entities the engine predicted broken that the record says nothing about.

    These cannot be scored, and leaving it at that would make over-prediction free: an
    engine that paints half the estate red is never wrong about the half nobody looked at.
    The count is reported so precision can be read with it.
    """
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

    def precision(self) -> float | None:
        """Of the predictions somebody checked, how many were right?

        Not "of everything we predicted" — see `unverified_predictions`. An engine is
        only ever graded against what a human wrote down, and this number cannot see the
        predictions nobody thought to verify.
        """
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

    def exact_on_impacted(self) -> float | None:
        """The same, over what actually broke.

        `exact_rate` counts `correct_up` too, so a record listing forty healthy entities
        can carry a poor engine to a high score. This one cannot be padded that way.
        """
        broken = [j for j in self.judgements if _SEVERITY[j.actual] > 0]
        if not broken:
            return None
        return sum(1 for j in broken if j.outcome is Outcome.HIT) / len(broken)


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

    unknown = [eid for eid in incident.observed if eid not in world.g]
    if unknown:
        raise KeyError(
            f"{incident.id}: observed entities missing from {incident.world}: "
            f"{', '.join(sorted(unknown))}. A typo here silently removes a judgement, so "
            f"it is refused rather than skipped."
        )

    reach = set(blast_radius(world, incident.trigger).impacted)

    sim = world.fork()
    propagate(sim, Event(incident.trigger, incident.event), elapsed_s=incident.elapsed_s)

    cmp = Comparison(
        incident_id=incident.id,
        title=incident.title,
        world=incident.world,
        structural_reach=reach,
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
    if not incident.assume_unlisted_up:
        # Compared against the untouched world, not against UP: a snapshot may legitimately
        # record something as already degraded, and reporting that as a prediction nobody
        # checked would inflate the warning with entities the engine never touched.
        cmp.unverified_predictions = sorted(
            e.id
            for e in sim.entities()
            if e.status is not world.entity(e.id).status
            and e.id != incident.trigger
            and e.id not in incident.observed
        )
    cmp.skipped_unobserved += sum(
        1 for e in sim.entities()
        if e.id not in incident.observed and e.id != incident.trigger
        and not incident.assume_unlisted_up
    )
    return cmp
