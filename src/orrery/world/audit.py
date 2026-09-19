"""Two questions you have the moment a map exists.

**Is this map any good?** You built it from four systems that disagree with each other.
Before anyone acts on an answer, someone should see how much of it is held together by
one source, how many services have no recorded place to run, and which entities nothing
points at — usually a join that silently failed rather than a server nobody uses.

**What is most dangerous?** Ranking entities by how much dies with them needs no incident
history and no calibration. It is often the first output that changes what a team does,
because the top of the list is reliably something nobody had thought of.

Neither of these is a verdict. Both are lists of things worth looking at, and the
findings are deliberately described as questions rather than errors — a map is a model of
an organization, and organizations are legitimately strange.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from orrery.schema import EntityKind, RelationKind

from .graph import World

_PLACEABLE = (EntityKind.SERVICE, EntityKind.DATABASE, EntityKind.NODE, EntityKind.VM)
_ROOTS = (EntityKind.SITE, EntityKind.EXTERNAL, EntityKind.NETWORK_SEGMENT)


@dataclass
class Finding:
    check: str
    entity_id: str
    detail: str


@dataclass
class MapAudit:
    entities: int = 0
    relations: int = 0
    findings: list[Finding] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)
    single_sourced: int = 0
    cross_confirmed: int = 0

    def by_check(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.check, []).append(f)
        return out

    def to_dict(self) -> dict:
        return {
            "entities": self.entities,
            "relations": self.relations,
            "sources": self.sources,
            "single_sourced": self.single_sourced,
            "cross_confirmed": self.cross_confirmed,
            "findings": [
                {"check": f.check, "id": f.entity_id, "detail": f.detail} for f in self.findings
            ],
        }

    def summary(self) -> str:
        lines = [f"map: {self.entities:,} entities, {self.relations:,} relations"]
        if self.sources:
            lines.append(
                "  sources: "
                + ", ".join(f"{k} ({v:,})" for k, v in sorted(self.sources.items()))
            )
            lines.append(
                f"  {self.cross_confirmed:,} entities confirmed by more than one source, "
                f"{self.single_sourced:,} by exactly one"
            )
        lines.append("")
        groups = self.by_check()
        if not groups:
            lines.append("nothing to flag")
            return "\n".join(lines)
        for check, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"{check} ({len(items)})")
            for f in items[:15]:
                lines.append(f"  {f.entity_id:<36} {f.detail}")
            if len(items) > 15:
                lines.append(f"  ... and {len(items) - 15} more")
            lines.append("")
        return "\n".join(lines).rstrip()


def _carriers(
    world: World,
    start: str,
    memo: dict[str, frozenset[str]],
    visiting: set[str],
) -> frozenset[str]:
    """Everything below `start` whose loss takes `start` down with it.

    The two edge kinds mean opposite things here and an earlier version of this walk
    unioned both, which made the audit contradict the simulator that ships beside it.

    Several `RUNS_ON` targets are **alternatives**: `propagate` only calls a thing down
    when the last of its places is gone, so a killer has to be under *every* place — they
    intersect. `HOSTED_IN` is not an alternative. A host sits in one rack; if the map
    claims two, losing either is bad news either way — so those union.
    """
    if start in memo:
        return memo[start]
    if start in visiting:
        # A cycle in the map — `HOSTED_IN` pointing both ways between two hosts, say.
        # Say nothing rather than loop; `check` reports map defects, it should not hang on
        # one. The cycle itself is visible as an odd shape in `spof`.
        return frozenset()
    visiting.add(start)

    places = world.out_edges(start, RelationKind.RUNS_ON)
    shared: frozenset[str] | None = None
    for place in places:
        below = frozenset({place}) | _carriers(world, place, memo, visiting)
        shared = below if shared is None else (shared & below)
    result = shared or frozenset()
    for container in world.out_edges(start, RelationKind.HOSTED_IN):
        result = result | {container} | _carriers(world, container, memo, visiting)

    visiting.discard(start)
    if not visiting:
        # Only cache a result computed outside any cycle-breaking. A truncated answer that
        # got memoized would leak out of the cycle it came from and poison the rest.
        memo[start] = result
    return result


def _shared_foundation(world: World, entity_id: str) -> tuple[str, int, EntityKind] | None:
    """Do all of this entity's places to run stand on one thing further down?

    Virtualization makes redundancy easy to fake without anyone meaning to. Three
    Kubernetes nodes look like three places to fail; if they are three virtual machines on
    one physical server, they are one. Nothing inside the cluster can see this — Kubernetes
    does not know what it is standing on — so the map is the only place the question can
    be asked at all.

    Returns the **nearest** thing whose loss would take every place with it, and its kind,
    since a shared physical server and a shared rack are the same defect with different
    fixes. A place counts as its own carrier: one pod on a node and one instance on the
    host that node stands on are two places on one machine, and an earlier version of this
    reported that as a shared *rack*, which named the wrong fix.

    Returns None for a single place, since that is the separate and more obvious finding.
    """
    places = world.out_edges(entity_id, RelationKind.RUNS_ON)
    if len(places) < 2:
        return None

    memo: dict[str, frozenset[str]] = {}
    shared: frozenset[str] | None = None
    for place in places:
        carries = frozenset({place}) | _carriers(world, place, memo, set())
        shared = carries if shared is None else (shared & carries)
    shared = (shared or frozenset()) - {entity_id}
    if not shared:
        return None

    # Only the nearest shared thing is worth saying: a service on one hypervisor is
    # necessarily also in one rack and one site, and reporting all three turns one defect
    # into three findings. "Nearest" is asked structurally rather than by counting hops —
    # the shared carriers form a chain, and the nearest one is the one that itself still
    # stands on the most. Hop counts answer this too until two places reach the same
    # carrier by paths of different lengths, and then they answer it differently depending
    # on which place you measure from. `sorted` makes the tie-break the name rather than
    # the hash seed.
    nearest = max(sorted(shared), key=lambda s: len(_carriers(world, s, memo, set())))
    kind = world.entity(nearest).kind
    if kind is EntityKind.SITE:
        # Everything in one datacentre is a fact about the estate rather than a defect, and
        # a finding on every service teaches people to skip the report.
        return None
    return nearest, len(places), kind


def audit(world: World) -> MapAudit:
    """Look for the shapes that usually mean the map is wrong rather than the estate."""
    a = MapAudit(entities=len(world), relations=len(world.relations()))
    racks = sum(1 for e in world.entities() if e.kind is EntityKind.RACK)

    for e in world.entities():
        srcs = {p.source for p in e.provenance}
        for s in srcs:
            a.sources[s] = a.sources.get(s, 0) + 1
        if len(srcs) > 1:
            a.cross_confirmed += 1
        elif len(srcs) == 1:
            a.single_sourced += 1
        else:
            a.findings.append(
                Finding("no provenance", e.id, "nothing records where this came from")
            )

        outgoing = sum(len(world.out_edges(e.id, k)) for k in RelationKind)
        incoming = sum(len(world.in_edges(e.id, k)) for k in RelationKind)
        if outgoing == 0 and incoming == 0:
            a.findings.append(
                Finding(
                    "isolated",
                    e.id,
                    "nothing connects to it — usually a join that failed, not a server nobody uses",
                )
            )
            continue

        if e.kind in _PLACEABLE and not world.out_edges(e.id, RelationKind.RUNS_ON):
            a.findings.append(
                Finding("no recorded placement", e.id, f"a {e.kind.value} with nowhere to run")
            )

        if e.kind is EntityKind.SERVICE:
            places = world.out_edges(e.id, RelationKind.RUNS_ON)
            declared = int(e.attrs.get("replicas", 0) or 0)
            if declared > 1 and len(places) == 1:
                a.findings.append(
                    Finding(
                        "redundancy on paper only",
                        e.id,
                        f"replicas={declared} but one place to run: losing it loses everything",
                    )
                )

        # Databases too, not services only. A primary and a replica on one hypervisor is
        # the oldest version of this defect and was silent here for a release.
        if e.kind in (EntityKind.SERVICE, EntityKind.DATABASE):
            concentrated = _shared_foundation(world, e.id)
            if concentrated:
                where, places, kind = concentrated
                if kind is EntityKind.RACK:
                    # Only when there is somewhere else to be. In an estate with one rack,
                    # "all in rack-1" is true of everything and is the same fact-about-the-
                    # estate that keeps `site` quiet — and it lands at the top of the
                    # report, because the report sorts by count.
                    if racks > 1:
                        a.findings.append(
                            Finding(
                                "redundancy in one rack",
                                e.id,
                                f"{places} places to run on different machines, all in "
                                f"{where} — one power feed, one top-of-rack switch",
                            )
                        )
                else:
                    a.findings.append(
                        Finding(
                            "redundancy on one machine",
                            e.id,
                            f"{places} places to run, all of them on {where} — "
                            f"losing it loses all of them",
                        )
                    )

        if e.kind is EntityKind.CLUSTER and e.attrs.get("quorum"):
            members = len(world.in_edges(e.id, RelationKind.MEMBER_OF))
            try:
                quorum = int(e.attrs["quorum"])
            except (TypeError, ValueError):
                a.findings.append(
                    Finding(
                        "quorum is not a number",
                        e.id,
                        f"quorum={e.attrs['quorum']!r} — propagation will refuse this",
                    )
                )
                continue
            if members < quorum:
                a.findings.append(
                    Finding(
                        "quorum unreachable",
                        e.id,
                        f"quorum {quorum} but only {members} members recorded",
                    )
                )

    for e in world.entities():
        if e.kind in _ROOTS:
            continue
        if not any(
            world.out_edges(e.id, k)
            for k in (RelationKind.HOSTED_IN, RelationKind.RUNS_ON, RelationKind.MEMBER_OF)
        ):
            depends_only = world.out_edges(e.id, RelationKind.DEPENDS_ON)
            if depends_only:
                a.findings.append(
                    Finding(
                        "floating",
                        e.id,
                        "depends on things but is not hosted anywhere",
                    )
                )

    return a


@dataclass
class Risk:
    entity_id: str
    kind: str
    name: str
    reach: int
    share: float

    def to_dict(self) -> dict:
        return {
            "id": self.entity_id,
            "kind": self.kind,
            "name": self.name,
            "reach": self.reach,
            "share": round(self.share, 4),
        }


def _reach_sizes(world: World) -> dict[str, int]:
    """How many entities each one takes with it, for every entity at once.

    The obvious implementation — a traversal per entity — costs the sum of all reaches,
    which on a real estate is dominated by a handful of hubs that each reach most of it.
    Measured, that was minutes on twenty-five thousand entities while the docstring
    claimed it was fine.

    So: collapse cycles into single nodes, then walk the resulting DAG once in reverse
    topological order, carrying each node's reachable set as a Python integer used as a
    bitset. Union becomes `|`, which runs in C over machine words instead of hashing one
    string at a time. Same answer, and the cost stops being the thing that decides
    whether anyone runs the command.
    """
    from orrery.sim.propagate import _DEPENDENT_EDGES, consequence_crosses

    ids = [e.id for e in world.entities()]
    index = {eid: i for i, eid in enumerate(ids)}

    # Edges point the way consequence travels: X -> everything that fails with X.
    g = nx.DiGraph()
    g.add_nodes_from(ids)
    for eid in ids:
        # Membership that does not carry death downward is not reach: a load balancer
        # dying leaves its pool running, and ranking it as if it took the pool with it
        # put it above things that really do.
        kinds = tuple(
            k for k in _DEPENDENT_EDGES if consequence_crosses(k, world.entity(eid).kind)
        )
        for dep in world.dependents(eid, kinds):
            g.add_edge(eid, dep)

    condensed = nx.condensation(g)
    mapping = condensed.graph["mapping"]
    members: dict[int, list[str]] = {}
    for eid, comp in mapping.items():
        members.setdefault(comp, []).append(eid)

    # Each component's bitset is up to one bit per entity, so holding all of them at once
    # costs O(V^2) bits — 2 GB at 127k entities, measured. A component is only needed
    # until every component that depends on it has folded it in, so they are counted and
    # dropped. Peak memory becomes the width of the frontier rather than the whole graph.
    remaining = {comp: condensed.in_degree(comp) for comp in condensed.nodes}
    bits: dict[int, int] = {}
    sizes: dict[str, int] = {}

    for comp in reversed(list(nx.topological_sort(condensed))):
        acc = 0
        for eid in members[comp]:
            acc |= 1 << index[eid]
        for succ in condensed.successors(comp):
            acc |= bits[succ]
            remaining[succ] -= 1
            if remaining[succ] == 0:
                del bits[succ]
        bits[comp] = acc
        for eid in members[comp]:
            # An entity does not count itself, and a cycle counts its peers but not itself.
            sizes[eid] = (acc & ~(1 << index[eid])).bit_count()

    return sizes


def single_points_of_failure(
    world: World, limit: int = 20, kinds: tuple[EntityKind, ...] | None = None
) -> list[Risk]:
    """Rank entities by how much goes with them.

    This is structural reach, not predicted damage — it deliberately does not run the
    behavior models. Redundancy is exactly what this list exists to find missing, and
    scoring with declared replicas in mind would quietly forgive the entity whose
    redundancy is recorded but not real.

    One pass over the whole graph regardless of how many entities are ranked, so
    narrowing by `kinds` filters the output rather than saving work.

    **Scale.** Measured on one laptop: 25k entities in 0.3 s and 130 MB, 127k in 5.5 s and
    1.3 GB. The memory is the binding limit, not the time — reach is held as one bit per
    entity per component, so it grows with the square of the estate. Bitsets are freed as
    soon as everything that depends on them has folded them in, which roughly halves the
    peak, but beyond a few hundred thousand entities this needs a different algorithm
    rather than a smaller constant.
    """
    total = max(1, len(world) - 1)
    sizes = _reach_sizes(world)
    out = [
        Risk(e.id, e.kind.value, e.name, sizes[e.id], sizes[e.id] / total)
        for e in world.entities()
        if sizes.get(e.id) and not (kinds and e.kind not in kinds)
    ]
    out.sort(key=lambda r: (-r.reach, r.entity_id))
    return out[:limit]


def format_risks(risks: list[Risk], total_entities: int) -> str:
    if not risks:
        return "nothing reaches anything else — the map has no dependency edges yet"
    lines = [f"single points of failure, by what goes with them ({total_entities:,} entities)", ""]
    width = max(len(r.entity_id) for r in risks)
    for i, r in enumerate(risks, 1):
        lines.append(
            f"  {i:>3}. {r.entity_id:<{width}}  {r.reach:>6,} ({r.share * 100:4.1f}%)  {r.kind}"
        )
    return "\n".join(lines)
