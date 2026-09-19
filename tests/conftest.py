"""Shared setup.

The CLI keeps its world in a state file, and the tests that exercise the CLI used to read
whatever happened to be in the working copy. That passed on any machine where someone had
run `orrery ingest` and failed on a clean checkout, which is the wrong way round: the
tests were green because of something no test had done.

So the state file is created here, in a temporary directory, for the whole session.
"""
from __future__ import annotations

import pathlib

import pytest

from orrery.connectors import StaticYamlConnector
from orrery.resolve import Resolver
from orrery.world import World

DEMO = "fixtures/demo-world.yaml"


@pytest.fixture(scope="session")
def demo_world_file(tmp_path_factory) -> pathlib.Path:
    """The demo world, ingested and saved where the CLI will look for it."""
    path = tmp_path_factory.mktemp("orrery-state") / "world.yaml"
    w = World()
    w.ingest(StaticYamlConnector(DEMO).discover(), Resolver())
    w.save(path)
    return path


@pytest.fixture(autouse=True, scope="session")
def _cli_state(demo_world_file):
    """Point the CLI's default state file at the temporary one.

    Session-scoped, so it uses `MonkeyPatch` directly rather than the function-scoped
    `monkeypatch` fixture.
    """
    from orrery import cli

    mp = pytest.MonkeyPatch()
    mp.setattr(cli, "_STATE", demo_world_file)
    yield
    mp.undo()
