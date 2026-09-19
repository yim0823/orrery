"""What changed between two snapshots of the world.

Every inventory system decays, and it decays quietly: a host is decommissioned and the
record stays, a dependency is added in a deploy and nobody writes it down. The decay is
invisible until an outage, when the map turns out to describe a company that no longer
exists. A diff between snapshots is the cheapest way to see it happening.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from orrery.schema import Entity, Relation

from .graph import World


def _rel_key(r: Relation) -> tuple[str, str, str]:
    return (r.src, r.dst, r.kind.value)


@dataclass
class EntityChange:
    id: str
    field: str
    before: object
    after: object


@dataclass
class WorldDiff:
    added_entities: list[Entity] = field(default_factory=list)
    removed_entities: list[Entity] = field(default_factory=list)
    changed_entities: list[EntityChange] = field(default_factory=list)
    added_relations: list[Relation] = field(default_factory=list)
    removed_relations: list[Relation] = field(default_factory=list)
    changed_relations: list[EntityChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.added_entities,
                self.removed_entities,
                self.changed_entities,
                self.added_relations,
                self.removed_relations,
                self.changed_relations,
            )
        )

    def to_dict(self) -> dict:
        return {
            "entities": {
                "added": [e.model_dump(mode="json") for e in self.added_entities],
                "removed": [e.model_dump(mode="json") for e in self.removed_entities],
                "changed": [
                    {"id": c.id, "field": c.field, "before": c.before, "after": c.after}
                    for c in self.changed_entities
                ],
            },
            "relations": {
                "added": [r.model_dump(mode="json") for r in self.added_relations],
                "removed": [r.model_dump(mode="json") for r in self.removed_relations],
                "changed": [
                    {"id": c.id, "field": c.field, "before": c.before, "after": c.after}
                    for c in self.changed_relations
                ],
            },
        }

    def summary(self) -> str:
        if self.empty:
            return "no change"
        lines: list[str] = []

        def block(title: str, items: list[str]) -> None:
            if items:
                lines.append(f"{title} ({len(items)})")
                lines.extend(f"  {i}" for i in items[:40])
                if len(items) > 40:
                    lines.append(f"  ... and {len(items) - 40} more")
                lines.append("")

        block("appeared", [f"{e.id} ({e.kind.value}) {e.name}" for e in self.added_entities])
        block("vanished", [f"{e.id} ({e.kind.value}) {e.name}" for e in self.removed_entities])
        block(
            "changed",
            [f"{c.id}: {c.field} {c.before!r} -> {c.after!r}" for c in self.changed_entities],
        )
        block(
            "wired",
            [f"{r.src} -{r.kind.value}-> {r.dst}" for r in self.added_relations],
        )
        block(
            "unwired",
            [f"{r.src} -{r.kind.value}-> {r.dst}" for r in self.removed_relations],
        )
        block(
            "rewired",
            [f"{c.id}: {c.field} {c.before!r} -> {c.after!r}" for c in self.changed_relations],
        )
        return "\n".join(lines).rstrip()


def diff(before: World, after: World) -> WorldDiff:
    """Compare two snapshots.

    Status is deliberately not compared. It is runtime state that changes every minute,
    and mixing it in would bury the structural drift this exists to surface.
    """
    d = WorldDiff()

    old = {e.id: e for e in before.entities()}
    new = {e.id: e for e in after.entities()}

    d.added_entities = [new[i] for i in sorted(new.keys() - old.keys())]
    d.removed_entities = [old[i] for i in sorted(old.keys() - new.keys())]

    for eid in sorted(old.keys() & new.keys()):
        a, b = old[eid], new[eid]
        if a.kind != b.kind:
            d.changed_entities.append(EntityChange(eid, "kind", a.kind.value, b.kind.value))
        if a.name != b.name:
            d.changed_entities.append(EntityChange(eid, "name", a.name, b.name))
        for key in sorted(set(a.attrs) | set(b.attrs)):
            if a.attrs.get(key) != b.attrs.get(key):
                d.changed_entities.append(
                    EntityChange(eid, f"attrs.{key}", a.attrs.get(key), b.attrs.get(key))
                )

    old_r = {_rel_key(r): r for r in before.relations()}
    new_r = {_rel_key(r): r for r in after.relations()}

    d.added_relations = [new_r[k] for k in sorted(new_r.keys() - old_r.keys())]
    d.removed_relations = [old_r[k] for k in sorted(old_r.keys() - new_r.keys())]

    for key in sorted(old_r.keys() & new_r.keys()):
        a, b = old_r[key], new_r[key]
        label = f"{a.src} -{a.kind.value}-> {a.dst}"
        if a.strength != b.strength:
            d.changed_relations.append(
                EntityChange(label, "strength", a.strength.value, b.strength.value)
            )
        for k in sorted(set(a.attrs) | set(b.attrs)):
            if a.attrs.get(k) != b.attrs.get(k):
                d.changed_relations.append(
                    EntityChange(label, f"attrs.{k}", a.attrs.get(k), b.attrs.get(k))
                )

    return d
