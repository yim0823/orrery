# Design notes

## Definition
Every server is on the map, and when you act, the consequence is computed.

## Level of detail
All entities exist as objects. Behavior is computed by per-kind models. Only the region an agent is currently acting on is materialized as running instances (kind/k3s, containerlab, cloud emulators). The agent sees the same tool surface either way.

## Five properties of a world (vs a QA environment)
1. Completeness: every server, service, database, network path and dependency is on the map.
2. Consequence physics: any action produces a computed, company-wide consequence.
3. Time: simulated time flows and can be accelerated.
4. Persistence and forking: state persists; worlds fork for parallel experiments.
5. Multiple actors: several agents, synthetic traffic and event generators act at once.

## Phase 1 deliverable
A complete map of one company and a correct blast-radius answer across the whole company.

## Open source parts used
Required: a graph store (in-memory networkx now; Neo4j/JanusGraph later), SimPy, kind/k3s, Chaos Mesh or Krkn.
Optional by need: Batfish (network reachability from configs), KWOK (modeled k8s nodes at scale), containerlab, LocalStack.
