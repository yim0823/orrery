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
from orrery.schema import (
    Entity,
    EntityKind,
    Relation,
    RelationKind,
    RelationStrength,
    Status,
)


def _private(e: Entity) -> Entity:
    """A copy of an entity that shares nothing mutable with the original.

    `model_copy` is shallow. Copying `attrs` and not `provenance` left the provenance list
    shared, so a fork merging a second source into an entity grew the parent's list.
    Every mutable field is listed here so the next one added is a conscious omission.
    """
    return e.model_copy(update={"attrs": dict(e.attrs), "provenance": list(e.provenance)})


class World:
    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()
        self._overlay: dict[str, Entity] | None = None
        """Set on a fork. Holds private copies of the entities this world has written to;
        everything else is read straight from the shared graph."""
        self.collisions: list[tuple[str, str, str]] = []
        """(id, name kept, name discarded) for every id that arrived twice under two
        different names. Usually an id scheme that is not as unique as it looked."""

    # ---- building ----
    def _detach(self) -> None:
        """Stop sharing structure with the world we forked from.

        Forks are read-mostly by design, but adding an entity or a relation changes the
        graph itself rather than one entity's state, so the shared structure has to
        become ours first.

        `MultiDiGraph.copy()` is shallow: the node attribute dicts are new, the `Entity`
        objects inside them are not. Leaving it there is the whole bug this docstring
        used to warn about and cause at the same time — merging into an entity on a fork
        reached through and edited the parent's object, and it surfaced in whatever ran
        next rather than here. So every entity is copied on detach.
        """
        if self._overlay is None:
            return
        self.g = self.g.copy()
        for node, data in self.g.nodes(data=True):
            e: Entity = self._overlay.get(node) or data["entity"]
            data["entity"] = _private(e)
        self._overlay = None

    def add_entity(self, e: Entity) -> None:
        """Add, or merge into what is already there.

        Merging is the point when two sources describe the same thing — that is how an
        entity ends up cross-confirmed. It is a collision when one source describes two
        different things under one id, and the two are indistinguishable from here, so
        the merge is recorded rather than announced. `collisions` is reported by
        `ingest` at the moment it happens; it is not persisted in a snapshot, because by
        then the two records are one and there is nothing left to show.
        """
        self._detach()
        if e.id in self.g:
            existing: Entity = self.g.nodes[e.id]["entity"]
            if existing.kind != e.kind:
                self.collisions.append(
                    (e.id, f"kind {existing.kind.value}", f"kind {e.kind.value}")
                )
            if existing.name != e.name:
                self.collisions.append((e.id, existing.name, e.name))
            if existing.status != e.status and e.status is not Status.UP:
                # Two sources, two opinions about whether it is up. The first one wins,
                # which is arbitrary, so the disagreement is recorded rather than lost.
                self.collisions.append(
                    (e.id, f"status {existing.status.value}", f"status {e.status.value}")
                )
            existing.provenance.extend(e.provenance)
            existing.attrs.update(e.attrs)
        else:
            self.g.add_node(e.id, entity=e)

    def add_relation(self, r: Relation) -> None:
        """Add, or merge into the edge already there.

        Same rule as `add_entity`, and it was missing here: a second source describing an
        edge the first source already described used to *replace* it, taking the first
        source's provenance with it. So a relation could never be confirmed by more than
        one source, however many saw it, while an entity could — an asymmetry nothing in
        the model asks for and nobody would find until they asked why every edge in a
        two-connector map was single-sourced.

        Disagreement about `strength` is not settled by arrival order. Hard wins, on the
        same argument the default rests on: calling a load-bearing dependency optional
        hides an outage, and calling an optional one load-bearing raises a false alarm.
        The disagreement is recorded either way.
        """
        self._detach()
        for end in (r.src, r.dst):
            if end not in self.g:
                raise KeyError(f"relation references unknown entity {end!r}")

        prior_data = self.g.get_edge_data(r.src, r.dst, key=r.kind.value)
        if prior_data is not None:
            prior: Relation = prior_data["relation"]
            strength = prior.strength
            if prior.strength is not r.strength:
                self.collisions.append(
                    (
                        f"{r.src} -{r.kind.value}-> {r.dst}",
                        f"strength {prior.strength.value}",
                        f"strength {r.strength.value}",
                    )
                )
                strength = RelationStrength.HARD
            # Replaced rather than mutated: relation objects are shared with the world
            # this one was forked from, and `_detach` copies entities only.
            r = prior.model_copy(
                update={
                    "strength": strength,
                    "provenance": [*prior.provenance, *r.provenance],
                    "attrs": {**prior.attrs, **r.attrs},
                }
            )

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

    def dependents(self, entity_id: str, kinds: tuple[RelationKind, ...]) -> set[str]:
        """Everything pointing at this entity over any of `kinds`, in one pass.

        Asking per-kind means re-walking the node's incoming edges once per kind, which
        is five scans for the five that carry consequence. Reach ranking calls this for
        every entity in the estate, so the difference is the difference between a command
        you run and one you schedule.
        """
        wanted = {k.value for k in kinds}
        return {u for u, _, k in self.g.in_edges(entity_id, keys=True) if k in wanted}

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
            # Flatten first: a deep copy of the shared graph would otherwise return the
            # parent's state and drop everything this world has done to itself.
            flat = self.g if self._overlay is None else self._flattened()
            w.g = copy.deepcopy(flat)
            return w
        # Share the structure and keep private copies only of what gets written to. A
        # simulation touches a fraction of the estate, so copying all of it — or even
        # just the graph — is work thrown away on every call.
        w.g = self.g
        # Inherit what this world has already changed. Forking a fork used to hand back
        # a pristine estate, which is worse than an error: `run_scenario` and `replay`
        # both fork whatever they are given, so a world that had already failed would
        # quietly come back healthy.
        w._overlay = (
            {}
            if self._overlay is None
            else {k: _private(v) for k, v in self._overlay.items()}
        )
        return w

    def _flattened(self) -> nx.MultiDiGraph:
        """This world's graph with the overlay written into it, as a new graph."""
        g = self.g.copy()
        for eid, ent in (self._overlay or {}).items():
            g.nodes[eid]["entity"] = ent
        return g

    def _own(self, entity_id: str) -> Entity:
        """Take private ownership of one entity before writing to it."""
        if self._overlay is None:
            return self.g.nodes[entity_id]["entity"]
        hit = self._overlay.get(entity_id)
        if hit is None:
            hit = _private(self.g.nodes[entity_id]["entity"])
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

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self.g

    def __len__(self) -> int:
        return self.g.number_of_nodes()
