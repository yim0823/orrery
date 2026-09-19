"""The reference connector, tested on the shapes kubectl actually emits.

Synthetic cluster. Nothing here corresponds to any real deployment.
"""
from __future__ import annotations

from orrery.connectors import KubernetesConnector
from orrery.schema import EntityKind, RelationKind, Status
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius


def _node(name: str, zone: str = "a"):
    return {
        "kind": "Node",
        "metadata": {"name": name, "labels": {"topology.kubernetes.io/zone": zone}},
        "status": {"capacity": {"cpu": "8", "memory": "32Gi"}},
    }


def _deployment(ns: str, name: str, ready: int = 2, app: str | None = None):
    return {
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "replicas": 3,
            "template": {"metadata": {"labels": {"app": app or name}}},
        },
        "status": {"replicas": 3, "readyReplicas": ready},
    }


def _pod(ns: str, name: str, node: str, owner_kind: str, owner_name: str):
    return {
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": ns,
            "ownerReferences": [{"kind": owner_kind, "name": owner_name}],
        },
        "spec": {"nodeName": node},
    }


def _service(ns: str, name: str, selector: dict | None):
    spec = {"type": "ClusterIP"}
    if selector is not None:
        spec["selector"] = selector
    return {"kind": "Service", "metadata": {"name": name, "namespace": ns}, "spec": spec}


NODES = {"items": [_node("n1"), _node("n2"), _node("n3", zone="b")]}
WORKLOADS = [
    {"items": [_deployment("shop", "web", ready=2), _deployment("shop", "cart", ready=1)]},
    {"items": [{"kind": "StatefulSet", "metadata": {"name": "cache", "namespace": "shop"},
                "spec": {"replicas": 1, "template": {"metadata": {"labels": {"app": "cache"}}}},
                "status": {"readyReplicas": 1}}]},
]
PODS = {
    "items": [
        _pod("shop", "web-abc123-1", "n1", "ReplicaSet", "web-abc123"),
        _pod("shop", "web-abc123-2", "n2", "ReplicaSet", "web-abc123"),
        _pod("shop", "web-abc123-3", "n1", "ReplicaSet", "web-abc123"),  # second pod, same node
        _pod("shop", "cart-def456-1", "n3", "ReplicaSet", "cart-def456"),
        _pod("shop", "cache-0", "n3", "StatefulSet", "cache"),
        _pod("shop", "orphan", "n1", "Job", "nightly"),          # not a mapped workload
        {"kind": "Pod", "metadata": {"name": "pending", "namespace": "shop"}, "spec": {}},
    ]
}
SERVICES = {
    "items": [
        _service("shop", "web", {"app": "web"}),
        _service("shop", "nothing", {"app": "does-not-exist"}),
        _service("shop", "headless", None),
    ]
}


def _conn() -> KubernetesConnector:
    return KubernetesConnector.from_kubectl_json(
        cluster="demo", nodes=NODES, workloads=WORKLOADS, pods=PODS, services=SERVICES
    )


def _world() -> World:
    w = World()
    w.ingest(_conn().discover())
    return w


def test_nodes_and_cluster_become_entities():
    d = _conn().discover()
    kinds = {e.kind for e in d.entities}
    assert EntityKind.CLUSTER in kinds and EntityKind.NODE in kinds
    assert sum(1 for e in d.entities if e.kind is EntityKind.NODE) == 3


def test_nodes_are_members_of_the_cluster():
    d = _conn().discover()
    member = [r for r in d.relations if r.kind is RelationKind.MEMBER_OF and "node/" in r.src]
    assert len(member) == 3


def test_pods_are_not_entities():
    # Pods churn by the minute; a map that churns with them is a map nobody trusts.
    d = _conn().discover()
    assert not any("pod" in e.id for e in d.entities)


def test_workloads_become_services_with_the_replica_count_that_is_real():
    d = _conn().discover()
    web = next(e for e in d.entities if e.name == "shop/web")
    # spec asked for 3, only 2 are ready — a deployment stuck at 2 is not a 3-replica service
    assert web.attrs["replicas"] == 2


def test_placement_is_joined_through_pods():
    d = _conn().discover()
    runs = {(r.src, r.dst) for r in d.relations if r.kind is RelationKind.RUNS_ON}
    web = "k8s:demo:deployment/shop/web"
    assert (web, "k8s:demo:node/n1") in runs
    assert (web, "k8s:demo:node/n2") in runs


def test_two_pods_on_one_node_is_one_place_to_lose():
    d = _conn().discover()
    web_edges = [
        r for r in d.relations
        if r.kind is RelationKind.RUNS_ON and r.src.endswith("deployment/shop/web")
    ]
    assert len(web_edges) == 2  # n1 and n2, not three pods


def test_a_deployments_pods_are_traced_through_the_replicaset_name():
    d = _conn().discover()
    assert any(
        r.src == "k8s:demo:deployment/shop/web" for r in d.relations
        if r.kind is RelationKind.RUNS_ON
    )


def test_a_pod_with_no_mapped_owner_is_skipped():
    d = _conn().discover()
    assert not any("nightly" in r.src or "Job" in r.src for r in d.relations)


def test_an_unscheduled_pod_is_skipped_rather_than_guessed_at():
    d = _conn().discover()
    assert all(r.dst.startswith("k8s:demo:node/") for r in d.relations
               if r.kind is RelationKind.RUNS_ON)


def test_services_become_load_balancers_with_their_backends():
    d = _conn().discover()
    members = {
        (r.src, r.dst) for r in d.relations
        if r.kind is RelationKind.MEMBER_OF and "svc/" in r.dst
    }
    assert ("k8s:demo:deployment/shop/web", "k8s:demo:svc/shop/web") in members


def test_a_service_matching_nothing_is_still_recorded():
    # An empty service is a real misconfiguration; hiding it hides the outage it causes.
    d = _conn().discover()
    assert any(e.id == "k8s:demo:svc/shop/nothing" for e in d.entities)
    assert not any(r.dst == "k8s:demo:svc/shop/nothing" for r in d.relations)


def test_a_headless_service_records_no_membership():
    d = _conn().discover()
    assert any(e.id == "k8s:demo:svc/shop/headless" for e in d.entities)
    assert not any(r.dst == "k8s:demo:svc/shop/headless" for r in d.relations)


def test_ids_are_stable_across_two_discoveries():
    # Snapshot diffing is worthless if ids move between runs.
    first = {e.id for e in _conn().discover().entities}
    second = {e.id for e in _conn().discover().entities}
    assert first == second


def test_the_result_answers_the_question_the_engine_exists_for():
    w = _world()
    br = blast_radius(w, "k8s:demo:node/n3")
    assert "k8s:demo:deployment/shop/cart" in br.impacted

    sim = w.fork()
    propagate(sim, Event("k8s:demo:node/n1", "down"))
    # web still has n2, so it degrades rather than dies
    assert sim.entity("k8s:demo:deployment/shop/web").status is Status.DEGRADED

    sim2 = w.fork()
    propagate(sim2, Event("k8s:demo:node/n3", "down"))
    # cart only ran on n3
    assert sim2.entity("k8s:demo:deployment/shop/cart").status is Status.DOWN


def test_empty_input_produces_just_the_cluster():
    conn = KubernetesConnector.from_kubectl_json(cluster="empty")
    d = conn.discover()
    assert [e.kind for e in d.entities] == [EntityKind.CLUSTER]
    assert not d.relations
