"""The third adversarial pass, which was aimed at four proposed features and found two
defects instead.

Neither defect was in the proposals. Both were asymmetries: one code path doing the right
thing and its twin quietly not. That is twice now — `blast` and `simulate` disagreeing
about a network segment in 0.2.0, and these two — which is worth saying out loud, because
the lesson is not "write more tests" but "when two places implement one rule, one of them
is wrong and no test will tell you which".
"""
from __future__ import annotations

from orrery.connectors import StaticYamlConnector
from orrery.schema import (
    Entity,
    EntityKind,
    Provenance,
    Relation,
    RelationKind,
    RelationStrength,
)
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius, single_points_of_failure


def _two_hosts() -> World:
    w = World()
    for i in ("a", "b"):
        w.add_entity(Entity(id=i, kind=EntityKind.HOST, name=i))
    return w


def _edge(source: str, strength: RelationStrength = RelationStrength.HARD) -> Relation:
    return Relation(
        src="a",
        dst="b",
        kind=RelationKind.DEPENDS_ON,
        strength=strength,
        provenance=[Provenance(source=source, source_id="1")],
    )


# ---- a relation could never be confirmed by a second source ----


def test_two_sources_describing_one_edge_both_end_up_on_it():
    """`add_entity` merges provenance and says in its docstring that merging is the point,
    because that is how something becomes cross-confirmed. `add_relation` replaced
    instead, so the first source's record was dropped and every edge in a two-connector
    map was single-sourced however many connectors saw it."""
    w = _two_hosts()
    w.add_relation(_edge("cmdb"))
    w.add_relation(_edge("flows"))

    assert len(w.relations()) == 1
    assert [p.source for p in w.relations()[0].provenance] == ["cmdb", "flows"]


def test_the_two_add_methods_agree_about_what_a_second_source_means():
    w = _two_hosts()
    w.add_entity(Entity(id="a", kind=EntityKind.HOST, name="a", provenance=[]))
    for source in ("cmdb", "flows"):
        w.add_entity(
            Entity(
                id="a",
                kind=EntityKind.HOST,
                name="a",
                provenance=[Provenance(source=source, source_id="a")],
            )
        )
        w.add_relation(_edge(source))

    entity_sources = [p.source for p in w.entity("a").provenance]
    relation_sources = [p.source for p in w.relations()[0].provenance]
    assert entity_sources == relation_sources == ["cmdb", "flows"]


def test_two_sources_disagreeing_about_strength_leave_it_hard_and_say_so():
    """Not first-wins, which is what arrival order would give. Calling a load-bearing
    dependency optional hides an outage; calling an optional one load-bearing raises a
    false alarm. The default rests on that argument and so does this."""
    for first, second in (
        (RelationStrength.SOFT, RelationStrength.HARD),
        (RelationStrength.HARD, RelationStrength.SOFT),  # the order that decides it
    ):
        w = _two_hosts()
        w.add_relation(_edge("cmdb", first))
        w.add_relation(_edge("flows", second))

        assert w.relations()[0].strength is RelationStrength.HARD, (first, second)
        assert any("strength" in before for _, before, _ in w.collisions)


def test_merging_a_relation_on_a_fork_leaves_the_parent_alone():
    """Relation objects are shared with the world a fork came from — `_detach` copies
    entities only — so the merge replaces the relation rather than mutating it. Mutating
    would have edited the parent, and it would have surfaced in whatever ran next."""
    parent = _two_hosts()
    parent.add_relation(_edge("cmdb"))

    child = parent.fork()
    child.add_relation(_edge("flows"))

    assert [p.source for p in parent.relations()[0].provenance] == ["cmdb"]
    assert [p.source for p in child.relations()[0].provenance] == ["cmdb", "flows"]


# ---- one rule, three readers ----


POOL = """
entities:
  - {id: site, kind: site, name: site}
  - {id: host-1, kind: host, name: h1}
  - {id: k8s, kind: cluster, name: k8s}
  - {id: node-1, kind: node, name: n1}
  - {id: lb, kind: load_balancer, name: edge}
  - {id: svc-a, kind: service, name: a}
  - {id: svc-b, kind: service, name: b}
relations:
  - {src: host-1, dst: site, kind: HOSTED_IN}
  - {src: node-1, dst: host-1, kind: RUNS_ON}
  - {src: node-1, dst: k8s, kind: MEMBER_OF}
  - {src: svc-a, dst: node-1, kind: RUNS_ON}
  - {src: svc-b, dst: node-1, kind: RUNS_ON}
  - {src: svc-a, dst: lb, kind: MEMBER_OF}
  - {src: svc-b, dst: lb, kind: MEMBER_OF}
"""


def _pool_world(tmp_path) -> World:
    path = tmp_path / "pool.yaml"
    path.write_text(POOL, encoding="utf-8")
    w = World()
    w.ingest(StaticYamlConnector(str(path)).discover())
    return w


def test_blast_and_simulate_agree_that_a_pool_does_not_take_its_members(tmp_path):
    """`blast lb` listed the backends. `simulate lb` left them alone. Both were reading
    the same edge list and only one of them knew that membership of a load balancer does
    not carry death downward — the same disagreement 0.2.0 fixed for network segments,
    rebuilt one level lower."""
    w = _pool_world(tmp_path)

    in_range = set(blast_radius(w, "lb").impacted)

    after = w.fork()
    propagate(after, Event("lb", "down"))
    actually_died = {e.id for e in after.entities() if e.status.value == "down"} - {"lb"}

    assert in_range == actually_died == set()


def test_spof_does_not_rank_a_pool_for_carrying_what_it_does_not_carry(tmp_path):
    """Reach ranking walked every membership edge, so the load balancer was ranked as if
    losing it lost its pool. It outranked entities that really do take things with them."""
    w = _pool_world(tmp_path)
    reach = {r.entity_id: r.reach for r in single_points_of_failure(w, limit=50)}
    assert reach.get("lb", 0) == 0


def test_a_cluster_still_takes_its_members_down(tmp_path):
    """The correction must not go the other way. A cluster's death does reach its nodes:
    they stop being nodes."""
    w = _pool_world(tmp_path)

    assert "node-1" in blast_radius(w, "k8s").impacted

    after = w.fork()
    propagate(after, Event("k8s", "down"))
    assert after.entity("node-1").status.value == "down"
