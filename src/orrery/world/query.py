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
#
# And one read the other way: node MEMBER_OF a group with `quorum` -> the node down can take
# the group down. That tail is kept apart (`through_quorum`) — it happens only if the group
# also loses enough of its other voters.
def _impact_edges():
    # imported lazily: propagate imports World, and World's package imports this module
    from orrery.sim.propagate import _DEPENDENT_EDGES

    return _DEPENDENT_EDGES


def _upward():
    """Member → quorum group, the one consequence that travels up an edge. Same rule as `propagate`."""
    from orrery.sim.propagate import quorum_groups

    return quorum_groups


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
    # entity id -> the quorum group it hinges on. In range only if that group also loses
    # enough of its other members; everything else in `impacted` goes with the root alone.
    through_quorum: dict[str, str] = field(default_factory=dict)

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
    crosses, up = _crosses(), _upward()
    seen: set[str] = set()
    frontier = [root]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            kinds = tuple(k for k in _impact_edges() if crosses(k, world.entity(cur).kind))
            for dep in list(world.dependents(cur, kinds)) + up(world, cur):
                if dep != root and dep not in seen:
                    seen.add(dep)
                    nxt.append(dep)
        frontier = nxt
    return seen


def blast_radius(world: World, root: str, max_hops: int | None = None) -> BlastRadius:
    if root not in world.g:
        raise KeyError(root)
    crosses, up = _crosses(), _upward()
    br = BlastRadius(root=root)
    br.paths[root] = [root]
    # Two walks, one set. First what goes with the root on its own; then, from everything
    # that reached, the quorum groups that lose a voter and what those groups take with
    # them. The set is the same as one walk over both; the split is what an operator needs,
    # because "goes with it" and "goes if one more voter goes" are different pages.
    hop_of = {root: 0}
    frontier = [root]
    for phase in ("direct", "quorum"):
        if phase == "quorum":
            frontier = [e for e in hop_of]  # every entity reached so far may be a voter
        while frontier:
            nxt: list[str] = []
            for cur in frontier:
                if max_hops is not None and hop_of[cur] >= max_hops:
                    continue
                cur_kind = world.entity(cur).kind
                steps = [
                    d for k in _impact_edges() if crosses(k, cur_kind)
                    for d in world.in_edges(cur, k)
                ]
                ups = up(world, cur) if phase == "quorum" else []
                for dep in [*steps, *ups]:
                    if dep in hop_of:
                        continue
                    hop_of[dep] = hop_of[cur] + 1
                    br.impacted[dep] = hop_of[dep]
                    br.paths[dep] = br.paths[cur] + [dep]
                    if phase == "quorum":
                        br.through_quorum[dep] = (
                            dep if dep in ups else br.through_quorum.get(cur, cur)
                        )
                    nxt.append(dep)
            frontier = nxt
    return br
