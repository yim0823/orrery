# Where this is going

[ARCHITECTURE.md](ARCHITECTURE.md) describes what exists. This describes what it is
aimed at, and marks honestly how far along each part is. Read it if you are deciding
whether to build on this, and want to know whether the direction is one you would want
to be carried in.

## The definition

> Every server is on the map, and when you act, the consequence is computed.

## Level of detail

The thing that makes this tractable, and the thing most often misread.

Every entity exists as an object. Behavior comes from a model per kind. Only the region
someone is currently acting on ever needs to be materialized as something actually
running — a real cluster, a real network topology, a real emulator — and the agent or the
engineer sees the same tool surface either way.

This is not a copy of production. A copy of production costs what production costs, and
nobody builds one. A world is complete in its *objects* and selective in its *execution*,
which is what makes completeness affordable at all.

**Status:** the object half exists and works. Nothing is materialized as running
instances yet; that is the next large piece of work.

## What separates a world from a test environment

Five properties. A staging environment has the first one at best.

| | | Status |
|---|---|---|
| **Completeness** | every server, service, database, network path and dependency is on the map, not just the part you meant to test | built — the map is as complete as your connectors are |
| **Consequence physics** | any action produces a computed consequence across the whole estate, not only locally | built — `propagate`, with hard/soft edges, quorum, and a clock |
| **Time** | simulated time flows, and can be run faster than real time | partial — relations declare tolerances and callers pass elapsed time, but nothing advances a clock on its own. A SimPy wrapper existed for a while, used by nothing; carrying a dependency for a phase that has not started is a cost with no return, so it was removed |
| **Persistence and forking** | state persists, and worlds fork so experiments can run in parallel | built — `save`/`load`/`fork`, and forking is cheap enough to do per query |
| **Multiple actors** | several agents, synthetic traffic and event generators act at the same time | not built — one event, one settle, one agent |

## Phases

**Phase 1 — a correct map, and proof it is correct.** A complete map of one organization,
a blast radius that holds up across it, and backtest numbers against that organization's
real incidents.

*Status: the engine is here. The proof is not, and cannot be until someone runs it
against their own incidents. Nothing in this repository has been validated against a real
outage, and the synthetic fixtures deliberately include cases the engine still gets
wrong.*

**Phase 2 — a world that runs.** Selected regions materialized as real running systems,
so an agent's action has a real effect rather than a modeled one, with the modeled
estate around it.

**Phase 3 — a world with weather.** Time that advances on its own, synthetic traffic,
several actors at once, and events that arrive while you are still dealing with the last
one — which is what an actual incident is, and what no evaluation currently reproduces.

## What this would be built on

Already used: `networkx` for the graph, `pydantic` for the schema, `pyyaml` for the file
format and `typer` for the CLI, with an optional Neo4j adapter for reading a graph you
already run. That is the whole dependency list, and it is short on purpose.

Likely later, by need rather than by plan:

| For | Candidates |
|---|---|
| materializing a cluster region | kind, k3s, KWOK for modeled nodes at scale |
| materializing a network region | containerlab, Batfish for reachability from configs |
| injecting failures for real | Chaos Mesh, krkn |
| cloud surfaces | LocalStack and similar emulators |

None of these are dependencies today, and each one is a decision to defer until something
concrete needs it. A dependency added for a phase that has not started is a dependency
you maintain for nothing.

## What would make this wrong

Worth stating, since a direction nobody can argue with is usually one nobody checked.

- **If blast radius turns out to be uninteresting in practice.** People might already know
  what depends on what, and the real gap might be elsewhere entirely. Backtesting against
  real incidents is how that gets found out, which is why it came before anything in
  Phase 2.
- **If accuracy plateaus below usefulness.** A map that is right 70% of the time may be
  worse than no map, because it is trusted 100% of the time.
- **If maintaining the map costs more than it saves.** This is how CMDBs die. Snapshot
  diffing exists to make the decay visible early enough to argue about.
