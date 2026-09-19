# Changelog

Notable changes. Dates are the day the work landed on `main`.

The format is loosely [Keep a Changelog](https://keepachangelog.com/). Versions follow
semantic versioning, with the usual pre-1.0 caveat: minor versions may break things, and
this section of the file will say so when they do.

## [0.1.0] — 2026-09-19

First version worth depending on. The engine answers its question, measures whether the
answer was right, and has somewhere for your own data to enter.

### Added

- **Backtesting** (`orrery backtest`). Write past incidents as records of what broke and
  what was *observed* to break; the engine replays each and scores itself. Two rules keep
  the numbers meaningful: an entity a record says nothing about is skipped rather than
  counted healthy, and predicting "down" for something that merely degraded is scored as
  overstated rather than as a hit. Reports below 30 predictions say so themselves.
- **Hard and soft dependencies.** A relation carries a `strength`. A soft edge caps what
  crosses it at "degraded" — losing something you can live without slows you down, it
  does not kill you. Defaults to hard.
- **A clock.** Soft relations may declare `tolerance_s`, and callers pass `elapsed_s`, so
  the engine can answer "we have been down forty minutes, what now?" and not only "what
  happens the instant this dies".
- **Quorum.** A cluster with a `quorum` attribute dies when too few members survive, and
  takes the survivors with it.
- **`orrery check`** — is this map any good? Entities nothing connects to, services with
  nowhere recorded to run, redundancy that exists on paper but not in the graph, quorums
  that cannot be reached, and how much of the map rests on a single source. On the demo
  world it immediately finds an unresolved alias and a service claiming two replicas with
  one place to run.
- **`orrery spof`** — what is most dangerous? Entities ranked by how much goes with them.
  Needs no incident history and no calibration, and deliberately ignores `replicas`:
  redundancy recorded but not real is what the list exists to surface.
- **Snapshot diffing** (`orrery diff`). What appeared, vanished, or was rewired between
  two ingests. Status is deliberately excluded: runtime state changes every minute and
  would bury the structural drift this exists to surface.
- **Machine-readable output.** `--json-out` on every command, carrying a
  `schema` version so anything built on it can check before trusting.
- **`orrery.adapters.neo4j`** — read a world out of a graph you already run, with your
  labels mapped to orrery's. No second source of truth.
- **`orrery.connectors.kubernetes`** — the reference connector, written to be copied. It
  reads `kubectl get -o json`, so the same code runs against a live cluster, a saved
  snapshot, or a fixture.
- **Scenario runner** (`orrery.scenarios.run_scenario`). Breaks a world, hands an agent
  its tools, records every call, and scores the result against the four-axis rubric.
- **`scripts/bench.py`** — the measurements behind the performance claims.
- **[docs/ADOPTING.md](docs/ADOPTING.md)** — the path from an empty repository to a blast
  radius a team believes.
- Clean-room enforcement as a commit and push hook that **refuses to run without a
  denylist** rather than passing.

### Fixed

Four defects in propagation, three of them found by the backtest harness on its first run.

- **Arrival order decided the answer.** Traversal stopped at the first visit to each
  entity, so a degrade reaching a service before a down left it recorded as merely
  degraded. Propagation is now a monotone fixpoint: a status may only worsen, so the
  result is the same whichever path arrives first, and cycles still terminate.
- **A second `propagate()` call could improve a status.** It cannot now.
- **Clusters ignored quorum.** Losing two of three members left the cluster predicted
  healthy, and the lone survivor with it.
- **Services trusted a number instead of counting.** `replicas` was read from attributes,
  so a service whose only two nodes were both dead came out degraded. The engine counts
  surviving places to run; an attribute written down once drifts, the graph does not.
- A degraded dependency no longer consults the dependent's own replica count. Every
  replica talks to the same slow thing.

### Changed

- **Forking is effectively free.** It deep-copied the whole graph — 451 ms on 25k
  entities, on the path of every simulation, and almost all of it thrown away since a
  simulation writes one field on a fraction of the estate. A fork now shares the graph
  and keeps private copies only of what it damages. `fork(deep=True)` for full isolation.
- English is the primary language for documentation; Korean versions live alongside.

### Known limits

- **Capacity is not modeled.** The engine knows whether somewhere is left to run, not
  whether the survivors can carry the load. `fixtures/incidents/INC-0006.yaml` keeps this
  visible rather than letting it be discovered during an outage.
- Accuracy has been measured only against synthetic incidents. Until it has been run
  against yours, the output is advisory, and should be described that way to anyone who
  asks.

## [0.0.1] — 2026-09-12

Scaffolding: world schema, connector interface, entity resolution, blast radius,
consequence propagation, scenario format, four-axis rubric, harness contract.
