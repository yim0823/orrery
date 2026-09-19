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

Wire this into pre-commit and CI of the company-specific repo, pointing at this repo's tree.

## Dependency direction

```
company repo  --depends on-->  orrery
orrery        --never-->       company repo
```

The engine reads from and computes over whatever graph the company repo already
owns. It does not carry its own store, its own connectors, or its own copy of
anyone's inventory.

## Authorship

Commits before 2026-09-19 carry a work email. That history is left as it stands
rather than rewritten. Ownership of this code has not been established and is a
question for the author and their employer to settle in writing, not something a
commit trailer decides.
