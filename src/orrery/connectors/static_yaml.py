"""A connector that reads a YAML fixture. Used for synthetic worlds and tests."""
from __future__ import annotations

import pathlib

import yaml

from orrery.schema import Entity, Provenance, Relation

from .base import Discovery


class StaticYamlConnector:
    name = "static_yaml"

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)

    def discover(self) -> Discovery:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        prov = [Provenance(source=self.name, source_id=str(self.path))]
        ents = [Entity(**e, provenance=prov) for e in data.get("entities", [])]
        rels = [Relation(**r, provenance=prov) for r in data.get("relations", [])]
        return Discovery(entities=ents, relations=rels)
