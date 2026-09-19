"""Fail if the README's example output no longer matches what the commands print.

A README that drifts from the tool is worse than no README: someone follows it, gets
something else, and stops trusting the rest of the page. CI runs this.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

CHECKS = [
    (["blast", "host-a1"], "$ orrery blast host-a1"),
    (["simulate", "host-a1"], "$ orrery simulate host-a1"),
    (["backtest", "fixtures/incidents"], "$ orrery backtest fixtures/incidents"),
]


def block_after(readme: str, marker: str) -> list[str]:
    start = readme.index(marker) + len(marker)
    end = readme.index("```", start)
    return [line.rstrip() for line in readme[start:end].strip("\n").splitlines()]


def actual(args: list[str]) -> list[str]:
    out = subprocess.run(
        ["orrery", *args], capture_output=True, text=True, check=True
    ).stdout
    return [line.rstrip() for line in out.strip("\n").splitlines()]


def main() -> int:
    readme = pathlib.Path("README.md").read_text(encoding="utf-8")
    failures = []
    for args, marker in CHECKS:
        if marker not in readme:
            failures.append(f"{marker!r} is not in the README any more")
            continue
        want = [line for line in block_after(readme, marker) if line.strip()]
        got = [line for line in actual(args) if line.strip()]
        if want != got:
            failures.append(
                f"{' '.join(args)}\n  README says:\n"
                + "\n".join(f"    {line}" for line in want)
                + "\n  command prints:\n"
                + "\n".join(f"    {line}" for line in got)
            )
    if failures:
        print("README example output is stale:\n")
        print("\n\n".join(failures))
        return 1
    print(f"README examples match the tool ({len(CHECKS)} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
