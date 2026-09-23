"""A backtest loads each world once and replays every incident against it.

That is only safe if a replay never changes the world it is handed. If it did, the second
incident would start from the first one's damage and the report would grade the engine on
a cascade nobody recorded.
"""
from __future__ import annotations

import pathlib

from orrery.backtest import Incident, replay
from orrery.backtest.report import run
from orrery.world import World

FIXTURES = pathlib.Path("fixtures/incidents")


def test_replaying_against_a_shared_world_leaves_it_untouched():
    incidents = Incident.load_dir(FIXTURES)
    world = World.load(incidents[0].world)
    before = {e.id: e.status for e in world.entities()}
    for inc in incidents:
        if pathlib.Path(inc.world).resolve() == pathlib.Path(incidents[0].world).resolve():
            replay(inc, world)
    assert {e.id: e.status for e in world.entities()} == before


def test_the_shared_run_grades_exactly_like_loading_per_incident():
    incidents = Incident.load_dir(FIXTURES)
    shared = run(incidents)
    alone = [replay(i) for i in incidents]
    assert [(c.incident_id, [(j.entity_id, j.outcome) for j in c.judgements])
            for c in shared.comparisons] == \
           [(c.incident_id, [(j.entity_id, j.outcome) for j in c.judgements]) for c in alone]
