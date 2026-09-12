"""The world's clock. Thin wrapper over SimPy so time can be accelerated and events queued."""
from __future__ import annotations

from collections.abc import Callable

import simpy


class Clock:
    def __init__(self, start: float = 0.0):
        self.env = simpy.Environment(initial_time=start)

    @property
    def now(self) -> float:
        return self.env.now

    def at(self, delay: float, fn: Callable[[], None]) -> None:
        def _proc(env: simpy.Environment):
            yield env.timeout(delay)
            fn()

        self.env.process(_proc(self.env))

    def run(self, until: float | None = None) -> None:
        self.env.run(until=until)
