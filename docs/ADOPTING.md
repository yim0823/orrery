# Adopting orrery inside a company

This is the path from an empty repository to a blast radius your team believes. It
assumes the thing you actually have: several inventory systems that disagree with each
other, no single map, and a wiki page somebody wrote in 2023.

---

## First: depend on it, do not fork it

The instinct is to fork. Resist it for as long as you can.

| | Depend | Fork |
|---|---|---|
| Upgrades | `uv lock --upgrade-package orrery` | merge conflicts forever |
| Your connectors | live in your repo either way | live in your repo either way |
| Behavior models | subclass or replace at runtime | ditto |
| Clean-room | enforced by the boundary | you have to remember |

Everything you are likely to change — connectors, entity ids, behavior models, the
mapping from your labels to orrery's — is designed as an extension point precisely so
you do not have to touch the engine. Fork only when you need to change how propagation
itself works, and when you do, send the change back rather than carrying it.

```toml
# your-repo/pyproject.toml
[project]
dependencies = ["orrery>=0.1"]
```

Pin it. An engine that decides what is safe to restart is not a dependency you want
floating.

---

## Repository layout

One repository, yours, private. orrery stays outside it.

```
your-world/
  pyproject.toml            # depends on orrery
  connectors/               # one module per inventory source
    cmdb.py
    kubernetes.py
    monitoring.py
  models/                   # behavior models calibrated to your estate
    load_balancer.py
  aliases.yaml              # confirmed entity-resolution decisions
  incidents/                # past incidents, for backtesting
    2026-03-14-psu.yaml
    world-2026-03.yaml      # the map as it stood that day
  snapshots/                # daily ingests, for diffing
  denylist.txt              # identifiers that must never reach the engine repo
```

The dependency arrow points one way and never the other:

```
your-world  ──depends on──▶  orrery
orrery      ──never───────▶  your-world
```

If you ever find yourself wanting orrery to import something from your repository, the
thing you want belongs in an extension point instead. Open an issue.

---

## Step 1 — one connector, one question

Do not model the estate. Model one service well enough to answer one question, and find
out whether anyone cares about the answer.

Pick the system that already knows the most: usually the CMDB or asset inventory.

```python
from orrery.connectors.base import Discovery
from orrery.schema import Entity, EntityKind, Relation, RelationKind, Provenance

class CmdbConnector:
    name = "cmdb"

    def discover(self) -> Discovery:
        d = Discovery()
        for row in cmdb.servers():          # your read-only client
            eid = f"cmdb:host/{row['id']}"
            d.entities.append(Entity(
                id=eid,
                kind=EntityKind.HOST,
                name=row["hostname"],
                attrs={"env": row["env"], "site": row["site"]},
                provenance=[Provenance(source=self.name, source_id=row["id"])],
            ))
            d.relations.append(Relation(
                src=eid, dst=f"cmdb:site/{row['site']}",
                kind=RelationKind.HOSTED_IN,
                provenance=[Provenance(source=self.name, source_id=row["id"])],
            ))
        return d
```

Read `orrery/connectors/kubernetes.py` before writing yours. It is there to be copied,
and it works through the three problems you are about to hit: what counts as an entity,
where relations come from when no field holds them, and what to refuse to guess.

**Credentials.** Use a read-only account, scoped to the systems you named. A connector
that *could* write is a connector that eventually will, against the wrong environment,
at the wrong hour. Do not reuse an existing automation account whose scope you have not
personally read.

---

## Step 2 — entity ids that survive contact

This decision is expensive to change later and cheap to get right now.

An id must be **stable** (the same server next week has the same id), **unique across
sources**, and **readable enough to argue with** in a terminal at 3am.

```
cmdb:host/44219                  good — stable, sourced, greppable
k8s:prod-1:deployment/shop/web   good — namespaced by cluster
i-0a1b2c3d4e                     fine if the cloud id is your real identity
web-server-3                     bad — unstable, ambiguous across environments
44219                            bad — meaningless in six months
```

Prefixing by source has an additional payoff: when two systems describe the same server,
you get two entities instead of one silently merged wrong entity, and resolution becomes
a decision someone makes rather than a collision nobody noticed.

---

## Step 2.5 — look at the map before believing any of it

The moment the first connector runs, two commands are worth more than any amount of
staring at YAML.

```bash
orrery check    # is this map any good?
orrery spof     # what is most dangerous?
```

`check` looks for the shapes that mean the map is wrong rather than the estate. The two
that turn up most on a first ingest:

- **isolated** — nothing connects to it. Almost always a join that failed quietly, not a
  server nobody uses. Expect a lot of these while you are still resolving names.
- **no recorded placement** — a service with nowhere to run, which means your placement
  source is missing or not joined yet.

It also reports how much of the map rests on one source. Early on that number is 100%,
and it is worth watching it fall as connectors are added; an entity two systems agree on
is an entity you can act on.

`spof` ranks entities by how much goes with them. This is usually the first output that
changes what a team does, because the top of the list is reliably something nobody had
thought of. It ignores `replicas` on purpose — redundancy that is written down but not
real is precisely what you are looking for.

Neither is a verdict. Both are lists of things worth a person's attention.

## Step 3 — the same thing under three names

Your CMDB calls it `inventory`. Monitoring calls it `inventory-prod`. The deploy pipeline
calls it `shop/inventory`. In the graph that is three entities, each with a third of the
truth, and all three blast radii are wrong.

```bash
orrery resolve discovery.yaml --json-out
```

orrery proposes candidates. It never merges. Confirmed decisions belong in your repo,
reviewed like code:

```yaml
# aliases.yaml
- canonical: cmdb:service/inventory
  aliases:
    - mon:service/inventory-prod
    - k8s:prod-1:deployment/shop/inventory
  confirmed_by: "reviewed 2026-03-02, ticket OPS-4412"
```

The reason merging is manual is worth internalizing, because you will be tempted to
automate it the first week. A missing map makes people suspicious, so they check. A
wrongly merged map answers confidently, so they do not — and then someone reboots a host
on an answer that was never true.

---

## Step 4 — the modelling decisions that decide your accuracy

Four choices carry almost all of the accuracy. None of them can be discovered from an
inventory system; every one comes out of a person's head.

**Hard or soft.** Does the dependent survive without it?

```yaml
- {src: svc-checkout, dst: db-orders,    kind: DEPENDS_ON}                  # hard: it stops
- {src: svc-checkout, dst: ext-payments, kind: DEPENDS_ON, strength: soft}  # soft: it queues
```

Default to hard. Marking something soft when it is not hides a real outage; marking it
hard when it is not raises a false alarm. The first hurts people, the second annoys them.

**How long soft stays soft.** A retry queue has a size.

```yaml
- {src: svc-checkout, dst: ext-payments, kind: DEPENDS_ON,
   strength: soft, attrs: {tolerance_s: 2400}}
```

Then ask the question that actually pages someone:

```bash
orrery simulate ext-payments --elapsed-s 600     # ten minutes in: degraded
orrery simulate ext-payments --elapsed-s 14400   # four hours in: down
```

**Quorum.** A three-member cluster shrugs off one loss and dies at two — and when it
dies it takes the healthy survivor with it.

```yaml
- {id: etcd-prod, kind: cluster, attrs: {quorum: 2}}
```

**Where things actually run.** Do not write `replicas` by hand and trust it. The engine
counts surviving `RUNS_ON` targets, so a connector that emits real placement is worth
more than an attribute someone typed in once and that has been drifting since.

---

## Step 5 — prove it before anyone relies on it

This is the step teams skip, and it is the one that decides whether the map gets used.

Take incidents you already have postmortems for. Write each as a record of what broke and
what was *observed* to break.

```yaml
id: 2026-03-14-psu
title: "Host lost a PSU; checkout stopped taking orders"
world: world-2026-03.yaml    # the map as it stood that day, not today's
trigger: cmdb:host/44219
event: down
elapsed_s: 2700
observed:
  cmdb:service/inventory: down
  cmdb:service/checkout: down
  cmdb:service/web: degraded
```

```bash
orrery backtest incidents/
```

Two rules make the numbers mean something.

**Silence is not health.** An entity your record says nothing about is skipped, not
scored as fine. During an incident you learn about what someone looked at. Counting the
unexamined as healthy inflates your score for free — and that inflated number is exactly
the one you would put in front of a director.

**Severity counts.** Predicting "down" for something that merely slowed is not a hit.

Read `MISS` first and everything else second. A miss — predicting healthy for something
that broke — is the failure that hurts people. A false alarm is the failure that gets the
tool ignored. Both matter; they are not the same kind of wrong.

Ten incidents is a smoke test. Thirty starts to be a measurement, and the report says so
itself below that line.

**This is also your only credible artifact.** "We built a dependency graph" persuades
nobody who has watched a CMDB rot. "We replayed our last thirty incidents and the map
predicted the impact in twenty-six, missed two, and over-called two" is an argument.

---

## Step 6 — operate it, or watch it rot

Every inventory decays, quietly, and the decay is invisible until the outage that reveals
the map describes a company that no longer exists.

```bash
# nightly
orrery ingest discovery.yaml --out snapshots/$(date +%F).yaml
orrery diff snapshots/$(date -d yesterday +%F).yaml snapshots/$(date +%F).yaml --json-out
```

Watch the diff, not the map. Things that should make somebody look:

- entities vanishing in bulk — usually a broken connector, not a decommission
- a `DEPENDS_ON` flipping from soft to hard, which changes every answer downstream of it
- a service's `RUNS_ON` count dropping, which means redundancy left without a ticket

Re-run the backtest on a schedule too. Accuracy is not a property you establish once; it
is a property that decays alongside the map.

---

## Step 7 — putting it in front of people

In rough order of how quickly it earns trust:

1. **A command.** `orrery blast <id>` in a terminal, during an incident. Nothing to
   deploy, nothing to believe in advance.
2. **A change-review comment.** Post the blast radius onto the change ticket
   automatically. `--json-out` gives you the path to each impacted entity, which is what
   makes it arguable rather than oracular.
3. **An incident channel bot.** Someone names a host; the bot replies with what dies and
   what merely degrades.
4. **An approval gate.** Only after the backtest numbers are good enough that you would
   defend them. Gating changes on an unvalidated map is the fastest way to have the whole
   thing switched off.

Lead with the second answer, never the first. "Five entities impacted" is noise.
"Checkout stops, because inventory is down to one replica" is a decision.

---

## The boundary, and why it is worth the friction

If you contribute anything back — and the engine gets better for everyone if you do — the
line has to hold in both directions.

Never into the engine: company names, hostnames, IP ranges, internal system names, team or
person names, real inventory, real incident data. Not even in a comment, a test fixture,
or a document *about* the integration. That last one is the trap: an integration plan or
an investigation note is about your systems, so it names them, so it belongs in your
repository no matter how much it discusses orrery.

The engine repository enforces this with a commit hook reading a denylist kept outside
it. Set it up in your fork or clone; git does not install hooks for you:

```bash
git config core.hooksPath .githooks
export ORRERY_DENYLIST=/path/to/your/denylist.txt
```

The hook **refuses to run without a denylist** rather than passing. A guard that silently
disables itself on a new machine is worse than no guard, because you stop checking by
hand once you believe something else is watching.

What you can safely contribute: behavior models for public technologies, connectors for
public systems, engine fixes, and — most valuable — the *shape* of what your backtest
found wrong, with your numbers left out.

---

## Honest expectations

- **A first blast radius from one connector:** days.
- **A map people trust in an incident:** a quarter, and most of that is resolution and
  modelling decisions, not code.
- **Backtest numbers worth showing anyone:** as long as it takes to write up thirty
  incidents properly, which is the real cost and the real value.

The engine is the small part. What you are actually building is a written record of how
your estate is wired, maintained by people who are on call for it. orrery's contribution
is making that record answer a question, so that keeping it accurate has a payoff on an
ordinary Tuesday instead of only during an outage.
