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

_PLACEABLE = (EntityKind.SERVICE, EntityKind.DATABASE, EntityKind.NODE)
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


def audit(world: World) -> MapAudit:
    """Look for the shapes that usually mean the map is wrong rather than the estate."""
    a = MapAudit(entities=len(world), relations=len(world.relations()))

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
    from orrery.sim.propagate import _DEPENDENT_EDGES

    ids = [e.id for e in world.entities()]
    index = {eid: i for i, eid in enumerate(ids)}

    # Edges point the way consequence travels: X -> everything that fails with X.
    g = nx.DiGraph()
    g.add_nodes_from(ids)
    for eid in ids:
        for dep in world.dependents(eid, _DEPENDENT_EDGES):
            g.add_edge(eid, dep)

    condensed = nx.condensation(g)
    members: dict[int, list[str]] = {}
    for eid, comp in condensed.graph["mapping"].items():
        members.setdefault(comp, []).append(eid)

    bits: dict[int, int] = {}
    for comp in reversed(list(nx.topological_sort(condensed))):
        own = 0
        for eid in members[comp]:
            own |= 1 << index[eid]
        acc = own
        for succ in condensed.successors(comp):
            acc |= bits[succ]
        bits[comp] = acc

    mapping = condensed.graph["mapping"]
    sizes: dict[str, int] = {}
    for eid in ids:
        # An entity does not count itself, and a cycle counts its peers but not itself.
        sizes[eid] = (bits[mapping[eid]] & ~(1 << index[eid])).bit_count()
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
