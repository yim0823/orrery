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


_NOISE = ("production", "prod", "live", "service", "svc")


def normalize(name: str) -> str:
    """Lowercase, strip separators and common environment suffixes, in any order.

    The suffixes are stripped repeatedly until none remain, so `billing-svc-prod` and
    `billing-prod-svc` both become `billing`. A single pass in a fixed order left them as
    two different strings and so never proposed them as the same thing.
    """
    n = _norm_re.sub("", name.lower())
    stripped = True
    while stripped:
        stripped = False
        for suffix in _NOISE:
            if n.endswith(suffix) and len(n) > len(suffix) + 2:
                n = n[: -len(suffix)]
                stripped = True
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
        # A cycle means someone confirmed both "A is really B" and "B is really A". Either
        # is plausible on its own; together they are a contradiction, and resolving one
        # silently to itself would hide it until two half-maps disagreed at 3am.
        cur = alias.canonical
        seen: set[str] = set()
        while True:
            if cur == alias.alias:
                raise ValueError(
                    f"alias cycle: {alias.alias} -> {alias.canonical} leads back to "
                    f"{alias.alias}. One of these confirmations is wrong."
                )
            if cur in seen or cur not in self._alias_to_canonical:
                break
            seen.add(cur)
            cur = self._alias_to_canonical[cur]
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
