"""A connector that reads a YAML fixture. Used for synthetic worlds and tests.

It also reads what `World.save` writes, which is not a coincidence: the quickest way to
try a change is to ingest a snapshot, edit it, and ingest it again. An earlier version
crashed on its own output because it added provenance to records that already had some.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml

from orrery.schema import Entity, Provenance, Relation

from .base import Discovery


class StaticYamlConnector:
    name = "static_yaml"

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)

    def discover(self) -> Discovery:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if raw is None:
            return Discovery()
        if not isinstance(raw, dict):
            # ValueError, not TypeError: nobody passed a wrong argument type. Someone's
            # file has the wrong contents, and the message is for them.
            raise ValueError(  # noqa: TRY004
                f"{self.path}: expected a mapping with `entities` and `relations` keys, "
                f"got {type(raw).__name__}"
            )
        prov = Provenance(source=self.name, source_id=str(self.path))

        ents = [self._build(Entity, e, prov, "entity", i) for i, e in enumerate(raw.get("entities") or [])]
        rels = [self._build(Relation, r, prov, "relation", i) for i, r in enumerate(raw.get("relations") or [])]
        return Discovery(entities=ents, relations=rels)

    def _build(self, cls, record: Any, prov: Provenance, what: str, index: int):
        if not isinstance(record, dict):
            raise ValueError(  # noqa: TRY004 - malformed data, not a bad argument type
                f"{self.path}: {what} #{index} is not a mapping: {record!r}"
            )
        data = dict(record)
        # Keep provenance the file already carries; a round-trip should not forget where
        # a fact originally came from, and it must not crash on the duplicate key either.
        existing = data.pop("provenance", None) or []
        try:
            return cls(**data, provenance=[*existing, prov])
        except TypeError as exc:
            raise ValueError(f"{self.path}: {what} #{index} {record!r}: {exc}") from exc
