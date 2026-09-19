"""World schema: entity and relation types.

Deliberately small. Every entity has an id, a kind, a status, free-form attributes and provenance.
Type-specific fields live in `attrs` until a model needs them to be first-class.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EntityKind(StrEnum):
    """What something is, to the degree the engine cares.

    The list is short on purpose, and each kind earns its place by failing differently
    from the others. A kind that behaves exactly like `service` is noise: it makes the
    model look richer while changing no answer. When something here does not fit, the
    honest move is usually the closest kind plus an `attrs` entry, not a new kind — the
    exception is anything whose failure semantics a behavior model would need to special
    case, and those are the ones below.
    """

    SITE = "site"  # an IDC, a cloud region, an availability zone
    RACK = "rack"
    HOST = "host"  # physical server or VM
    CLUSTER = "cluster"  # e.g. a Kubernetes cluster
    NODE = "node"  # a cluster member
    SERVICE = "service"  # a deployable workload
    DATABASE = "database"
    QUEUE = "queue"  # broker or stream: producers survive it, consumers usually do not
    STORAGE = "storage"  # volume, SAN, object store: many things run on one of these
    LOAD_BALANCER = "load_balancer"
    DNS = "dns"  # a zone or resolver — close to a universal dependency
    CERTIFICATE = "certificate"  # expires on a date rather than failing at random
    CDN = "cdn"
    JOB = "job"  # batch or scheduled work, as opposed to something always running
    NETWORK_SEGMENT = "network_segment"  # VLAN, subnet, VPC
    EXTERNAL = "external"  # third-party dependency we do not model internally


class RelationKind(StrEnum):
    RUNS_ON = "RUNS_ON"  # service -> node/host ; node -> host ; host -> rack
    DEPENDS_ON = "DEPENDS_ON"  # service -> service/database/external/load_balancer
    CONNECTS_TO = "CONNECTS_TO"  # host/node -> network_segment
    MEMBER_OF = "MEMBER_OF"  # node -> cluster ; service -> load_balancer pool
    HOSTED_IN = "HOSTED_IN"  # rack/host/cluster/segment -> site


class RelationStrength(StrEnum):
    """How much the dependent needs the thing it depends on.

    HARD: it cannot work without it. A service loses its primary database and stops.
    SOFT: it degrades but survives. A checkout service whose payment provider is gone
    queues orders and retries; the cart still works, confirmation is late.

    This lives on the relation, not the entity, because the same database can be
    load-bearing for one service and a nice-to-have for another.
    """

    HARD = "hard"
    SOFT = "soft"


class Status(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class Provenance(BaseModel):
    """Where a fact came from. Every entity and relation carries at least one."""

    source: str  # connector name
    source_id: str  # the id in that source
    observed_at: str | None = None  # ISO 8601


class Entity(BaseModel):
    # `strenght: soft` used to ingest as hard with no message. A typo in a field name is
    # the one mistake that produces a confident wrong answer instead of an error.
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EntityKind
    name: str
    status: Status = Status.UP
    attrs: dict[str, Any] = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str
    dst: str
    kind: RelationKind
    strength: RelationStrength = RelationStrength.HARD
    """Default HARD: assume a dependency is load-bearing until someone says otherwise.

    Guessing SOFT would hide real outages, which is the failure mode that hurts people.
    Guessing HARD only produces false alarms, which is the failure mode that annoys them.
    """
    attrs: dict[str, Any] = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)
