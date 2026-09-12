"""Queries that make the graph a world. The first one: blast radius.

"If this goes down, what dies?" — computed structurally over RUNS_ON / HOSTED_IN / MEMBER_OF /
DEPENDS_ON edges. This is the Phase-1 deliverable: a correct answer across a whole company.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from orrery.schema import RelationKind

from .graph import World

# If X is down, everything that has one of these edges *pointing at X* is impacted.
_IMPACT_EDGES = (
    RelationKind.RUNS_ON,  # service RUNS_ON node -> node down kills service
    RelationKind.HOSTED_IN,  # host HOSTED_IN site -> site down kills host
    RelationKind.MEMBER_OF,  # node MEMBER_OF cluster -> cluster down kills node
    RelationKind.DEPENDS_ON,  # service DEPENDS_ON db -> db down degrades service
)


@dataclass
class BlastRadius:
    root: str
    impacted: dict[str, int] = field(default_factory=dict)  # entity id -> hop distance
    paths: dict[str, list[str]] = field(default_factory=dict)  # entity id -> path from root

    def by_hop(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for eid, hop in sorted(self.impacted.items(), key=lambda kv: kv[1]):
            out.setdefault(hop, []).append(eid)
        return out


def blast_radius(world: World, root: str, max_hops: int | None = None) -> BlastRadius:
    if root not in world.g:
        raise KeyError(root)
    br = BlastRadius(root=root)
    frontier = [root]
    br.paths[root] = [root]
    hop = 0
    while frontier and (max_hops is None or hop < max_hops):
        hop += 1
        nxt: list[str] = []
        for cur in frontier:
            for kind in _IMPACT_EDGES:
                for dependent in world.in_edges(cur, kind):
                    if dependent == root or dependent in br.impacted:
                        continue
                    br.impacted[dependent] = hop
                    br.paths[dependent] = br.paths[cur] + [dependent]
                    nxt.append(dependent)
        frontier = nxt
    return br
