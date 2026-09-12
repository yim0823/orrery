"""Entity resolution: the same thing is named differently by every system.

Policy: propose candidates, never auto-merge. Humans confirm aliases; confirmed aliases
are the only thing that changes the graph.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from orrery.schema import Entity

_norm_re = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """Lowercase, strip separators and common environment suffixes."""
    n = _norm_re.sub("", name.lower())
    for suffix in ("prod", "production", "live", "svc", "service"):
        if n.endswith(suffix) and len(n) > len(suffix) + 2:
            n = n[: -len(suffix)]
    return n


@dataclass(frozen=True)
class Alias:
    canonical: str  # canonical entity id
    alias: str  # another entity id that is the same thing
    confirmed_by: str  # a human


class Resolver:
    def __init__(self, aliases: list[Alias] | None = None):
        self._alias_to_canonical: dict[str, str] = {}
        for a in aliases or []:
            self.confirm(a)

    def confirm(self, alias: Alias) -> None:
        self._alias_to_canonical[alias.alias] = alias.canonical

    def canonical(self, entity_id: str) -> str:
        seen = set()
        cur = entity_id
        while cur in self._alias_to_canonical and cur not in seen:
            seen.add(cur)
            cur = self._alias_to_canonical[cur]
        return cur

    def propose(self, entities: list[Entity]) -> list[list[Entity]]:
        """Group entities of the same kind whose normalized names collide.

        Returns candidate clusters (size >= 2) for a human to review. Does not merge.
        """
        buckets: dict[tuple[str, str], list[Entity]] = defaultdict(list)
        for e in entities:
            key = (e.kind.value, normalize(e.name))
            buckets[key].append(e)
        return [group for group in buckets.values() if len(group) >= 2]
