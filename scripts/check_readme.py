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
    (["spof", "--limit", "5"], "$ orrery spof --limit 5"),
    (["check"], "$ orrery check"),
    (["resolve", "fixtures/demo-world.yaml"], "$ orrery resolve fixtures/demo-world.yaml"),
]

# Every README, not just the English one. The translation is the copy that drifts, because
# nobody rereads it when the tool changes: README.ko.md spent two releases explaining
# `simulate` by replica counts the engine had stopped reading.
READMES = ["README.md", "README.ko.md"]


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
    failures = []
    checked = 0
    for name in READMES:
        readme = pathlib.Path(name).read_text(encoding="utf-8")
        for args, marker in CHECKS:
            if marker not in readme:
                continue  # a translation need not carry every example
            checked += 1
            want = [line for line in block_after(readme, marker) if line.strip()]
            got = [line for line in actual(args) if line.strip()]
            if want != got:
                failures.append(
                    f"{name}: {' '.join(args)}\n  the README says:\n"
                    + "\n".join(f"    {line}" for line in want)
                    + "\n  the command prints:\n"
                    + "\n".join(f"    {line}" for line in got)
                )
    # An example the English README has and the translation does not is fine. An example
    # nobody has is how the check quietly stops checking anything.
    english = pathlib.Path("README.md").read_text(encoding="utf-8")
    for _, marker in CHECKS:
        if marker not in english:
            failures.append(f"{marker!r} is not in README.md any more")
    if failures:
        print("README example output is stale:\n")
        print("\n\n".join(failures))
        return 1
    print(f"README examples match the tool ({checked} block(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
