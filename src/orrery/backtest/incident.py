"""An incident record: what happened, and what was observed to break.

This is ground truth. It is deliberately awkward to write, because the awkward parts are
where backtests usually lie to themselves.

The sharp edge is `observed`. In a real incident you learn about the systems someone
noticed — the ones that paged, the ones a customer complained about, the ones that showed
up in the postmortem. Silence about a service is not evidence that it was healthy; it is
evidence that nobody looked. So by default an entity absent from `observed` is excluded
from scoring rather than counted as up. Set `assume_unlisted_up: true` only when the
record really does cover the whole world, which is rare and worth stating explicitly.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml
from pydantic import BaseModel, Field

from orrery.schema import Status


class Incident(BaseModel):
    """One past incident, replayable against a world."""

    id: str
    title: str = ""
    occurred_at: str | None = None  # ISO 8601

    world: str
    """Path to the world snapshot as it stood at the time, relative to the record."""

    trigger: str
    """Entity id where the incident started."""

    event: str = "down"
    """The event applied to the trigger. Matches the names behavior models react to."""

    observed: dict[str, Status] = Field(default_factory=dict)
    """Entity id -> the status it was actually in. Ground truth."""

    assume_unlisted_up: bool = False
    """Score entities absent from `observed` as having been up.

    Off by default. Turning it on asserts the record covers the entire world, which makes
    every quiet corner of the graph count against a false alarm. Say so only if it is true.
    """

    notes: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> Incident:
        p = pathlib.Path(path)
        data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8"))
        inc = cls(**data)
        # world paths are written relative to the record so a directory of incidents moves as one
        if not pathlib.Path(inc.world).is_absolute():
            inc.world = str((p.parent / inc.world).resolve())
        return inc

    @classmethod
    def load_dir(cls, path: str | pathlib.Path) -> list[Incident]:
        p = pathlib.Path(path)
        if p.is_file():
            return [cls.load(p)]
        files = sorted(f for f in p.glob("*.yaml") if f.name != "world.yaml")
        return [cls.load(f) for f in files]

    def impacted(self) -> set[str]:
        """Entities observed in any state other than up."""
        return {k for k, v in self.observed.items() if v is not Status.UP}
