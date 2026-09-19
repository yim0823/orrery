"""Read a world out of an existing property graph.

Most organizations that would use orrery already have a graph: a CMDB, an asset
inventory, something built on Neo4j. Standing up a second store beside it is the worst
available option — two sources of truth diverge, and once they disagree neither is
trusted. So orrery reads from the graph that already exists and computes over it.

Nothing here knows any organization's labels or relationship types. You supply a mapping
from yours to orrery's, which is also the moment you find out that two systems have been
calling the same relationship different things.

    from orrery.adapters.neo4j import Neo4jSource, LabelMap

    src = Neo4jSource(
        driver,
        LabelMap(
            entity_labels={"Server": EntityKind.HOST, "App": EntityKind.SERVICE},
            relation_types={"DEPLOYED_ON": RelationKind.RUNS_ON},
        ),
    )
    world = src.load()

The neo4j driver is an optional dependency: `pip install orrery[neo4j]`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from orrery.connectors.base import Discovery
from orrery.schema import (
    Entity,
    EntityKind,
    Provenance,
    Relation,
    RelationKind,
    RelationStrength,
)
from orrery.world import World


class _Session(Protocol):
    def run(self, query: str, **params: Any) -> Iterable[Mapping[str, Any]]: ...


class _Driver(Protocol):
    def session(self, **kwargs: Any) -> Any: ...


@dataclass
class LabelMap:
    """How your graph's vocabulary maps onto orrery's.

    Anything not mentioned is skipped rather than guessed at. A silent guess here would
    put a wrong edge in the map, and a wrong edge is worse than a missing one: it answers
    confidently.
    """

    entity_labels: dict[str, EntityKind] = field(default_factory=dict)
    relation_types: dict[str, RelationKind] = field(default_factory=dict)

    id_property: str = "id"
    """Node property holding a stable identifier. Must be unique across the graph."""

    name_property: str = "name"

    strength_property: str | None = "strength"
    """Relationship property holding "hard" or "soft". None to treat every edge as hard."""

    attr_properties: dict[str, list[str]] = field(default_factory=dict)
    """Per-label list of node properties to carry into `attrs`.

    Keep it short. Copying every property makes snapshots large and diffs noisy, and the
    only properties that change an answer are the ones a behavior model reads.
    """

    def kind_for(self, labels: Iterable[str]) -> EntityKind | None:
        for label in labels:
            if label in self.entity_labels:
                return self.entity_labels[label]
        return None


@dataclass
class Neo4jSource:
    """Pulls entities and relations out of a Bolt-speaking graph.

    Read-only by construction: it issues MATCH and nothing else. Give it a read-only
    role anyway — a connector that could write is a connector that will, eventually,
    on the wrong database.
    """

    driver: _Driver
    labels: LabelMap
    database: str | None = None
    batch_size: int = 10_000
    name: str = "neo4j"

    def discover(self) -> Discovery:
        d = Discovery()
        with self.driver.session(**({"database": self.database} if self.database else {})) as s:
            d.entities.extend(self._entities(s))
            d.relations.extend(self._relations(s))
        return d

    def load(self) -> World:
        w = World()
        w.ingest(self.discover())
        return w

    # ---- internals ----

    def _entities(self, session: _Session) -> list[Entity]:
        out: list[Entity] = []
        wanted = list(self.labels.entity_labels)
        if not wanted:
            return out
        query = (
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
            "RETURN labels(n) AS labels, properties(n) AS props "
            # SKIP/LIMIT across separate queries is only stable under an explicit order.
            # Without one, a concurrent write or a changed plan silently duplicates or
            # drops rows, and the result is a map with holes nobody can account for.
            "ORDER BY n[$idp] SKIP $skip LIMIT $limit"
        )
        for row in self._paged(session, query, labels=wanted, idp=self.labels.id_property):
            props = dict(row["props"])
            eid = props.get(self.labels.id_property)
            kind = self.labels.kind_for(row["labels"])
            if eid is None or kind is None:
                continue
            keep = self._attr_keys(row["labels"])
            out.append(
                Entity(
                    id=str(eid),
                    kind=kind,
                    name=str(props.get(self.labels.name_property, eid)),
                    attrs={k: props[k] for k in keep if k in props},
                    provenance=[Provenance(source=self.name, source_id=str(eid))],
                )
            )
        return out

    def _relations(self, session: _Session) -> list[Relation]:
        out: list[Relation] = []
        wanted = list(self.labels.relation_types)
        if not wanted:
            return out
        query = (
            "MATCH (a)-[r]->(b) WHERE type(r) IN $types "
            "AND a[$idp] IS NOT NULL AND b[$idp] IS NOT NULL "
            "RETURN a[$idp] AS src, b[$idp] AS dst, type(r) AS type, properties(r) AS props "
            "ORDER BY src, dst, type SKIP $skip LIMIT $limit"
        )
        for row in self._paged(session, query, types=wanted, idp=self.labels.id_property):
            kind = self.labels.relation_types.get(row["type"])
            if kind is None:
                continue
            props = dict(row["props"])
            out.append(
                Relation(
                    src=str(row["src"]),
                    dst=str(row["dst"]),
                    kind=kind,
                    strength=self._strength(props),
                    attrs=props,
                    provenance=[Provenance(source=self.name, source_id=row["type"])],
                )
            )
        return out

    def _strength(self, props: Mapping[str, Any]) -> RelationStrength:
        if not self.labels.strength_property:
            return RelationStrength.HARD
        raw = props.get(self.labels.strength_property)
        if isinstance(raw, str) and raw.lower() == "soft":
            return RelationStrength.SOFT
        # Anything unrecognized stays hard. Reading an unexpected value as "soft" would
        # quietly hide outages; reading it as "hard" only produces false alarms.
        return RelationStrength.HARD

    def _attr_keys(self, labels: Iterable[str]) -> list[str]:
        keys: list[str] = []
        for label in labels:
            keys.extend(self.labels.attr_properties.get(label, []))
        return keys

    def _paged(self, session: _Session, query: str, **params: Any) -> Iterable[Mapping[str, Any]]:
        """Page through results so a large graph does not arrive as one object.

        Real inventories run to hundreds of thousands of nodes. Asking for all of them in
        one result set is how this becomes the thing that takes the database down.
        """
        skip = 0
        while True:
            rows = list(session.run(query, skip=skip, limit=self.batch_size, **params))
            yield from rows
            if len(rows) < self.batch_size:
                return
            skip += self.batch_size
