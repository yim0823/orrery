"""Connector interface.

A connector knows how to talk to one inventory source and yields entities and relations
with provenance. Concrete connectors for real systems live in company-specific repos.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from orrery.schema import Entity, Relation


@dataclass
class Discovery:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)

    def extend(self, other: Discovery) -> None:
        self.entities.extend(other.entities)
        self.relations.extend(other.relations)


class Connector(Protocol):
    name: str

    def discover(self) -> Discovery: ...


def run_all(connectors: Iterable[Connector]) -> Discovery:
    out = Discovery()
    for c in connectors:
        out.extend(c.discover())
    return out
