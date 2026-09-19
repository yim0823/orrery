# Changelog

Notable changes. Dates are the day the work landed on `main`.

The format is loosely [Keep a Changelog](https://keepachangelog.com/). Versions follow
semantic versioning, with the usual pre-1.0 caveat: minor versions may break things, and
this section of the file will say so when they do.

## [0.3.0] — 2026-09-19

### Added

- **`vm` is its own entity kind, and `orrery check` reports redundancy that shares one
  machine.** Virtualization fakes redundancy without anyone meaning to: three Kubernetes
  nodes look like three places to fail, and if they are three virtual machines on one
  physical server they are one. Nothing inside the cluster can see this — Kubernetes does
  not know what it is standing on — so the map is the only place the question can be
  asked. Sharing a *site* is deliberately not reported; everything in one datacentre is a
  fact about the estate, and a finding on every service teaches people to skip the report.
- The documentation now names the **two layers** a map is made of — infrastructure (what
  sits on what, from an inventory) and call (who talks to whom, in no inventory) — and
  how to build the second without instrumenting every service.

## [0.2.0] — 2026-09-19

Four adversarial reviewers were pointed at 0.1.0 and told to break it. They did. This
release is what they found, and the most useful thing in it is not any single fix — it is
that a mutation sweep broke the source in twenty-two small ways and **nineteen of those
mutations passed 142 green tests**. Test count is not evidence.

### Fixed — the engine was wrong

- **A service on forty-nine healthy nodes was reported down when one rebooted.** Two
  reviewers found this independently, and 0.1.0's changelog claimed it was already fixed:
  propagation counted surviving places to run, and then the behavior model threw the count
  away and re-derived redundancy from an `attrs["replicas"]` that defaulted to 1. Behavior
  models no longer count anything. Whatever needs the graph is decided in `propagate` and
  arrives encoded in the event.
- **A network segment failing computed as affecting nothing.** `CONNECTS_TO` was excluded
  from impact, justified as "communication is symmetric". A host's sole attachment to a
  VLAN is not symmetric, and the exclusion made an entire class of outage invisible. Worse,
  `blast` and `simulate` kept separate edge lists and so disagreed with each other; there
  is now one list.
- **`replica: true` is gone.** An attribute asserting a replica exists is a claim the graph
  can check, and `orrery check` already flags that exact shape as redundancy on paper only.
  Believing it in the engine had the tool contradict its own audit, in the direction that
  hides an outage — telling it a database was down got you "degraded". Redundancy is now
  somewhere to run, counted.
- **A database on a degraded host was reported healthy.** `DatabaseModel` returned no
  status for degradation at all.
- **An unreachable node counted as a place to run.** Survivors were "not DOWN", which let
  UNKNOWN through. A node the inventory cannot reach during the incident you are simulating
  is not evidence of a survivor.
- **Degradation had no horizon.** One shared optional sink painted every service that
  transitively touched it. `MAX_DEGRADE_HOPS` bounds it, and says in its own docstring that
  it is a blunt instrument standing in for capacity modelling.
- **Losing a backend did not thin a load balancer's pool.** `MEMBER_OF` was only read
  upward when quorum was declared.
- **Unknown event names propagated nothing and returned success.** A typo produced a clean,
  empty, confident result.

### Fixed — the fork leaked

- **Forking a fork returned a pristine world.** `run_scenario` and `replay` both fork
  whatever they are handed, so a world that had already failed came back healthy.
- **Adding an entity to a fork edited the parent.** `MultiDiGraph.copy()` is shallow, so
  the merge in `add_entity` reached through the shared `Entity` object. The `_detach`
  docstring warned about exactly this bug while causing it.

### Fixed — the rubric mis-scored

- `irreversible_count` was inferred from the `reversible` axis, so five irreversible
  actions reported as three, and a scenario scoring rule that lowered the axis
  manufactured irreversible actions that never happened.
- The runner and the rubric disagreed about what a read is, so an agent that called
  `get_series` before acting was marked down for acting without evidence.
- Escalating with no declared deadline scored as late.

### Fixed — the backtest could not see its own blind spot

- **Precision counted only predictions somebody checked.** Over-prediction was free: an
  engine painting half the estate red is never wrong about the half nobody looked at. The
  report now prints how many predictions went unverified and says precision is an upper
  bound.
- **`exact` was padded by healthy entities.** A record listing forty `up` rows lifted the
  score without the engine getting anything hard right. `on breaks` counts only what broke.
- **A typo in an observed id silently removed a judgement**, which flattered the score. It
  is now refused with the id and the file.
- **Replaying every incident against one snapshot is flagged** as measuring hindsight
  rather than prediction.

### Changed

- **`orrery spof` went from 228 s to 0.3 s on 25k entities** — measured, not estimated.
  It was a traversal per entity; it is now a bitset DP over the graph's condensation, and
  a test asserts the fast answer equals the slow one. 63k entities takes a second.
- **Quorum checking was quadratic per cluster**: 5.2 s for a four-thousand-member cluster,
  which is the shape a real one has. Member counts are memoized within a call: 108 ms.
- Six entity kinds added — `queue`, `storage`, `dns`, `certificate`, `cdn`, `job` — each
  because it fails differently from everything already there.
- The file format reads what it writes. Ingesting a saved snapshot crashed on duplicate
  provenance, which made "edit a snapshot and try again" impossible.
- Unknown ids, unknown events, unknown kinds and missing files produce a sentence and a
  suggestion instead of a traceback.
- Ids arriving twice under different names or kinds are reported rather than merged in
  silence.
- The commit hook tells an outside contributor how to satisfy it. Fail-closed with no
  documented way through is a wall, not a guard.
- Neo4j paging orders its queries. `SKIP`/`LIMIT` across separate queries without an
  `ORDER BY` silently duplicates or drops rows.

### Removed

Dead code the mutation sweep found by breaking it with no test failing: `Report.worst`,
`Comparison.recall`, and an unreachable branch in the soft-edge cap.

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

### Fixed on a fresh clone

- `uv sync` did not install the test dependencies, so `uv run pytest` failed for anyone
  following the contributing guide while passing for everyone who already had an
  environment. Dev dependencies moved to a `[dependency-groups]` entry, which a plain
  sync installs. `scripts/smoke.sh` now runs the documented setup end to end in CI, so
  this class of breakage is caught by the build rather than by a new contributor.

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
