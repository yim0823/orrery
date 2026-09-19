# 설계

README가 "무엇에 쓰는가"라면, 이 문서는 "어떻게 만들어졌는가"입니다.

## Layers

1. **schema** — entity and relation types (`Site`, `Host`, `Cluster`, `Node`, `Service`, `Database`, `LoadBalancer`, `NetworkSegment`; `RUNS_ON`, `DEPENDS_ON`, `CONNECTS_TO`, `MEMBER_OF`, `HOSTED_IN`).
2. **connectors** — the interface every inventory source implements (`discover()` yields entities and relations with provenance). Concrete connectors to a company's systems live **outside** this repo.
3. **resolve** — entity resolution: the same service is named differently by every system. Candidates are clustered; **humans confirm**; nothing auto-merges.
4. **world** — the graph, snapshots/forks, and the first query that makes it a world: *blast radius* ("if this goes down, what dies?").
5. **sim** — a clock (SimPy), behavior models per entity type, and consequence propagation over dependency edges.
6. **scenarios** — a scenario format: initial state, injections, observation conditions, boundaries, answer, scoring rules.
7. **scoring** — the four-axis rubric (Reversible, Observable, Bounded, Human-in-command), with the no-action gate and deploy thresholds.
8. **harness** — the tool-surface abstraction an agent-under-test acts through, with an audit log.

## Hard rule: this repo is company-agnostic

No company names, hostnames, IP ranges, internal system names, team names or scenario data from any real company may enter this repo. Fixtures are synthetic. `scripts/check_identifiers.py` enforces a denylist that is kept **outside** this repo (see `CLEANROOM.md`).

Company-specific worlds are separate repos that depend on `orrery`. `orrery` never depends on them.

## Quick start

```bash
uv sync --extra dev
uv run orrery ingest fixtures/demo-world.yaml
uv run orrery blast svc-checkout
uv run pytest
```

## Status

v0.0.1 — scaffold. Phase 1 target: a complete map of one company and a correct blast-radius answer across the whole company.
