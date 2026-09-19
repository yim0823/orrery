"""Queries that make the graph a world. The first one: blast radius.

"If this goes down, what is in range?" — computed structurally over the edges that carry
consequence. Which edges those are is defined once, in `orrery.sim.propagate`, and imported
here. Two lists drifted apart once already: a network segment failure was visible to
`simulate` and invisible to `blast`, which is the kind of disagreement that makes people
stop believing both answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .graph import World


# If X is down, everything that has one of these edges *pointing at X* is in range.
#
#   service RUNS_ON node        -> node down reaches the service
#   host HOSTED_IN site         -> site down reaches the host
#   node MEMBER_OF cluster      -> cluster down reaches the node
#   service DEPENDS_ON database -> database down reaches the service
#   host CONNECTS_TO segment    -> segment down reaches the host
def _impact_edges():
    # imported lazily: propagate imports World, and World's package imports this module
    from orrery.sim.propagate import _DEPENDENT_EDGES

    return _DEPENDENT_EDGES


def _crosses():
    """Not every edge in the list carries consequence from every kind — see the predicate.

    Importing it rather than restating it is the point: the list and the predicate are one
    rule, and the last time half of it lived here the two commands disagreed.
    """
    from orrery.sim.propagate import consequence_crosses

    return consequence_crosses


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


def reach(world: World, root: str) -> set[str]:
    """Everything in range of `root` failing, without recording how it got there.

    `blast_radius` keeps a path per impacted entity, which is what makes its answer
    arguable instead of oracular — and also what makes it too expensive to call once per
    entity in a large estate. Ranking wants only the size, so it gets only the set.
    """
    if root not in world.g:
        raise KeyError(root)
    kinds = _impact_edges()
    seen: set[str] = set()
    frontier = [root]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            for dep in world.dependents(cur, kinds):
                if dep != root and dep not in seen:
                    seen.add(dep)
                    nxt.append(dep)
        frontier = nxt
    return seen


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
        crosses = _crosses()
        for cur in frontier:
            cur_kind = world.entity(cur).kind
            for kind in _impact_edges():
                if not crosses(kind, cur_kind):
                    continue
                for dependent in world.in_edges(cur, kind):
                    if dependent == root or dependent in br.impacted:
                        continue
                    br.impacted[dependent] = hop
                    br.paths[dependent] = br.paths[cur] + [dependent]
                    nxt.append(dependent)
        frontier = nxt
    return br
