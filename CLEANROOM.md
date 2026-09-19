# Clean room rules

This engine is designed to be published as open source by its owner. To make that possible without a cleanup pass, the following are **forbidden in this repository**:

- real company names, product names of internal systems, team or person names
- real hostnames, IP addresses/ranges, domain names, account IDs
- real inventory data, topology exports, incident histories, scenarios derived from real incidents
- configuration, credentials, or connector implementations for any real system

- **notes and plans that name internal systems** — investigation notes, integration plans, and anything citing real figures or internal file paths. These live outside this repo even when they are about this repo.

Allowed: synthetic fixtures, generic connector interfaces, generic behavior models, generic scenario formats.

## Enforcement

`scripts/check_identifiers.py --denylist <path>` scans the tree for tokens listed in a denylist file. The denylist itself may contain sensitive tokens, so it lives **outside this repo** (in the company-specific repo, or in CI secrets) and is passed by path.

```bash
uv run python scripts/check_identifiers.py --denylist <path outside this repo>/denylist.txt
```

This is wired into `.githooks/pre-commit` and `.githooks/pre-push`, enabled with:

```bash
git config core.hooksPath .githooks
```

The hook **fails closed**: if the denylist cannot be found, the commit is refused
rather than allowed. A guard that silently disables itself on a new machine is
worse than no guard, because you stop checking by hand. Override the location
with `ORRERY_DENYLIST` if you keep it somewhere else.

Anyone cloning this repo must set `core.hooksPath` themselves — git does not ship
hooks on clone. Until they do, they have no guard.

## Dependency direction

```
company repo  --depends on-->  orrery
orrery        --never-->       company repo
```

The engine reads from and computes over whatever graph the company repo already
owns. It does not carry its own store, its own connectors, or its own copy of
anyone's inventory.

## Authorship

The first two commits carry a work email address. The history is left as it
stands rather than rewritten, because rewriting it would hide something rather
than fix anything: this project is the author's own work, licensed under
Apache-2.0 by him, and the rule in this file is what keeps any employer's material
out of it. An email address in a commit header is metadata about a machine, not a
claim about ownership.
