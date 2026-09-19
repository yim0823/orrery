#!/bin/sh
# What someone gets on a fresh clone, run exactly as the README says to.
#
# CI runs this in a clean checkout. The reason it exists: the documented setup silently
# stopped installing the test dependencies at one point, so `uv run pytest` failed for
# anyone following the instructions while passing for everyone who already had an
# environment. That is the class of breakage nobody notices until a new person hits it.
set -eu

echo "== install =="
uv sync

echo "== the quickstart, verbatim from the README =="
uv run orrery ingest fixtures/demo-world.yaml
uv run orrery blast site-a
uv run orrery simulate db-stock

echo "== the rest of the commands =="
uv run orrery check
uv run orrery spof --limit 5
uv run orrery resolve fixtures/demo-world.yaml
uv run orrery backtest fixtures/incidents

echo "== machine-readable output parses =="
uv run orrery blast site-a --json-out | uv run python -c "import json,sys; d=json.load(sys.stdin); assert d['schema']==1; print('json ok:', len(d['impacted']), 'impacted')"

echo "== tests, the way CONTRIBUTING says to run them =="
uv run pytest -q
uv run ruff check .

echo "== documentation is not lying =="
uv run python scripts/check_readme.py

echo
echo "fresh clone works"
