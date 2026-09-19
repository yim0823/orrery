> This document is also available in [한국어](ARCHITECTURE.ko.md). The English version is
> the one kept current.

# Architecture

The README says what this is for. This document says how it works and where you are meant
to change it. Read it before the code.

---

## 1. The whole picture

```mermaid
flowchart LR
  subgraph outside["your repository (private)"]
    C1[CMDB connector]
    C2[Kubernetes connector]
    C3[monitoring connector]
  end
  subgraph engine["orrery (public)"]
    D[Discovery] --> R[resolve<br/>propose candidates]
    R -->|a human confirms| W[(World<br/>graph)]
    W --> B[blast_radius<br/>structural range]
    W --> P[propagate<br/>behavioral result]
    W --> A[audit<br/>check · spof]
    W --> DF[diff<br/>drift between snapshots]
    P --> BT[backtest<br/>score against past incidents]
    P --> SC[scenarios<br/>break it, hand an agent its tools]
    SC --> S[scoring<br/>four-axis rubric]
  end
  C1 --> D
  C2 --> D
  C3 --> D
```

There is one boundary that matters. **No code that talks to a real system lives in this
repository.** Connectors define an interface here; implementations live in your own
repository. The dependency arrow points one way only.

```
your repository  ──depends on──▶  orrery
orrery           ──never───────▶  your repository
```

Two things in this repo look like exceptions and are not. `orrery.connectors.kubernetes`
is a reference connector against a public API, written to be copied. `orrery.adapters.neo4j`
reads a property graph whose vocabulary you supply. Neither knows anything about any
particular organization.

---

## 2. The two layers, and where each comes from

Before the data model: a map is built from two kinds of edge that come from different
places and cost very different amounts of effort. Most of what follows is a consequence of
keeping them distinct.

```mermaid
flowchart TB
  subgraph sources["Sources — all outside this repo"]
    direction LR
    cmdb[("CMDB · cloud API · Kubernetes")]
    flows[("flow logs · firewall logs · socket tables")]
    traces[("traces or eBPF, where available")]
  end

  subgraph layers["The map"]
    direction TB
    calls["Call layer — DEPENDS_ON<br/>who talks to whom"]
    infra["Infrastructure layer<br/>RUNS_ON · HOSTED_IN · MEMBER_OF · CONNECTS_TO<br/>what sits on what"]
    calls -->|joins on identity| infra
  end

  subgraph answers["What the engine computes"]
    direction LR
    blast["blast<br/>what is in range"]
    sim["simulate<br/>what actually breaks"]
    spof["spof<br/>what is most dangerous"]
    chk["check<br/>is this map any good"]
    back["backtest<br/>was it right last time"]
  end

  cmdb --> infra
  flows --> calls
  traces --> calls
  layers --> answers
```

| | Infrastructure layer | Call layer |
|---|---|---|
| Question | what sits on what | who talks to whom |
| Edges | `RUNS_ON`, `HOSTED_IN`, `MEMBER_OF`, `CONNECTS_TO` | `DEPENDS_ON` |
| Source | an inventory: CMDB, cloud API, Kubernetes | **no inventory has it** — it is a property of running code |
| Cost | a connector per source | the hard part |
| Missing it means | there is no map | "a server is down" rather than "checkout stops" |

**The join between them is the risk.** A service in the call layer `RUNS_ON` a node in the
infrastructure layer, and those two records usually come out of different systems that name
the same thing differently. A wrong join splits one service into two, and then both halves
answer confidently and wrongly. Entity resolution exists for this seam, and it refuses to
guess (§6).

### Getting the call layer without instrumenting everything

Three ways exist, and they are not equally available:

| Approach | Touches the application? | The catch |
|---|---|---|
| Distributed tracing — OpenTelemetry, Jaeger, Zipkin | **yes**, a library in every service | Realistic only where every service can be instrumented and every hop propagates context. One service that cannot breaks the graph downstream of it, and native or legacy code usually cannot. |
| eBPF — Pixie, SkyWalking Rover, Hubble | no | Kubernetes-shaped; kernel requirements; an agent per node |
| Connection observation — flow logs, firewall logs, socket tables | **no** | Coarse: `host A → host B:9000`, not which endpoint. Traffic that never crosses an observation point is invisible. |

The tools we looked at assume the first, which is why they work beautifully on a
green-field Kubernetes estate and barely at all on one that grew over fifteen years. That
is an observation from a survey, not a proof; if you know one that does the third, it is
worth telling us, because it would be a better starting point than this.

The third builds a usable call layer by joining two facts, neither of which requires
touching an application:

```
"host A sends traffic to host B:9000"           (flow or firewall log)
"port 9000 on host B is the inventory service"  (process listing)
────────────────────────────────────────────────
A's service DEPENDS_ON the inventory service
```

Coarse is enough here. Blast radius asks what breaks, not which endpoint breaks, and for
that question a listening port is a service.

### The infrastructure layer is one level deeper than people model it

The call layer is the hard half to *acquire*. The infrastructure layer is easy to acquire
and easy to get wrong, in one specific way: it is usually modelled one level too shallow.

A Kubernetes node is rarely a physical server. It is a virtual machine — an OpenStack
instance, an EC2 instance, a guest on a hypervisor another team runs — and several of them
commonly share one physical machine. The full chain is six levels, and the third is the one
that gets left out:

```
service → node → vm → host → rack → site
                 ↑
                 the cluster cannot see past here
```

Everything the cluster reports is true. Three nodes are three nodes. What is false is the
inference everyone draws from it — that three nodes are three failure domains — and that
inference is wrong precisely when the `vm → host` edge is missing, which is also exactly
when nothing can correct it. Kubernetes does not know what it is standing on, so no amount
of querying the cluster recovers the level. It has to come from the layer below (the cloud
or virtualization API, or a CMDB that records placement), and joining it to the node is the
same identity problem as §6.

This is why `EntityKind.VM` is distinct from `EntityKind.HOST`. Collapsing them is
tempting — both are "a machine a thing runs on" — and it silently removes the only edge
that makes the defect visible. `audit._shared_foundation()` (§9) walks `RUNS_ON` and
`HOSTED_IN` down from every place a service can run and reports the **nearest** thing all
of them share: a host reads as `redundancy on one machine`, a rack as `redundancy in one
rack`, and a site as nothing at all, because sharing a datacentre is a fact about the
estate rather than a defect and a finding on every service is how a report gets ignored.

---

## 3. The data model

Kept small on purpose. A model that grows to hold every organization's circumstances ends
up fitting none of them.

```python
class Entity(BaseModel):
    id: str
    kind: EntityKind          # seventeen of them: site, rack, host, vm, cluster, node,
                              # service, database, queue, storage, load_balancer, dns,
                              # certificate, cdn, job, network_segment, external
    name: str
    status: Status = UP       # up | degraded | down | unknown
    attrs: dict[str, Any]     # kind-specific fields go here
    provenance: list[Provenance]

class Relation(BaseModel):
    src: str
    dst: str
    kind: RelationKind        # RUNS_ON | DEPENDS_ON | CONNECTS_TO | MEMBER_OF | HOSTED_IN
    strength: RelationStrength = HARD
    attrs: dict[str, Any]
    provenance: list[Provenance]
```

**Three design decisions.**

**`attrs` is free-form.** Replica counts, database engines, site codes all live there. The
schema does not grow until a behavior model needs a field to be first-class. There is a
price: typos pass silently. Nothing in propagation reads `replicas` any more — it counts
places instead — but `check` does, so `replica` where you meant `replicas` costs you the
`redundancy on paper only` finding and nobody is told. Validate in your connector.

**`provenance` is close to mandatory.** Without a record of which connector saw this fact
and when, you cannot adjudicate between two systems that disagree. A map is trusted
because of source tracking, not in spite of it.

**`status` lives on the entity.** Simulation advances by changing it. That is why a
simulation always runs on a `fork()` and never touches the original.

### Direction is everything

Reverse an arrow and the blast radius is wrong in a way nothing will tell you about. **The
arrow points from the thing that depends to the thing depended upon.**

| Relation | src | dst | Reads as |
|---|---|---|---|
| `RUNS_ON` | service | node | the service **runs on** the node; a node on a vm, a vm on a host |
| `HOSTED_IN` | host | rack | the host **sits in** the rack; a rack in a site |
| `MEMBER_OF` | node | cluster | the node **belongs to** the cluster |
| `DEPENDS_ON` | service | database | the service **needs** the database |
| `CONNECTS_TO` | host | network segment | the host **talks to** the segment |

**All five propagate impact.** `CONNECTS_TO` was left out of the walk for a release, on the
reasoning that communication is symmetric — which made a whole class of outage, a VLAN or a
top-of-rack switch, compute as affecting nothing. A host attached to a segment does depend
on that segment. 0.2.0 fixed it, and `blast` and `simulate` now read the same edge list
rather than each keeping its own.

---

## 4. blast radius — structural range

`src/orrery/world/query.py`

**If X goes down, the things with an arrow pointing at X are in range.** A breadth-first
walk over incoming edges.

```python
def blast_radius(world, root, max_hops=None) -> BlastRadius
```

The edge list is not query.py's own: `_impact_edges()` returns propagation's
`_DEPENDENT_EDGES`, so the two commands cannot drift apart again.

It returns a **hop distance** and a **path** for each entity in range. The path is the part
that matters. "checkout is affected" is not something a person can act on. Show
`host-a1 → node-a1 → svc-inventory → svc-checkout` and they can both believe it and argue
with it.

**Complexity is O(V+E)** over an in-memory graph. Real timings are in
[§14 Performance](#14-performance); the short version is tens of milliseconds at 25k
entities and a few hundred at 127k, not the "milliseconds" this used to claim. Past that,
change the storage — and when you do, still do not give orrery a store of its own. Compute
over the graph database you already run.

**What this does not answer:** whether anything actually dies. That is the next section.

---

## 5. propagate — behavioral consequence

`src/orrery/sim/propagate.py`

Same edges, but at each entity it asks a **behavior model**: "this event reached you — what
happens to you?"

```mermaid
flowchart TD
  E[Event: host-a1 down] --> Q{queue}
  Q --> M[BehaviorModel.react for that kind]
  M --> S[status change]
  M --> EM[emit follow-on events]
  EM --> IN[find dependents via in_edges]
  IN --> T[translate the event]
  T --> Q
  M --> QF[check clusters for lost quorum]
  QF --> Q
```

```python
@dataclass
class Effect:
    entity_id: str
    status: Status | None      # this entity's new status
    emit: list[str]            # events to pass on to dependents
    note: str                  # why, in words a person reads
```

**The model holds the judgement.** The default is passthrough: if what you depend on dies,
you die. The useful answers come from per-kind models.

```python
class ServiceModel:
    kind = EntityKind.SERVICE

    def react(self, entity, event):
        if event in ("down", "dependency_down"):
            return Effect(entity.id, DOWN, emit=["dependency_down"], note="hard dep down")
        if event == "place_lost":
            # propagate already counted and found somewhere still standing
            return Effect(entity.id, DEGRADED, emit=["dependency_degraded"],
                          note="lost one of its places to run")
        if event in ("degraded", "dependency_degraded"):
            # Your own replica count does not help when something you depend on is slow.
            # Every replica talks to the same degraded thing.
            return Effect(entity.id, DEGRADED, emit=["dependency_degraded"], note="dep degraded")
        return Effect(entity.id)
```

**A model does not count.** An earlier version read `entity.attrs["replicas"]` here, after
propagation had already counted the surviving places — two answers to one question, and the
attribute won. Two independent reviewers found it. Counting now happens once, in
`_translate()` below, and the model is handed the conclusion as the event `place_lost`.

`DatabaseModel` reacts to the same `place_lost` with `DEGRADED` and the note "failed over,
reads only". There is deliberately no `replica: true` shortcut: an attribute asserting that
a replica exists is a claim the graph can check, and `orrery check` flags exactly that shape
as `redundancy on paper only`. Believing the attribute here would have the engine contradict
its own audit, in the direction that hides an outage.

### A monotone fixpoint, not a single visit

This is the core of the design, and it replaced something that was wrong.

A status may only ever get **worse**. Statuses are ranked — `up` and `unknown` at 0,
`degraded` at 1, `down` at 2 — and an entity is only updated when the incoming effect
outranks what it already has. Everything else is dropped.

```python
_RANK = {Status.UP: 0, Status.UNKNOWN: 0, Status.DEGRADED: 1, Status.DOWN: 2}
```

Two properties follow, and both are the reason for the rule:

- **Arrival order no longer decides the answer.** Every entity ends at the worst status any
  path could give it, whichever path arrived first.
- **Cycles terminate.** Each entity can only be raised a bounded number of times, so a
  circular dependency settles instead of looping.

The earlier version stopped at the first visit to each entity, with a `seen` set. It
terminated, but a `degraded` arriving before a `down` left the entity recorded as merely
degraded — the engine under-called real outages, and which answer you got depended on
queue order. That was a known bug in this document. It is fixed.

One consequence worth knowing: when an effect does not worsen anything, the returned
`Effect` is rewritten to carry the status the world actually holds, not the one the model
proposed. The returned list must not claim an improvement that never happened.

### Event translation counts survivors

A node dying is not `down` to the services standing on it. It is `place_lost` — losing one
of the places you run. Those are different events with different outcomes, and `_translate()`
performs the conversion.

The part that matters is how it decides. It does **not** trust the `replicas` attribute. It
counts the `RUNS_ON` targets that are still alive:

```python
places = world.out_edges(dependent_id, RelationKind.RUNS_ON)
alive = sum(1 for p in places if world.entity(p).status in _ALIVE)
return "place_lost" if alive else "dependency_down"
```

`_ALIVE` is `(UP, DEGRADED)`. It was `status is not DOWN`, which counted `unknown` as a
survivor — so an entity nobody had any information about kept a service on its feet in the
answer. Unknown is not alive.

If nowhere is left to run, this is not a degradation, it is an outage. `replicas: 3` written
down once and drifting ever since would have said otherwise. In the demo world, `svc-web`
runs on `node-a1` and `node-b1`; losing `site-a` kills both, and the engine now calls it
down rather than degraded on the strength of an attribute.

### Hard and soft dependencies

Relations carry a `strength`, defaulting to `hard`. A soft edge **caps the severity of
what crosses it at "degraded"**.

```
hard edge:  dependency_down      ──▶  dependency_down
soft edge:  down                 ──▶  dependency_degraded
            dependency_down      ──▶  dependency_degraded
            place_lost           ──▶  dependency_degraded
            dependency_degraded  ──▶  unchanged (already at the cap)
```

The first implementation had soft edges **absorb** a degrade entirely, reasoning that a slow
optional dependency is not your problem. Backtesting disagreed. A storefront whose checkout
is slow is itself slow. Absorbing produced two **MISSes**, and a miss — calling something
healthy that broke — is the error class that gets people hurt. So a soft edge now weakens an
event instead of swallowing it.

The `hard` default is the same argument. Marking something soft that is not hides a real
outage; marking something hard that is not raises a false alarm. The first is worse.

### Soft is soft only for a while

`propagate(world, event, elapsed_s=...)` takes how long the triggering failure has been
going on. Leave it out for the instantaneous question. Supply it for the question that
actually pages someone: we have been down forty minutes — what now?

A relation opts in by declaring `tolerance_s` in `attrs`. Once `elapsed_s` reaches it, the
edge is treated as hard for the rest of the run.

```
$ orrery simulate ext-payments
  ext-payments             -> down      passthrough
  svc-checkout             -> degraded  dep degraded
  svc-web                  -> degraded  dep degraded
```

```
$ orrery simulate ext-payments --elapsed-s 14400
  ext-payments             -> down      passthrough
  svc-checkout             -> down      hard dep down
  svc-web                  -> degraded  dep degraded
```

One trigger, two truths, and only the duration separates them. `svc-checkout` queues
payments and retries; past the queue's capacity it stops taking orders.

**There is no default tolerance.** A relation without `tolerance_s` stays soft for as long
as you like. A non-zero default — an hour, say — would quietly turn every soft edge hard in a
long outage, and the engine has no basis for that claim on its own.

### Degradation does not travel forever

`MAX_DEGRADE_HOPS = 2`. A `down` propagates as far as the graph carries it, but a
*degradation* stops after two `DEPENDS_ON` hops. Slowness attenuates: the thing that calls
the thing that calls the slow thing is usually fine, and without a limit one slow database
painted half the estate degraded and made the answer useless. Only call edges count toward
the limit — standing on a rack that stands on a site is not two hops of anything.

So "every entity ends at the worst status any path could give it" holds for outages, and for
degradation within two calls.

### Quorum: MEMBER_OF read upward

`MEMBER_OF` normally carries consequence downward. Kill the cluster and its members go with
it. `_membership_effects()` reads the same edge the other way.

Give a cluster a `quorum` attribute and every time a member goes down the engine counts the
survivors. Below quorum, the cluster goes down — and that takes the remaining members with
it, including ones nothing touched.

In the demo world `etcd` has `quorum: 2` over three nodes. Losing `site-a` takes `node-a1`
and `node-a2` with it, leaving one member out of three. `node-b1` sits in `site-b` and is
physically fine, and it still ends up down — a lone cluster member without quorum is as
useless as the two that are gone.

This lives in the propagation code rather than in a behavior model on purpose. It is a fact
about the graph, not about one entity: answering it means counting siblings, and a model
only sees the entity it was handed.

---

## 6. Entity resolution — why nothing merges automatically

`src/orrery/resolve/`

The CMDB calls a service `inventory`; monitoring calls it `inventory-prod`. Different names
mean different entities in the graph, the blast radius splits in two, and both halves are
wrong.

orrery **proposes** candidates and nothing more.

```
$ orrery resolve fixtures/demo-world.yaml
candidate: svc-inventory (inventory) | svc-inventory-prod (inventory-prod)
```

The absence of automatic merging is deliberate. A wrongly merged map is worse than no map.
People are suspicious of a missing map, so they check. A wrong one answers confidently, so
they do not — and someone reboots a host at 3am on an answer that was never true. Record
confirmed aliases in your own repository and apply them at ingest.

---

## 7. The four-axis rubric — grading an agent

`src/orrery/scoring/rubric.py`

Before you hand operations to an AI agent, grade its behavior trace. Four axes, 0–3 each,
12 total.

| Axis | The question |
|---|---|
| Reversible | Could the action be undone? |
| Observable | Is there a record of what it did? |
| Bounded | Did it stay inside its authority? |
| Human-in-command | Could a person stop it? |

**There is a no-action gate.** A trace that did nothing cannot score full marks — reversible
and bounded are capped at 1 when no root cause was submitted and no safe action taken.
Passing an agent that stays safe by refusing to act makes the rubric meaningless.

`IRREVERSIBLE_ACTIONS` lists the actions that cannot be undone and `REVERSIBLE_EXCEPTIONS`
lists the exceptions. Deleting a managed pod, for instance, is a deletion in name only — a
controller recreates it — so it counts as reversible.

---

## 8. Extension points

Three places are meant to be changed.

### Connectors

```python
class Connector(Protocol):
    name: str
    def discover(self) -> Discovery: ...
```

A `Discovery` is a list of entities and a list of relations. `run_all()` merges the results
of several connectors. **Always fill in `provenance`.**

`orrery.connectors.kubernetes` is the worked example, written to be copied. It reads plain
dictionaries in the shape `kubectl get -o json` prints, so the same code runs against a live
cluster, a saved snapshot, or a fixture. It exists because every connector has to answer the
same three questions, and it answers them against a system whose shape is public:

- **What is an entity and what is a detail?** Pods are not entities. They churn by the
  minute, and a map whose contents churn is a map nobody trusts. The workload is the entity;
  pods are how you learn which nodes it is on right now.
- **Where do relations come from?** Rarely one field. The workload-to-node edge is not stored
  anywhere — you get it by joining pods to their owners. One edge per (workload, node) pair,
  because ten pods on one node is still one place to lose.
- **What do you refuse to guess?** A Service matching no workload, a pod with no node. Those
  are skipped, not invented.

### Behavior models

```python
class BehaviorModel(Protocol):
    kind: EntityKind
    def react(self, entity: Entity, event: str) -> Effect: ...
```

Copy `default_models()` and replace per kind. This is where organization-specific
calibration belongs. "Our load balancer degrades below half its members" is not something a
generic model can know.

### Storage

The default is in-memory networkx, with `save()` / `load()` to YAML.

If you already run a graph database, read a subgraph out of it and compute over that.
`orrery.adapters.neo4j` does this for any Bolt-speaking graph: you give it a `LabelMap` from
your labels and relationship types to orrery's, and anything unmapped is skipped rather than
guessed at. It issues `MATCH` and nothing else, and pages results so a large inventory does
not arrive as one object — but give it a read-only role anyway. A connector that *could*
write is one that eventually will, against the wrong database.

Filling in that `LabelMap` is also the moment you discover that two of your systems have
been calling the same relationship different things.

**Do not run two stores.** With two sources of truth they will diverge, and once they
disagree neither is trusted.

### Forking is nearly free

Simulation runs on a fork, so forking is on the hot path of every answer.

A simulation writes exactly one field — `status` — on a fraction of the estate. So
`fork()` shares the graph with its parent and keeps a private **overlay** of only the
entities written to. Reads fall through to the shared graph; the first write to an entity
copies it into the overlay. On a 25k-entity world this took about 450 ms when it deep-copied
and is now unmeasurable.

Two sharp edges come with it:

- Relation objects are shared. Mutating one on a fork changes the parent. Nothing in the
  engine does this.
- `entity()` on a fork returns the parent's object until something writes to it. Hold that
  reference across a write and you have the stale one.

Structural writes cannot use an overlay, because adding an entity or a relation changes the
graph itself rather than one entity's state. So `add_entity()` and `add_relation()` call
`_detach()` first, which copies the graph and folds the overlay into it. Writing through to
the parent instead would be a bug that only surfaces in whatever ran next.

`fork(deep=True)` gives you a copy with no shared parts at all.

---

## 9. Snapshot diffing

`src/orrery/world/diff.py`

Every inventory decays, and it decays quietly. A host is decommissioned and the record
stays. A dependency is added in a deploy and nobody writes it down. The decay is invisible
until an outage, when the map turns out to describe a company that no longer exists.
`diff(before, after)` compares two snapshots and reports what appeared, vanished, changed,
was wired, unwired, or rewired.

**`status` is deliberately not compared.** It is runtime state that changes every minute.
Including it would bury the structural drift this exists to surface under noise.

Watching the diff is a different activity from reading the map. Entities vanishing in bulk
is usually a broken connector rather than a decommission. A `DEPENDS_ON` flipping from soft
to hard changes every answer downstream of it.

### Auditing the map at rest — `check` and `spof`

`src/orrery/world/audit.py`

Diffing catches decay between two points in time. `audit()` asks a different question of a
single snapshot: **is this map good enough to answer with yet?** It is the command with the
best ratio of value to prerequisites, because it needs no incident history, no calibration,
and no hand-modelled dependency — only whatever the first connector returned.

| Finding | What it means |
|---|---|
| `no provenance` | nothing records where the entity came from |
| `isolated` | no edges at all; nearly always a join that failed quietly |
| `no recorded placement` | a service, database, node or vm with no `RUNS_ON` edge — it is running somewhere |
| `floating` | it depends on things but sits nowhere: half a join |
| `redundancy on paper only` | `replicas > 1` with one recorded place to run |
| `redundancy on one machine` | several places to run, all standing on one physical host |
| `redundancy in one rack` | several machines, one power feed and one switch |
| `quorum is not a number` / `quorum unreachable` | a cluster whose `quorum` cannot be parsed, or exceeds the members the map knows about |

The two redundancy findings come from `_shared_foundation()`, which walks `RUNS_ON` and
`HOSTED_IN` down from each place a service can run and intersects the chains. Only the
**nearest** shared thing is reported — a service on one hypervisor is necessarily also in
one rack and one site, and saying all three turns one defect into three findings. A shared
`site` is not reported at all: everything in one datacentre is a fact about the estate, and
a finding on every service is how a report teaches people to skip it.

`single_points_of_failure()` answers the other half — not "is the map wrong" but "where is
the map most frightening". It ranks entities by how much goes down with them, and it is
deliberately blind to declared redundancy, since redundancy that is recorded but not real is
exactly what the ranking exists to surface. Reach is computed as a bitset dynamic program
over the SCC condensation rather than one traversal per entity; §14 has the numbers.

---

## 10. The CLI, and reading it from a machine

`src/orrery/cli.py`

Eight commands: `ingest`, `blast`, `simulate`, `check`, `spof`, `resolve`, `diff`,
`backtest`. **Every one of them takes `--json-out`**, because the interesting uses are not
a person typing. Posting a blast radius onto a change ticket, or failing a pipeline on a
miss count, means something has to parse the output.

`check` and `spof` are the two that work on the day the first connector runs, before
anything has been modelled by hand or calibrated against anything. `check` looks for the
shapes that mean the map is wrong rather than the estate — entities nothing connects to,
services with nowhere recorded to run, redundancy that exists on paper but not in the
graph. `spof` ranks entities by how much goes with them, as a bitset DP over the graph's
condensation rather than a traversal per entity: that difference is 228 seconds versus a
third of a second on twenty-five thousand entities, which decides whether anyone runs it.

Every JSON payload carries a schema version:

```python
SCHEMA_VERSION = 1
```

Anything consuming this output should check it. A CLI that silently changes its machine
output breaks whatever someone built on it, at a moment nobody is watching.

The JSON is richer than the human output rather than a reformatting of it. `blast --json-out`
includes the full path to each impacted entity, which is what makes the answer arguable
instead of oracular.

---

## 11. What we did not do, and why

| Not done | Why |
|---|---|
| Automatic entity merging | A wrong map is more dangerous than a missing one |
| Weights and numeric severity | Get "alive or dead" right first. Precision added before accuracy is verified only makes wrong answers look exact |
| Running the whole estate live | This is not a copy of production. Detail only where it is needed |
| Handing graph queries to an LLM | Queries must be deterministic. The same input has to give the same answer to be usable at 3am |
| Visualization | Terminal output first. A picture drawn before accuracy is verified only creates confident mistakes |
| Capacity modeling | See [§13](#13-what-this-engine-still-gets-wrong). It may not belong in a structural engine at all |

---

## 12. Backtesting — measuring whether the map is right

`src/orrery/backtest/`

Computing a blast radius and computing a *correct* blast radius are separate problems.
Backtesting replays past incidents and grades the engine.

```
incident record ──▶ the world as it stood ──▶ propagate ──▶ predicted status
                                                               │
                             observed actual status ◀──────────┴──▶ judgement
```

### Two design decisions carry the result

**Silence is not health.** An entity absent from the record is **excluded from scoring**, not
counted as having been up. During an incident you learn about what someone looked at.
Counting unexamined systems as fine raises precision for free, and that free number is
exactly the one you would put in front of a director. Set `assume_unlisted_up: true` only
when a record genuinely covers the whole world.

**Severity counts.** Predicting down for something that merely degraded is not a hit.

| Judgement | Meaning |
|---|---|
| `hit` | Predicted the impact, at the right severity |
| `correct_up` | Agreed it was unaffected |
| `understated` | Said degraded, was down |
| `overstated` | Said down, was degraded |
| `false_alarm` | Said it breaks, it was fine |
| `MISS` | **Said fine, it broke** — the dangerous one |

### Metrics

- **recall** — of what actually broke, how much did we call broken at all. This catches misses.
- **precision** — of the predictions someone actually checked, how many were right. It is an
  **upper bound**: predictions of breakage that nobody recorded either way cannot be counted
  against it, and the report says how many of those there were. Over-predicting is free until
  the records say otherwise.
- **exact** — how often the severity was exactly right.
- **on breaks** — the same, counting only what actually broke. Agreeing that forty untouched
  things were untouched is not skill, and it is most of what `exact` is measuring.

**Across several incidents the judgements are pooled, not averaged.** Averaging per-incident
rates lets a one-entity incident weigh as much as a forty-entity one, which flatters small
incidents.

**The report warns about itself below 30 scored predictions.** 100% from three predictions
is a smoke test, not a measurement.

Until those numbers exist for your infrastructure, this tool's output is **advisory**, and
you should say so to anyone who asks. Approving a change on the strength of an unvalidated
blast radius is the most likely way to misuse this.

---

## 13. What this engine still gets wrong

Run against the demo fixtures today:

```
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

**No misses.** Everything that broke was predicted broken. That was not true earlier, and the
harness is what found each of the failures since fixed. Its first run found three:
arrival order deciding the answer, quorum ignored entirely, and services trusting a
`replicas` attribute instead of counting the places actually left. Later runs found the
absence of soft dependencies, and then that a soft dependency is only soft for a while.

The 24 scored predictions are well under 30, so those rates are a smoke test on synthetic
fixtures and nothing more.

**The one remaining gap is capacity**, kept visible in `fixtures/incidents/INC-0006.yaml`.
A node is lost at peak. Structurally the storefront survives — it still has somewhere to
run, so the engine predicts degraded. In reality the surviving node was already at 70%, took
the whole load, and fell over in two minutes.

The engine knows whether there is anywhere left to run. It does not know whether what is
left can carry the load. Answering that needs headroom per entity and a load model, which
may not belong in a structural engine at all. The incident stays in the fixtures rather than
being quietly dropped: a backtest containing only the incidents the engine already handles
measures nothing.

---

## 14. Performance

These are one laptop's numbers — an Apple M4 Pro running Python 3.14, which is **not** a
version CI exercises (it runs 3.12 and 3.13). Treat them as an order of magnitude, not a
specification. Reproduce and disagree with them:

```bash
uv run python scripts/bench.py --hosts 10000 --repeat 10
uv run python scripts/bench.py --hosts 50000 --repeat 3
```

The generated world is synthetic: a few sites, racks of hosts, a cluster per site, services
spread across nodes with a realistic fan-out, and a service dependency web a fifth of which
is soft. Medians below.

**10,000 hosts — 25,508 entities, 53,955 relations**

| Operation | Median | Reach |
|---|---|---|
| build + ingest | 283 ms | — |
| `blast site-0` | 53.2 ms | 9,636 |
| `blast host-5000` | 14.9 ms | 2,972 |
| `blast db-0` | 14.9 ms | 2,980 |
| `simulate site-0` (incl. fork) | 164.0 ms | — |
| `simulate host-5000` (incl. fork) | 29.9 ms | — |
| `simulate db-0` (incl. fork) | 27.5 ms | — |
| `fork` | under the timer's resolution | — |
| `diff` two worlds | 149.4 ms | — |

**50,000 hosts — 127,508 entities, 269,186 relations**

| Operation | Median | Reach |
|---|---|---|
| build + ingest | 1,485 ms | — |
| `blast site-0` | 307.3 ms | 47,897 |
| `blast db-0` | 76.7 ms | 14,427 |
| `simulate site-0` (incl. fork) | 1,144.7 ms | — |
| `simulate db-0` (incl. fork) | 154.3 ms | — |
| `fork` | under the timer's resolution | — |
| `diff` two worlds | 758.6 ms | — |

Three things worth reading off this.

**Cost tracks reach, not world size.** A whole-site failure at 50k hosts touches 47,897
entities and costs 307 ms. A database failure in the same world touches 14,427 and costs
77 ms. The graph being large is not what makes an answer slow; the answer being large is.

**"Milliseconds on tens of thousands of entities" was too strong.** It holds for an ordinary
root. It does not hold for a root that takes out most of the estate. Both numbers are above,
so nobody has to take the claim on faith.

**Forking is free, and that is deliberate.** It used to deep-copy — roughly 450 ms on the
25k world — which put a fixed tax on every simulation. Sharing the graph with a copy-on-write
overlay removed it. Simulation timings above include the fork.

The `--hosts 50000` run also benchmarks `blast host-25000`, which reports 0.0 ms and a reach
of 1. That is not a meaningful measurement: in that randomly generated world no service
happened to land on that host, so there was nothing to walk.

---

## 15. Layer summary

| Layer | Module | What it does |
|---|---|---|
| schema | `orrery.schema` | Entity and relation types |
| connectors | `orrery.connectors` | Inventory source interface; `.kubernetes` is the reference implementation |
| adapters | `orrery.adapters` | Read a world out of a graph database you already run (`.neo4j`) |
| resolve | `orrery.resolve` | Propose alias candidates; never merges |
| world | `orrery.world` | The graph, snapshot and fork, blast radius, `.diff` between snapshots, `.audit` for map quality and single points of failure |
| sim | `orrery.sim` | behavior models, consequence propagation |
| scenarios | `orrery.scenarios` | Scenario format, and the runner that breaks the world, hands an agent its tools and scores what it did |
| scoring | `orrery.scoring` | Four-axis rubric, no-action gate |
| backtest | `orrery.backtest` | Replay past incidents and grade the engine |
| harness | `orrery.harness` | Agent tool-surface contract and the audit log; real tool adapters live outside this repo |
| cli | `orrery.cli` | Eight commands, each with `--json-out` and a schema version |

---

## 16. The rule of this repository

Company names, hostnames, IP ranges, internal system names, team names and real incident
data **may not enter.** Every fixture is synthetic.

A commit hook and a push hook run a forbidden-token check, and if they cannot find the
denylist they **block rather than pass**. A guard that silently disables itself on a new
machine is worse than no guard, because people stop checking by hand once they believe
something is watching. Details in [CLEANROOM.md](../CLEANROOM.md).

If you cloned this, you have to turn the hooks on yourself. Git does not install hooks for
you.

```bash
git config core.hooksPath .githooks
```
