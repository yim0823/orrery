> **Note** — This README is also available in [한국어](README.ko.md).

<h1>orrery</h1>

[![CI](https://github.com/yim0823/orrery/actions/workflows/ci.yml/badge.svg)](https://github.com/yim0823/orrery/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-early--alpha-orange.svg)](#project-status)

**orrery computes what else fails when one thing in your infrastructure does.**

You model your estate as a graph. orrery answers two questions over it: what is in
range of a failure, and what actually goes down once redundancy and failover are taken
into account. It models one thing failing, not a change being applied — "reboot this
host" is expressed as that host going down.

> ⚠️ **Early alpha, and one licensing caveat.** The engine works, is tested, and can
> grade itself against past incidents — but it has not been graded against *yours*, and
> accuracy is the open question. Separately: the first commits were made on employer
> equipment, so the author's right to license this is not yet settled in writing. If
> that matters to you, wait. Both are expanded in [Project status](#project-status).

---

## The question it answers

```console
$ orrery blast host-a1

root: host-a1 (host)
  hop 1: node-a1 (node), db-stock (database)
  hop 2: svc-web (service), svc-inventory (service)
  hop 3: svc-checkout (service)
impacted: 5 / 17
```

One host, and three hops later your checkout service is in the list. `host-a1` and
`svc-checkout` are never directly connected — a node, a database and an inventory
service sit in between. Tracing that by hand, at 3am, is where outages get longer.

**But "in range" is not "down".** Whether something actually dies depends on
replicas and failover, so there is a second command:

```console
$ orrery simulate host-a1

  host-a1                  -> down      passthrough
  node-a1                  -> down      passthrough
  db-stock                 -> down      
  svc-web                  -> degraded  dep degraded
  svc-inventory            -> down      hard dep down
  svc-checkout             -> down      hard dep down
```

Same host, different answer:

- `svc-web` **degrades** — it still has another node to run on. Not because anything
  declares a replica count; because the graph has another `RUNS_ON` edge.
- `svc-inventory` **dies** — the database it needs died with the host.
- `svc-checkout` **dies with it** — hard dependency on inventory.

What you need on a page at 3am is not "5 impacted". It is *"checkout stops, because the
stock database went with the host."*

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
| `orrery check` | Is this map any good? Gaps and drift in the map itself |
| `orrery spof` | What is most dangerous? Entities ranked by what goes with them |
| `orrery diff <a> <b>` | What changed between two snapshots |
| `orrery backtest <dir>` | Replay past incidents and score the engine against them |

## Two questions you have on day one

Before any incident history exists, before anyone has calibrated anything, and before you
have modelled a single dependency by hand. These two run on whatever the first connector
gave you.

Before any incident history exists, and before anyone has calibrated anything:

```console
$ orrery spof --limit 4

single points of failure, by what goes with them (18 entities)

    1. site-a         9 (52.9%)  site
    2. etcd           6 (35.3%)  cluster
    3. k8s-main       6 (35.3%)  cluster
    4. host-a1        5 (29.4%)  host
```

Structural reach, and deliberately blind to any declared redundancy — redundancy that is
recorded but not real is exactly what this list exists to surface.

```console
$ orrery check

map: 18 entities, 25 relations
  sources: static_yaml (18)
  0 entities confirmed by more than one source, 18 by exactly one

redundancy on paper only (1)
  svc-checkout                         replicas=2 but one place to run: losing it loses everything

isolated (1)
  svc-inventory-prod                   nothing connects to it — usually a join that failed, not a server nobody uses
```

`redundancy on paper only` is a service claiming two replicas with one recorded place to
run. `isolated` is usually a join that failed quietly, not a server nobody uses. Neither
is an error; both are worth someone looking at before the map is trusted.

---

## Modeling your own infrastructure

A world is **entities** and **relations**, nothing else.

```yaml
entities:
  - {id: host-a1,      kind: host,     name: "a1"}
  - {id: node-a1,      kind: node,     name: "node-a1"}
  - {id: svc-checkout, kind: service,  name: "checkout"}
  - {id: db-orders,    kind: database, name: "orders"}

relations:
  - {src: node-a1,      dst: host-a1,   kind: RUNS_ON}
  - {src: svc-checkout, dst: node-a1,   kind: RUNS_ON}
  - {src: db-orders,    dst: host-a1,   kind: RUNS_ON}
  - {src: svc-checkout, dst: db-orders, kind: DEPENDS_ON}
```

Every id in `relations` must already exist in `entities`; ingest refuses a dangling
reference rather than inventing the missing end.

Note what is *not* here: no `replicas`, no `replica: true`. **Redundancy is somewhere to
run, not a number you assert.** A service with three `RUNS_ON` edges survives losing one;
a service with `replicas: 3` and one `RUNS_ON` edge does not, and `orrery check` will tell
you which one you have built.

Five relation kinds. The arrow always points **from the dependent to the depended-upon**
— reverse one and the blast radius is silently wrong.

There are sixteen entity kinds (`orrery ingest` will list them if you mistype one); the
ones in the demo are `site`, `host`, `cluster`, `node`, `service`, `database`,
`load_balancer` and `external`. Each kind earns its place by failing differently.

| Kind | Reads as |
|---|---|
| `RUNS_ON` | service runs on node; node runs on host |
| `HOSTED_IN` | host is hosted in a site |
| `MEMBER_OF` | node is a member of a cluster |
| `DEPENDS_ON` | service depends on a database |
| `CONNECTS_TO` | host talks to a network segment |

The first four propagate impact. `CONNECTS_TO` does not — communication is symmetric
and does not imply "if A dies, B dies".

### Hard and soft dependencies

Not everything you depend on is load-bearing. Lose your primary database and you stop.
Lose a payment provider and, if you queue and retry, you keep taking orders — the cart
still works, confirmation is just late.

```yaml
relations:
  - {src: svc-checkout, dst: db-orders,    kind: DEPENDS_ON}                   # hard
  - {src: svc-checkout, dst: ext-payments, kind: DEPENDS_ON, strength: soft}
```

A soft edge **caps what crosses it at "degraded"**. It weakens the consequence; it does
not silence it. A storefront whose checkout is slow is itself slow.

`strength` defaults to `hard`, deliberately. Marking something soft when it is not hides
a real outage; marking something hard when it is not produces a false alarm. The first
failure hurts people, the second annoys them — so when in doubt, leave it hard.

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

## Grading a map you already have

You do not need to adopt the engine to use the harness. If your dependencies already live
in a graph, read them out, and let your own past incidents tell you how good that map is:

```python
from orrery.adapters.neo4j import LabelMap, Neo4jSource
from orrery.schema import EntityKind, RelationKind

world = Neo4jSource(driver, LabelMap(
    entity_labels={"Server": EntityKind.HOST, "App": EntityKind.SERVICE},
    relation_types={"DEPLOYED_ON": RelationKind.RUNS_ON, "CALLS": RelationKind.DEPENDS_ON},
)).load()
world.save("snapshots/2026-03.yaml")
```

Then write incidents against that snapshot and run `orrery backtest`. The number that
comes out — "the map predicted 19 of 26 impacts, missed 2, over-called 2" — is a claim
about your map, not about this tool, and it is the only artifact in this space that lets
you say anything precise about a dependency map at all.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — data model, propagation, extension points, tradeoffs
- [Architecture (한국어)](docs/ARCHITECTURE.ko.md) — the same document in Korean; the English one is kept current
- [Clean-room rules](CLEANROOM.md) — what may never enter this repository
- [Contributing](CONTRIBUTING.md)

---

## Project status

Early alpha, `0.2.0`. Honest picture:

**Licensing.** The first two commits were authored on employer equipment under an
employer account. Code is copyright rather than patent, and work-for-hire rules are
stricter than most people expect, so the author's right to grant this licence is not
settled in writing yet. Nothing here is company-specific and a commit hook enforces that,
but if you need a clean provenance chain, this is not yet it.

| Works | Not yet |
|---|---|
| Entity/relation model, YAML ingest, snapshot diffing | Connectors to real systems — Kubernetes is the only one |
| Structural blast radius and behavioral propagation | Capacity: whether the survivors can carry the load |
| Hard and soft dependencies, tolerance windows, quorum | Magnitude: two of three replicas lost reads as one |
| Entity-resolution candidates | Visualization — terminal and JSON only |
| Machine-readable output, Neo4j source | Time that advances on its own, and more than one actor |
| Map audit (`check`) and risk ranking (`spof`) | Materializing part of the world as real running systems |
| Backtesting harness | |

**Accuracy is the open problem, and measuring it is the part nobody else does.**

Plenty of tools will draw you a dependency map. Reviewing the field in 2026 — CMDB and
application-dependency-mapping products, observability service maps, chaos platforms,
developer portals — none of them score their own map against what actually happened in
past incidents. That is what this harness does, and **it does not require the map to be
orrery's.** Point `orrery.adapters.neo4j` at a graph you already run, save the snapshot,
write your incidents against it, and the thing being graded is your existing map.

```console
$ orrery backtest fixtures/incidents

backtest: 6 incident(s), 24 prediction(s) scored
  78 entit(ies) skipped — the records say nothing about them

  recall    100%   of what broke, we called broken at all
  precision 100%   of the predictions someone checked, right
  exact     96%   severity exactly right
  on breaks 95%   severity exactly right, counting only what broke

  ⚠ 5 prediction(s) of breakage nobody checked. Precision cannot see them,
    so it is an upper bound: over-predicting is free until the records say otherwise.

  hit            19   predicted, right severity
  correct up      4   agreed it was unaffected
  understated     1   said degraded, was down
  overstated      0   said down, was degraded
  false alarm     0   said broken, was fine
  MISS            0   said fine, was broken

⚠ fewer than 30 scored predictions. Treat these rates as a smoke test, not a measurement.
⚠ every incident replays against one snapshot. If that snapshot was written after the
  incidents, this measures hindsight rather than prediction — an edge learned from a
  postmortem is already in the map being graded.
```

Write your past incidents as records — what broke, and what was *observed* to break —
and the engine grades itself. Two design choices matter:

- **Silence is not health.** An entity your record says nothing about is skipped, not
  scored as healthy. You only learn about what someone noticed at the time, and counting
  unexamined systems as fine inflates every number on this report.
- **Severity counts.** Predicting "down" when something merely degraded is not a hit. It
  is `overstated`, and it is why the demo scores 100% recall but 75% exact.

The harness has earned its keep twice. Its first run found four defects — arrival order
deciding whether a service came out degraded or down, quorum ignored entirely, no notion
of a soft dependency, and a service that trusted a `replicas` attribute instead of
counting the nodes actually left. An adversarial review then found that the last one was
only half fixed: propagation counted survivors and the behavior model then overrode the
count with the attribute anyway, so a service on forty-nine healthy nodes was reported
down when one rebooted. That review also found that a network segment failing computed as
affecting nothing, because the edge that attaches a host to it was excluded from impact.

Read that as the argument for the harness rather than against the engine: both classes of
defect were invisible to 140 passing tests, and the second was invisible to the harness
too until someone went looking for it deliberately.

The one `understated` result left is deliberate, in `fixtures/incidents/INC-0006.yaml`:
**the engine knows whether somewhere is left to run, not whether the survivors can carry
the load.** One node was lost at peak; structurally the storefront survived, and in
reality the remaining node took the whole load and fell over. Answering that needs
capacity modelling, which may not belong in a structural engine at all.

A backtest containing only incidents the engine already handles measures nothing.

**Read the report's own warnings.** Precision is computed over predictions somebody
actually checked, so it is an upper bound: the report prints how many predictions nobody
verified, and until that number is small, over-predicting is free. Recall counts a
prediction of "degraded" for something that died as caught, which is why `on breaks` —
severity exactly right, counting only what broke — is the number to watch. And if every
incident replays against one snapshot written after the fact, the exercise measures
hindsight rather than prediction; the report says that too.

Until you have run this against your own incidents, treat the output as advisory and say
so to anyone who asks.

Performance, measured rather than asserted — one laptop, `scripts/bench.py`:

| World | blast (whole site) | simulate | fork | spof |
|---|---|---|---|---|
| 25,508 entities | 53 ms | 164 ms | below timer resolution | 0.3 s, 130 MB |
| 127,508 entities | 307 ms | 1.1 s | below timer resolution | 5.5 s, 1.3 GB |

The large numbers are the pathological case: a whole site failing and reaching a third of
the estate. A single host or database is an order of magnitude cheaper.

`spof` is the one to watch, and memory is its limit rather than time — it holds reach as
a bitset per component, so it grows with the square of the estate. Past a few hundred
thousand entities it needs a different algorithm, not a bigger machine.

---

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md)
first — in particular the clean-room rule, which is enforced by a commit hook.

```bash
uv sync
uv run pytest
uv run ruff check .
```

## License

[Apache-2.0](LICENSE).

---

<sub>An orrery is a mechanical model of the solar system. Every planet is on the model,
and when you turn the crank, every position is computed.</sub>
