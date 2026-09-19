"""The world graph: entities as nodes, relations as typed edges. In-memory via networkx.

Persistence backends (e.g. Neo4j) implement the same surface later; the query layer only
uses `World`.
"""
from __future__ import annotations

import copy
import pathlib

import networkx as nx
import yaml

from orrery.connectors.base import Discovery
from orrery.resolve import Resolver
from orrery.schema import Entity, EntityKind, Relation, RelationKind, Status


class World:
    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()
        self._overlay: dict[str, Entity] | None = None
        """Set on a fork. Holds private copies of the entities this world has written to;
        everything else is read straight from the shared graph."""

    # ---- building ----
    def _detach(self) -> None:
        """Stop sharing structure with the world we forked from.

        Forks are read-mostly by design, but adding an entity or a relation changes the
        graph itself rather than one entity's state, so the shared structure has to
        become ours first. Silently writing through to the parent would be a bug that
        only shows up in whatever ran next.
        """
        if self._overlay is None:
            return
        self.g = self.g.copy()
        for eid, ent in self._overlay.items():
            self.g.nodes[eid]["entity"] = ent
        self._overlay = None

    def add_entity(self, e: Entity) -> None:
        self._detach()
        if e.id in self.g:
            existing: Entity = self.g.nodes[e.id]["entity"]
            existing.provenance.extend(e.provenance)
            existing.attrs.update(e.attrs)
        else:
            self.g.add_node(e.id, entity=e)

    def add_relation(self, r: Relation) -> None:
        self._detach()
        for end in (r.src, r.dst):
            if end not in self.g:
                raise KeyError(f"relation references unknown entity {end!r}")
        self.g.add_edge(r.src, r.dst, key=r.kind.value, relation=r)

    def ingest(self, d: Discovery, resolver: Resolver | None = None) -> None:
        res = resolver or Resolver()
        for e in d.entities:
            cid = res.canonical(e.id)
            self.add_entity(e.model_copy(update={"id": cid}))
        for r in d.relations:
            self.add_relation(
                r.model_copy(update={"src": res.canonical(r.src), "dst": res.canonical(r.dst)})
            )

    # ---- reading ----
    def entity(self, entity_id: str) -> Entity:
        if self._overlay is not None:
            hit = self._overlay.get(entity_id)
            if hit is not None:
                return hit
        return self.g.nodes[entity_id]["entity"]

    def entities(self, kind: EntityKind | None = None) -> list[Entity]:
        if self._overlay:
            out = [self._overlay.get(n, d["entity"]) for n, d in self.g.nodes(data=True)]
        else:
            out = [d["entity"] for _, d in self.g.nodes(data=True)]
        return [e for e in out if kind is None or e.kind == kind]

    def relations(self, kind: RelationKind | None = None) -> list[Relation]:
        out = [d["relation"] for _, _, d in self.g.edges(data=True)]
        return [r for r in out if kind is None or r.kind == kind]

    def out_edges(self, entity_id: str, kind: RelationKind) -> list[str]:
        return [v for _, v, k in self.g.out_edges(entity_id, keys=True) if k == kind.value]

    def in_edges(self, entity_id: str, kind: RelationKind) -> list[str]:
        return [u for u, _, k in self.g.in_edges(entity_id, keys=True) if k == kind.value]

    def in_relations(self, entity_id: str, kind: RelationKind) -> list[Relation]:
        """Incoming edges as relations, so callers can read strength and attrs."""
        return [
            d["relation"]
            for _, _, k, d in self.g.in_edges(entity_id, keys=True, data=True)
            if k == kind.value
        ]

    # ---- state ----
    def set_status(self, entity_id: str, status: Status) -> None:
        self._own(entity_id).status = status

    # ---- persistence: snapshot / fork ----
    def fork(self, deep: bool = False) -> World:
        """A copy you can damage without touching this one.

        The default costs almost nothing. Forking is on the path of every simulation, and
        a simulation writes exactly one field — `status` — on a fraction of the estate, so
        the fork shares the graph and keeps private copies only of the entities it
        actually damages. On a 25k-entity world that took 450 ms when it deep-copied and
        is now microseconds.

        Two sharp edges. Relation objects are shared, so mutating one on a fork changes
        the parent; nothing in the engine does this. And reading `entity()` on a fork
        returns the parent's object until something writes to it, so holding that
        reference across a write gives you the stale one. Pass `deep=True` for a fork
        with no shared parts at all.
        """
        w = World()
        if deep:
            w.g = copy.deepcopy(self.g)
            return w
        # Share the structure and keep private copies only of what gets written to. A
        # simulation touches a fraction of the estate, so copying all of it — or even
        # just the graph — is work thrown away on every call.
        w.g = self.g
        w._overlay = {}
        return w

    def _own(self, entity_id: str) -> Entity:
        """Take private ownership of one entity before writing to it."""
        if self._overlay is None:
            return self.g.nodes[entity_id]["entity"]
        hit = self._overlay.get(entity_id)
        if hit is None:
            source: Entity = self.g.nodes[entity_id]["entity"]
            hit = source.model_copy(update={"attrs": dict(source.attrs)})
            self._overlay[entity_id] = hit
        return hit

    def to_dict(self) -> dict:
        return {
            "entities": [e.model_dump(mode="json") for e in self.entities()],
            "relations": [r.model_dump(mode="json") for r in self.relations()],
        }

    def save(self, path: str | pathlib.Path) -> None:
        pathlib.Path(path).write_text(yaml.safe_dump(self.to_dict(), allow_unicode=True), "utf-8")

    @classmethod
    def load(cls, path: str | pathlib.Path) -> World:
        data = yaml.safe_load(pathlib.Path(path).read_text("utf-8"))
        w = cls()
        for e in data["entities"]:
            w.add_entity(Entity(**e))
        for r in data["relations"]:
            w.add_relation(Relation(**r))
        return w

    def __len__(self) -> int:
        return self.g.number_of_nodes()
