"""The adapter is tested against a fake driver.

A real Neo4j would make these tests slow and skippable, and skippable tests do not get
run. What matters here is the mapping and the paging, both of which are ours.
"""
from __future__ import annotations

from orrery.adapters.neo4j import LabelMap, Neo4jSource
from orrery.schema import EntityKind, RelationKind, RelationStrength, Status
from orrery.sim import Event, propagate


class FakeSession:
    def __init__(self, nodes, rels):
        self.nodes, self.rels = nodes, rels
        self.queries: list[str] = []

    def run(self, query, **params):
        self.queries.append(query)
        skip, limit = params["skip"], params["limit"]
        if "MATCH (n)" in query:
            rows = [
                {"labels": n["labels"], "props": n["props"]}
                for n in self.nodes
                if any(label in params["labels"] for label in n["labels"])
            ]
        else:
            rows = [r for r in self.rels if r["type"] in params["types"]]
        return rows[skip : skip + limit]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, nodes, rels):
        self.session_obj = FakeSession(nodes, rels)
        self.kwargs = None

    def session(self, **kwargs):
        self.kwargs = kwargs
        return self.session_obj


NODES = [
    {"labels": ["Server"], "props": {"id": "h1", "name": "host one", "env": "prod", "noise": "x"}},
    {"labels": ["Server"], "props": {"id": "h2", "name": "host two", "env": "prod"}},
    {"labels": ["App"], "props": {"id": "a1", "name": "app one", "replicas": 2}},
    {"labels": ["App"], "props": {"id": "a2", "name": "app two", "replicas": 1}},
    {"labels": ["Ignored"], "props": {"id": "zz", "name": "not mapped"}},
    {"labels": ["Server"], "props": {"name": "no id at all"}},
]
RELS = [
    {"src": "a1", "dst": "h1", "type": "DEPLOYED_ON", "props": {}},
    {"src": "a2", "dst": "h2", "type": "DEPLOYED_ON", "props": {}},
    {"src": "a1", "dst": "a2", "type": "CALLS", "props": {"strength": "soft"}},
    {"src": "a1", "dst": "zz", "type": "UNMAPPED", "props": {}},
]

LABELS = LabelMap(
    entity_labels={"Server": EntityKind.HOST, "App": EntityKind.SERVICE},
    relation_types={"DEPLOYED_ON": RelationKind.RUNS_ON, "CALLS": RelationKind.DEPENDS_ON},
    attr_properties={"Server": ["env"], "App": ["replicas"]},
)


def _source(**kw) -> Neo4jSource:
    return Neo4jSource(FakeDriver(NODES, RELS), LABELS, **kw)


def test_only_mapped_labels_become_entities():
    d = _source().discover()
    assert {e.id for e in d.entities} == {"h1", "h2", "a1", "a2"}


def test_a_node_without_an_id_is_skipped_not_guessed_at():
    d = _source().discover()
    assert all(e.id for e in d.entities)


def test_only_listed_attributes_are_carried():
    d = _source().discover()
    h1 = next(e for e in d.entities if e.id == "h1")
    assert h1.attrs == {"env": "prod"}  # "noise" is not in attr_properties


def test_unmapped_relationship_types_are_skipped():
    d = _source().discover()
    assert {(r.src, r.dst) for r in d.relations} == {("a1", "h1"), ("a2", "h2"), ("a1", "a2")}


def test_strength_is_read_from_the_relationship():
    d = _source().discover()
    call = next(r for r in d.relations if r.kind is RelationKind.DEPENDS_ON)
    assert call.strength is RelationStrength.SOFT


def test_an_unrecognized_strength_stays_hard():
    rels = [{"src": "a1", "dst": "a2", "type": "CALLS", "props": {"strength": "maybe?"}}]
    src = Neo4jSource(FakeDriver(NODES, rels), LABELS)
    rel = next(r for r in src.discover().relations if r.kind is RelationKind.DEPENDS_ON)
    assert rel.strength is RelationStrength.HARD


def test_provenance_records_where_each_fact_came_from():
    d = _source(name="cmdb").discover()
    assert all(e.provenance and e.provenance[0].source == "cmdb" for e in d.entities)


def test_large_graphs_are_paged():
    src = _source()
    src.batch_size = 2
    src.discover()
    # 4 entities at 2 per page needs 3 round trips (the last one proves exhaustion)
    entity_queries = [q for q in src.driver.session_obj.queries if "MATCH (n)" in q]
    assert len(entity_queries) >= 3


def test_the_database_name_is_passed_through_when_given():
    src = _source(database="inventory")
    src.discover()
    assert src.driver.kwargs == {"database": "inventory"}


def test_the_loaded_world_actually_computes():
    # The point of the adapter is that the rest of the engine works on what it returns.
    w = _source().load().fork()
    propagate(w, Event("h2", "down"))
    assert w.entity("a2").status is Status.DOWN      # its only host died
    assert w.entity("a1").status is Status.DEGRADED  # soft caller of a2
