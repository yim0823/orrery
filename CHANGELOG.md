# Changelog

Notable changes. Dates are the day the work landed on `main`.

The format is loosely [Keep a Changelog](https://keepachangelog.com/). Versions follow
semantic versioning, with the usual pre-1.0 caveat: minor versions may break things, and
this section of the file will say so when they do.

## [Unreleased]

Three reviewers were pointed at four proposed features. Two of the four were rejected,
one was cut down, one survived — and between them the reviewers found two defects that
were not in the proposals at all. Both were the same shape: one code path doing the right
thing and its twin quietly not.

### Added

- **`REACHED_VIA`: a service nobody can reach is broken, however healthy its process is.**
  This came out of a real map rather than a design session. A game's load balancers were
  loaded into a world, the appliance carrying six of its VIPs was killed, and `simulate`
  answered *nothing happens* — correct under the model as it stood, since the backends
  really do keep running, and useless, because no player could have connected. Modelling
  the edge as pool membership said the true half and stopped there.

  Reachability turns out to behave exactly like placement: several ways in are redundancy,
  one is a single point of failure, none is an outage. So it is counted by the same code —
  `_translate` now counts surviving alternatives for both `RUNS_ON` and `REACHED_VIA` — and
  the only difference is the word in the finding: `place_lost` is answered by another
  machine, `path_lost` by another route. On the map that prompted it, one appliance went
  from affecting nothing to taking down six services, including the game's login.

  It also settles the direction problem a reviewer raised and this changelog deferred:
  `service MEMBER_OF load_balancer` pointed the wrong way, since the pool does not depend
  on its members. `service REACHED_VIA load_balancer` points from dependent to
  depended-upon like every other edge.

### Fixed

- **A relation could never be confirmed by a second source.** `add_entity` merges
  provenance, and its docstring says merging is the point, because that is how something
  becomes cross-confirmed. `add_relation` replaced instead, so a second connector
  describing an edge the first already described dropped the first one's record. Every
  edge in a two-connector map was single-sourced however many connectors saw it. The
  merge is now symmetric, and the relation is replaced rather than mutated because
  relation objects are shared with the world a fork came from.
- **Two sources disagreeing about `strength` are settled by argument, not arrival order.**
  Hard wins, on the same reasoning the default rests on: calling a load-bearing
  dependency optional hides an outage, calling an optional one load-bearing raises a
  false alarm. The disagreement is recorded as a collision either way.
- **`blast`, `spof` and `simulate` disagreed about a load balancer.** `blast lb-edge`
  listed a backend that `simulate lb-edge` left untouched, and `spof` ranked the load
  balancer for carrying a pool it does not carry. The rule that a load balancer's death
  does not reach its members lived in `propagate` alone; the other two walked every
  membership edge. 0.2.0 unified the edge list after exactly this happened with network
  segments, and the membership rule then rebuilt the disagreement one level down. It is
  now a predicate next to the edge list, and all three read it.

## [0.3.0] — 2026-09-19

### Added

- **`vm` is its own entity kind, and `orrery check` reports redundancy that shares one
  machine.** Virtualization fakes redundancy without anyone meaning to: three Kubernetes
  nodes look like three places to fail, and if they are three virtual machines on one
  physical server they are one. Nothing inside the cluster can see this — Kubernetes does
  not know what it is standing on — so the map is the only place the question can be
  asked. Sharing a *site* is deliberately not reported; everything in one datacentre is a
  fact about the estate, and a finding on every service teaches people to skip the report.
- **`redundancy in one rack`.** A rack is one power feed and one top-of-rack switch, so
  two machines in it are two machines and one failure domain. It was previously silent,
  lumped in with the deliberate exclusion of `site`, which only ever had a justification
  for `site`: a shared datacentre is a fact about the estate, a shared rack is something
  someone can move a server out of this week. Only the **nearest** shared foundation is
  reported, so a service on one hypervisor still reads as `redundancy on one machine`
  rather than producing three findings for one defect.
- **The shipped demo world now contains the trap the documentation is about.** It gained a
  rack layer, a hypervisor with two virtual machines on it, and a service whose two
  Kubernetes nodes both stand on that one machine, so `orrery check` in the quickstart
  prints the finding rather than the README merely describing it. `spof` now ranks that
  hypervisor above the bare-metal hosts, which is the point.
- The documentation now names the **two layers** a map is made of — infrastructure (what
  sits on what, from an inventory) and call (who talks to whom, in no inventory) — and
  how to build the second without instrumenting every service. `docs/ARCHITECTURE.md`
  gained a section on the map audit (`check`, `spof`), which was undocumented.

- **`LICENSE` names a copyright holder.** The licence text shipped with
  `Copyright [yyyy] [name of copyright owner]` still in it, which is not a small
  omission: a licence with no named licensor grants nothing. It and the new `NOTICE` now
  say Apache-2.0, copyright TaeHyoung Yim.
- **The ownership caveat is withdrawn.** Earlier releases carried a warning that the
  author's right to license this was unsettled because the first two commits were
  authored on employer equipment. The author's position is that this is his own work and
  his to license, and the documentation says that plainly now instead of hedging. What
  the clean-room rule protects — that no employer's material is in this repository — is
  unchanged and still enforced by a fail-closed commit hook; that was always a separate
  question from copyright, and it is the one this repository can actually answer.
- **Published.** The repository is public, so the CI badge and the `git clone` line
  resolve for everyone rather than 404ing for everyone but the author, which is what they
  did for as long as they sat above a private repository.

### Fixed

- **The reason column could contradict the status beside it.** `orrery simulate rack-a1`
  printed `svc-checkout -> down   dep degraded`: it died, and its dependency got slower.
  When a weaker effect arrived by a shorter path the status was correctly carried over
  from the stronger one and the note was not, so the two halves of the row came from
  different events — and which half you got depended on traversal order. The reason is
  the column that turns an answer into a decision; it is the last one that may be wrong.
- **The concentration check unioned alternatives that the simulator treats as
  redundancy.** Walking down from each place collected *everything* below it, so a node
  recorded on two hypervisors — a live migration caught mid-inventory, or two connectors
  disagreeing — produced "2 places to run, all of them on h2, losing it loses all of
  them" while `propagate(h2 down)` said `degraded`. The audit now intersects `RUNS_ON`
  alternatives and unions `HOSTED_IN` containers, because those two edges mean opposite
  things. An audit that the simulator beside it denies is worse than no audit.
- **A place that is the foundation of another place was invisible.** One pod on a node
  and one instance on the host that node stands on are two places on one machine. The
  walk looked only *below* each place, never at the place itself, so it reported this as
  a shared *rack*: the right alarm with the wrong fix attached. A place is now its own
  carrier.
- **`redundancy in one rack` is silent in an estate with one rack.** The same reasoning
  that keeps `site` quiet, which the first version of the finding failed to apply to
  itself: where there is one rack, "all in rack-1" is true of every service in the
  company. A twelve-service, one-rack estate produced twelve findings, at the top of the
  report, since the report sorts by count.
- **Databases are checked for concentration too.** The check asked only about services,
  leaving out a primary and its replica on one hypervisor — the version of this defect
  that predates Kubernetes.
- **The nearest shared foundation is chosen structurally, not by hop count.** Two places
  can reach the same carrier by paths of different lengths, and then hop counting answers
  differently depending on which place you measure from; ties were broken by the hash
  seed, so the same map named different hosts in different processes. The nearest carrier
  is now the one that itself stands on the most, and the depth cap — which silently
  returned "nothing found" past six hops — is gone.
- **Four mermaid diagrams did not render.** `call` is a flowchart keyword (`click X call
  fn()`), so using it as a subgraph id was a parse error — the two-layer diagram, the one
  readers are pointed at first, showed a red error box on GitHub in all four documents
  that carry it. `scripts/check_mermaid.mjs` now parses every diagram in CI, because a
  Python test suite was never going to notice this.
- **`docs/ARCHITECTURE.md` §4–5 documented an engine that has not existed since 0.2.0.**
  `_IMPACT_EDGES`, `node_lost`, `DEFAULT_TOLERANCE_S`, `_quorum_failures`, a `ServiceModel`
  reading `attrs["replicas"]`, and "`CONNECTS_TO` is the only one not used for impact
  propagation" — every one of them removed or renamed two releases ago, in the section
  that describes the core of the engine, in a document the README called "kept current".
  `MAX_DEGRADE_HOPS` was never documented at all. `scripts/check_readme.py` now covers
  the architecture documents too, so their command output cannot drift again; the prose
  had to be read by hand.
- **`docs/ADOPTING.md` told readers to install someone else's package.**
  `dependencies = ["orrery>=0.1"]` — `orrery` on PyPI is an unrelated project and this one
  is unpublished. The distribution is now named **`orrery-engine`**, and the guide shows a
  git dependency pinned to a commit, which resolves by URL rather than by name. The import
  and the command stay `orrery`: they are what the documentation types and what people
  say, and neither is a name PyPI gets to decide.
- **The Korean README's modelling example did not ingest** (a missing `name`, a relation
  to an entity that was never declared) and taught `replicas` / `replica: true`, which the
  English copy explicitly tells you not to write. It also claimed four of the five
  relation kinds propagate impact; all five do. It gained the hard/soft dependency
  section, the tolerance section and the command table it never had.
- Smaller, all of them false as written: `README.md` claiming a `spof` column measured by
  a benchmark that does not time `spof`; `world.save()` shown writing into a directory it
  does not create; connectors described as living only outside the repo on one page and a
  shipped Kubernetes connector named on another; three different accounts of what the
  backtest harness found on its first run; `docs/VISION.md` listing two of four runtime
  dependencies; two anchors pointing at section numbers that had shifted.
- **The claims this project is most exposed on now say they are a survey.** "The only
  artifact in this space" and "none of them score their own map" were stated as fact with
  nothing in the repository behind them. What is true is that we looked and did not find
  one, and that being shown otherwise would be worth more than being right.
- **`README.ko.md` documented behavior the engine does not have.** Its `simulate` output
  showed `replicas=3` and `replicas=1` as reasons and the prose explained the result by
  replica counts. The engine counts surviving `RUNS_ON` edges and has not read a replica
  attribute for two releases; the Korean README was teaching a model of the tool that was
  wrong. Its connector example also called `EntityKind.host`, which raises
  `AttributeError`. Both corrected, along with a status table that still claimed
  backtesting and snapshot diffing were unbuilt.

## [0.2.0] — 2026-09-19

Four adversarial reviewers were pointed at 0.1.0 and told to break it. They did. This
release is what they found, and the most useful thing in it is not any single fix — it is
that a mutation sweep broke the source in twenty-two small ways and **nineteen of those
mutations passed 140 green tests**. Test count is not evidence.

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
