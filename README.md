> **Note** — This README is also available in [한국어](README.ko.md).

<h1>orrery</h1>

[![CI](https://github.com/yim0823/orrery/actions/workflows/ci.yml/badge.svg)](https://github.com/yim0823/orrery/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-early--alpha-orange.svg)](#project-status)

**orrery computes what breaks when you touch your infrastructure.**

You model your hosts, clusters, services and databases as a graph. orrery answers
two questions over it: what is in range of a failure, and what actually goes down
once replicas and failover are taken into account.

> ⚠️ **Early alpha.** The engine works and is tested, but its output has never been
> checked against a real incident. Read [Project status](#project-status) before you
> rely on it for anything.

---

## The question it answers

```console
$ orrery blast host-a1

root: host-a1 (host)
  hop 1: node-a1 (node), db-stock (database)
  hop 2: svc-web (service), svc-inventory (service)
  hop 3: svc-checkout (service)
impacted: 5 / 16
```

One host, and three hops later your checkout service is in the list. `host-a1` and
`svc-checkout` are never directly connected — a node, a database and an inventory
service sit in between. Tracing that by hand, at 3am, is where outages get longer.

**But "in range" is not "down".** Whether something actually dies depends on
replicas and failover, so there is a second command:

```console
$ orrery simulate host-a1

  host-a1        -> down      passthrough
  node-a1        -> down      passthrough
  db-stock       -> down
  svc-web        -> degraded  replicas=3
  svc-inventory  -> down      replicas=1
  svc-checkout   -> down      hard dep down
```

Same host, different answer:

- `svc-web` **degrades** — three replicas, losing one node is survivable.
- `svc-inventory` **dies** — one replica.
- `svc-checkout` **dies with it** — hard dependency on inventory.

What you need on a page at 3am is not "5 impacted". It is *"checkout stops, because
inventory runs a single replica."*

---

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/yim0823/orrery && cd orrery
uv sync
uv run orrery ingest fixtures/demo-world.yaml
uv run orrery blast site-a
uv run orrery simulate db-stock
```

`fixtures/demo-world.yaml` is a small synthetic company — 3 hosts, a cluster, 4
services, 2 databases. It resembles no real organization.

### Commands

| Command | What it does |
|---|---|
| `orrery ingest <file.yaml>` | Load a world and persist it under `.orrery/` |
| `orrery blast <entity-id>` | Structural blast radius: what is in range, by hop |
| `orrery simulate <entity-id>` | Behavioral result: what actually degrades or dies |
| `orrery resolve <file.yaml>` | Propose entity-resolution candidates (never merges) |

---

## Modeling your own infrastructure

A world is **entities** and **relations**, nothing else.

```yaml
entities:
  - {id: host-a1,      kind: host,     name: "a1"}
  - {id: svc-checkout, kind: service,  name: "checkout", attrs: {replicas: 2}}
  - {id: db-orders,    kind: database, name: "orders",   attrs: {replica: true}}

relations:
  - {src: svc-checkout, dst: db-orders, kind: DEPENDS_ON}
  - {src: svc-checkout, dst: node-a2,   kind: RUNS_ON}
```

Five relation kinds. The arrow always points **from the dependent to the depended-upon**
— reverse one and the blast radius is silently wrong.

| Kind | Reads as |
|---|---|
| `RUNS_ON` | service runs on node; node runs on host |
| `HOSTED_IN` | host is hosted in a site |
| `MEMBER_OF` | node is a member of a cluster |
| `DEPENDS_ON` | service depends on a database |
| `CONNECTS_TO` | host talks to a network segment |

The first four propagate impact. `CONNECTS_TO` does not — communication is symmetric
and does not imply "if A dies, B dies".

### Connectors

orrery ships the **interface**, not implementations. Connectors to your CMDB,
Kubernetes or monitoring live in your own repository, because no two organizations
model these the same way.

```python
from orrery.connectors.base import Discovery
from orrery.schema import Entity, Relation, EntityKind, RelationKind, Provenance

class MyCmdbConnector:
    name = "mycmdb"

    def discover(self) -> Discovery:
        d = Discovery()
        for row in my_cmdb.list_servers():
            d.entities.append(Entity(
                id=f"host-{row['id']}", kind=EntityKind.HOST, name=row["hostname"],
                attrs={"ip": row["ip"], "env": row["env"]},
                provenance=[Provenance(source="mycmdb", source_id=row["id"])],
            ))
            d.relations.append(Relation(
                src=f"host-{row['id']}", dst=f"site-{row['idc']}",
                kind=RelationKind.HOSTED_IN,
                provenance=[Provenance(source="mycmdb", source_id=row["id"])],
            ))
        return d
```

Implement `discover()` and nothing else. Results from multiple connectors merge.

### Entity resolution never merges automatically

Your CMDB calls it `inventory`; your monitoring calls it `inventory-prod`. Two names
means two nodes in the graph, which means both blast radii are wrong.

```console
$ orrery resolve fixtures/demo-world.yaml
candidate: svc-inventory (inventory) | svc-inventory-prod (inventory-prod)
```

orrery proposes; a human confirms. This is deliberate. A wrongly merged map is worse
than no map — people distrust a missing map, but a wrong one answers confidently, and
someone acts on that answer at 3am.

---

## What orrery is not

- **Not monitoring.** Your existing tools tell you what is broken now. orrery computes
  what would break.
- **Not an APM service map.** Those draw observed traffic, so they miss nightly batch
  paths, failover routes, and services with no load yet. orrery uses declared structure.
- **Not a CMDB.** Inventory lives in your CMDB. orrery is a compute layer on top.
  Do not run a second source of truth; two will diverge, and then neither is trusted.
- **Not a second production.** Nothing is actually executed. Entities are objects and
  behavior models; only the region under examination is computed in detail.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — data model, propagation, extension points, tradeoffs
- [Clean-room rules](CLEANROOM.md) — what may never enter this repository
- [Contributing](CONTRIBUTING.md)

---

## Project status

Early alpha, `0.0.1`. Honest picture:

| Works | Not yet |
|---|---|
| Entity/relation model, YAML ingest | Connectors to real systems (you write them) |
| Structural blast radius | Visualization — terminal output only |
| Behavioral propagation with per-kind models | Snapshot diffing over time |
| Entity-resolution candidates | Severity weighting — binary up/down only |
| Four-axis agent trust rubric | Scenario runner (format defined, runner missing) |

**The biggest open problem is accuracy.** Nothing here has been validated against a
real incident. The next milestone is backtesting: take N past incidents, and score
whether this engine would have predicted the impact — true positives, misses, and
false alarms. Until those numbers exist, treat the output as advisory, and say so to
anyone who asks.

There is one known correctness gap in propagation: when a degrade event and a down
event reach the same entity, **arrival order decides the result.** Events need
severity so the stronger one wins. See [Architecture §4](docs/ARCHITECTURE.md).

---

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md)
first — in particular the clean-room rule, which is enforced by a commit hook.

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
```

## License

[Apache-2.0](LICENSE).

---

<sub>An orrery is a mechanical model of the solar system. Every planet is on the model,
and when you turn the crank, every position is computed.</sub>
