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

    # ---- building ----
    def add_entity(self, e: Entity) -> None:
        if e.id in self.g:
            existing: Entity = self.g.nodes[e.id]["entity"]
            existing.provenance.extend(e.provenance)
            existing.attrs.update(e.attrs)
        else:
            self.g.add_node(e.id, entity=e)

    def add_relation(self, r: Relation) -> None:
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
        return self.g.nodes[entity_id]["entity"]

    def entities(self, kind: EntityKind | None = None) -> list[Entity]:
        out = [d["entity"] for _, d in self.g.nodes(data=True)]
        return [e for e in out if kind is None or e.kind == kind]

    def relations(self, kind: RelationKind | None = None) -> list[Relation]:
        out = [d["relation"] for _, _, d in self.g.edges(data=True)]
        return [r for r in out if kind is None or r.kind == kind]

    def out_edges(self, entity_id: str, kind: RelationKind) -> list[str]:
        return [v for _, v, k in self.g.out_edges(entity_id, keys=True) if k == kind.value]

    def in_edges(self, entity_id: str, kind: RelationKind) -> list[str]:
        return [u for u, _, k in self.g.in_edges(entity_id, keys=True) if k == kind.value]

    # ---- state ----
    def set_status(self, entity_id: str, status: Status) -> None:
        self.entity(entity_id).status = status

    # ---- persistence: snapshot / fork ----
    def fork(self) -> "World":
        w = World()
        w.g = copy.deepcopy(self.g)
        return w

    def to_dict(self) -> dict:
        return {
            "entities": [e.model_dump(mode="json") for e in self.entities()],
            "relations": [r.model_dump(mode="json") for r in self.relations()],
        }

    def save(self, path: str | pathlib.Path) -> None:
        pathlib.Path(path).write_text(yaml.safe_dump(self.to_dict(), allow_unicode=True), "utf-8")

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "World":
        data = yaml.safe_load(pathlib.Path(path).read_text("utf-8"))
        w = cls()
        for e in data["entities"]:
            w.add_entity(Entity(**e))
        for r in data["relations"]:
            w.add_relation(Relation(**r))
        return w

    def __len__(self) -> int:
        return self.g.number_of_nodes()
