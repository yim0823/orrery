"""A reference connector, against Kubernetes.

This exists to be copied. It is the worked example of the three problems every connector
has to solve, using a system whose shape is public so the answers can be read:

  1. **What is an entity, and what is a detail?** Pods are not entities here. They are
     cattle — created and destroyed constantly, and a map whose contents churn by the
     minute is a map nobody trusts. The workload is the entity; pods are how you find
     out which nodes it actually runs on right now.
  2. **Where do relations come from?** Rarely from one field. The edge from a workload
     to a node is not stored anywhere: you get it by joining pods to their owners.
  3. **What do you refuse to guess?** A Service with no matching workload, a pod with no
     node assigned. Those are skipped, not invented.

It reads plain dictionaries, exactly as `kubectl get -o json` prints them, so it runs
against a live cluster, a saved snapshot, or a fixture with the same code.

    from orrery.connectors.kubernetes import KubernetesConnector

    conn = KubernetesConnector.from_kubectl_json(
        cluster="prod-1",
        nodes=json.load(open("nodes.json")),
        workloads=[json.load(open("deployments.json"))],
        pods=json.load(open("pods.json")),
        services=json.load(open("services.json")),
    )
    world = World(); world.ingest(conn.discover())
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from orrery.schema import (
    Entity,
    EntityKind,
    Provenance,
    Relation,
    RelationKind,
)

from .base import Discovery

_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}


def _items(doc: Any) -> list[Mapping[str, Any]]:
    """Accept a List object, a bare list, or a single resource. All three turn up."""
    if doc is None:
        return []
    if isinstance(doc, Mapping):
        if "items" in doc:
            return list(doc["items"])
        return [doc]
    return list(doc)


def _meta(obj: Mapping[str, Any]) -> Mapping[str, Any]:
    return obj.get("metadata", {}) or {}


def _ns_name(obj: Mapping[str, Any]) -> tuple[str, str]:
    m = _meta(obj)
    return m.get("namespace", "default"), m.get("name", "")


@dataclass
class KubernetesConnector:
    """Turns a set of Kubernetes resources into entities and relations."""

    cluster: str
    nodes: list[Mapping[str, Any]] = field(default_factory=list)
    workloads: list[Mapping[str, Any]] = field(default_factory=list)
    pods: list[Mapping[str, Any]] = field(default_factory=list)
    services: list[Mapping[str, Any]] = field(default_factory=list)
    name: str = "kubernetes"

    @classmethod
    def from_kubectl_json(
        cls,
        cluster: str,
        nodes: Any = None,
        workloads: Iterable[Any] = (),
        pods: Any = None,
        services: Any = None,
        name: str = "kubernetes",
    ) -> KubernetesConnector:
        flat: list[Mapping[str, Any]] = []
        for doc in workloads:
            flat.extend(_items(doc))
        return cls(
            cluster=cluster,
            nodes=_items(nodes),
            workloads=flat,
            pods=_items(pods),
            services=_items(services),
            name=name,
        )

    # ---- identifiers ----
    # Stable, human-readable, and unique across clusters. An entity id that changes when
    # a pod restarts makes every snapshot diff meaningless.

    def cluster_id(self) -> str:
        return f"k8s:{self.cluster}"

    def node_id(self, node_name: str) -> str:
        return f"k8s:{self.cluster}:node/{node_name}"

    def workload_id(self, kind: str, namespace: str, name: str) -> str:
        return f"k8s:{self.cluster}:{kind.lower()}/{namespace}/{name}"

    def service_id(self, namespace: str, name: str) -> str:
        return f"k8s:{self.cluster}:svc/{namespace}/{name}"

    # ---- discovery ----

    def discover(self) -> Discovery:
        d = Discovery()
        prov = [Provenance(source=self.name, source_id=self.cluster)]

        d.entities.append(
            Entity(
                id=self.cluster_id(),
                kind=EntityKind.CLUSTER,
                name=self.cluster,
                attrs={"nodes": len(self.nodes)},
                provenance=prov,
            )
        )

        node_ids = self._add_nodes(d)
        workload_ids = self._add_workloads(d)
        self._add_placements(d, node_ids, workload_ids)
        self._add_services(d, workload_ids)
        return d

    def _add_nodes(self, d: Discovery) -> dict[str, str]:
        ids: dict[str, str] = {}
        for node in self.nodes:
            _, name = _ns_name(node)
            if not name:
                continue
            nid = self.node_id(name)
            ids[name] = nid
            labels = _meta(node).get("labels", {}) or {}
            capacity = (node.get("status", {}) or {}).get("capacity", {}) or {}
            d.entities.append(
                Entity(
                    id=nid,
                    kind=EntityKind.NODE,
                    name=name,
                    attrs={
                        k: v
                        for k, v in {
                            "cpu": capacity.get("cpu"),
                            "memory": capacity.get("memory"),
                            "zone": labels.get("topology.kubernetes.io/zone"),
                        }.items()
                        if v is not None
                    },
                    provenance=[Provenance(source=self.name, source_id=name)],
                )
            )
            d.relations.append(
                Relation(
                    src=nid,
                    dst=self.cluster_id(),
                    kind=RelationKind.MEMBER_OF,
                    provenance=[Provenance(source=self.name, source_id=name)],
                )
            )
        return ids

    def _add_workloads(self, d: Discovery) -> dict[tuple[str, str, str], str]:
        ids: dict[tuple[str, str, str], str] = {}
        for w in self.workloads:
            kind = w.get("kind", "")
            if kind not in _WORKLOAD_KINDS:
                continue
            ns, name = _ns_name(w)
            if not name:
                continue
            wid = self.workload_id(kind, ns, name)
            ids[(kind, ns, name)] = wid
            spec = w.get("spec", {}) or {}
            status = w.get("status", {}) or {}
            # `replicas` is what the engine reads, so prefer what is actually running over
            # what was asked for. A deployment stuck at 1 of 3 is not a 3-replica service.
            replicas = status.get("readyReplicas", status.get("replicas", spec.get("replicas", 1)))
            d.entities.append(
                Entity(
                    id=wid,
                    kind=EntityKind.SERVICE,
                    name=f"{ns}/{name}",
                    attrs={
                        "replicas": int(replicas or 0),
                        "namespace": ns,
                        "workload": kind,
                    },
                    provenance=[Provenance(source=self.name, source_id=f"{ns}/{name}")],
                )
            )
        return ids

    def _add_placements(
        self,
        d: Discovery,
        node_ids: Mapping[str, str],
        workload_ids: Mapping[tuple[str, str, str], str],
    ) -> None:
        """Where each workload is actually running, joined through pods.

        A workload does not record its nodes anywhere; pods do. One edge per
        (workload, node) pair, not per pod — ten pods on one node is still one place to
        lose, and duplicating it would let a busy node look like ten redundant ones.
        """
        seen: set[tuple[str, str]] = set()
        for pod in self.pods:
            node_name = (pod.get("spec", {}) or {}).get("nodeName")
            if not node_name or node_name not in node_ids:
                continue  # unscheduled, or a node we were not given
            owner = self._owner(pod, workload_ids)
            if owner is None:
                continue  # a bare pod with no controller: not an entity here
            pair = (owner, node_ids[node_name])
            if pair in seen:
                continue
            seen.add(pair)
            d.relations.append(
                Relation(
                    src=owner,
                    dst=node_ids[node_name],
                    kind=RelationKind.RUNS_ON,
                    provenance=[Provenance(source=self.name, source_id=_ns_name(pod)[1])],
                )
            )

    def _owner(
        self, pod: Mapping[str, Any], workload_ids: Mapping[tuple[str, str, str], str]
    ) -> str | None:
        """Walk a pod up to the workload it belongs to.

        Deployments own ReplicaSets which own Pods, so a pod's direct owner is usually a
        ReplicaSet whose name is the deployment's plus a hash. If the ReplicaSet itself
        was not supplied, fall back to that naming convention — imperfect, but the
        alternative is losing every placement edge for deployments.
        """
        ns = _meta(pod).get("namespace", "default")
        for ref in _meta(pod).get("ownerReferences", []) or []:
            kind, name = ref.get("kind", ""), ref.get("name", "")
            if (kind, ns, name) in workload_ids:
                return workload_ids[(kind, ns, name)]
            if kind == "ReplicaSet" and "-" in name:
                deployment = name.rsplit("-", 1)[0]
                key = ("Deployment", ns, deployment)
                if key in workload_ids:
                    return workload_ids[key]
        return None

    def _add_services(
        self, d: Discovery, workload_ids: Mapping[tuple[str, str, str], str]
    ) -> None:
        """Services become load balancers, with their backing workloads as members.

        Matching is by selector against workload pod labels. A Service whose selector
        matches nothing is still recorded — an empty service is a real and common
        misconfiguration, and hiding it would hide the outage it causes.
        """
        by_ns_labels: list[tuple[str, dict[str, str], str]] = []
        for w in self.workloads:
            kind = w.get("kind", "")
            if kind not in _WORKLOAD_KINDS:
                continue
            ns, name = _ns_name(w)
            wid = workload_ids.get((kind, ns, name))
            if not wid:
                continue
            labels = (
                ((w.get("spec", {}) or {}).get("template", {}) or {}).get("metadata", {}) or {}
            ).get("labels", {}) or {}
            by_ns_labels.append((ns, dict(labels), wid))

        for svc in self.services:
            ns, name = _ns_name(svc)
            if not name:
                continue
            selector = (svc.get("spec", {}) or {}).get("selector") or {}
            sid = self.service_id(ns, name)
            d.entities.append(
                Entity(
                    id=sid,
                    kind=EntityKind.LOAD_BALANCER,
                    name=f"{ns}/{name}",
                    attrs={
                        "namespace": ns,
                        "type": (svc.get("spec", {}) or {}).get("type", "ClusterIP"),
                    },
                    provenance=[Provenance(source=self.name, source_id=f"{ns}/{name}")],
                )
            )
            if not selector:
                continue  # headless or externally-managed: no membership to record
            for w_ns, labels, wid in by_ns_labels:
                if w_ns != ns:
                    continue
                if all(labels.get(k) == v for k, v in selector.items()):
                    d.relations.append(
                        Relation(
                            src=wid,
                            dst=sid,
                            kind=RelationKind.MEMBER_OF,
                            provenance=[
                                Provenance(source=self.name, source_id=f"{ns}/{name}")
                            ],
                        )
                    )
