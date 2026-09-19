# Contributing to orrery

Thanks for taking a look. This document covers the one rule that is unusual here, then
the ordinary stuff.

## The clean-room rule

**No organization-specific material may enter this repository.** That means company
names, hostnames, IP ranges, internal system names, team or person names, and data
derived from real incidents or real inventory.

This is not stylistic. The engine is published openly so that anyone can use it, and a
single leaked identifier is not something you can take back. It also applies to notes
*about* this repo — investigation write-ups and integration plans belong elsewhere,
even when they discuss orrery itself.

All fixtures are synthetic. If you need an example, invent one.

A commit hook enforces a denylist. Enable it after cloning — git does not install hooks
for you:

```bash
git config core.hooksPath .githooks
```

The hook reads a denylist kept **outside** the repository (the list itself is sensitive).
Point to yours with `ORRERY_DENYLIST=/path/to/denylist.txt`. If it cannot find one, it
**blocks the commit** rather than letting it through — a guard that silently disables
itself is worse than no guard, because you stop checking by hand.

Connectors to real systems belong in your own repository, which depends on orrery.
orrery never depends on it.

## Development

```bash
uv sync --all-extras --dev
uv run pytest            # 15 tests
uv run ruff check .
```

Run the demo end to end before opening a PR:

```bash
uv run orrery ingest fixtures/demo-world.yaml
uv run orrery blast host-a1
uv run orrery simulate host-a1
```

## What is most useful right now

In rough order of value:

1. **Soft dependencies.** `orrery backtest fixtures/incidents` reports three
   `overstated` results, all the same cause: a service that queues and retries when a
   dependency dies is modeled as dying with it. Relations need a strength, and
   behavior models need to read it. This is the gap the harness was built to expose.
2. **Event severity in propagation.** Today, when a degrade and a down reach the same
   entity, arrival order decides the outcome. Events need severity so the stronger one
   wins regardless of order.
3. **Behavior models.** The built-ins are deliberately simple. Load balancers, stateful
   sets, and cross-region failover all deserve better ones.
4. **A scenario runner.** The format exists in `orrery.scenarios`; nothing executes it.

## Pull requests

- One concern per PR.
- Add a test that fails without your change.
- If you change how impact is computed, say in the description what would now be
  answered differently, and why that is more correct.
- Public API changes go in the same PR as the doc change.

## Reporting a problem

Open an issue with the world fixture that reproduces it — synthetic, small, and
self-contained. "Blast radius looks wrong on our infrastructure" cannot be acted on;
twelve lines of YAML can.

## License

Contributions are licensed under [Apache-2.0](LICENSE), same as the project.
