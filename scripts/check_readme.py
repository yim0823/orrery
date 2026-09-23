"""Fail if the README's example output no longer matches what the commands print.

A README that drifts from the tool is worse than no README: someone follows it, gets
something else, and stops trusting the rest of the page. CI runs this.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

CHECKS = [
    (["blast", "host-a1"], "$ orrery blast host-a1"),
    (["simulate", "host-a1"], "$ orrery simulate host-a1"),
    (["backtest", "fixtures/incidents"], "$ orrery backtest fixtures/incidents"),
    (["spof", "--limit", "5"], "$ orrery spof --limit 5"),
    (["check"], "$ orrery check"),
    (["resolve", "fixtures/demo-world.yaml"], "$ orrery resolve fixtures/demo-world.yaml"),
    (["simulate", "ext-payments"], "$ orrery simulate ext-payments\n"),
    (
        ["simulate", "ext-payments", "--elapsed-s", "14400"],
        "$ orrery simulate ext-payments --elapsed-s 14400",
    ),
]

# Every page that shows output, not the English README only. The translation is the copy
# that drifts, because nobody rereads it when the tool changes: README.ko.md spent two
# releases explaining `simulate` by replica counts the engine had stopped reading. The
# architecture document drifted the same way, for the same reason — its propagation
# section documented identifiers that had not existed for two releases.
READMES = ["README.md", "README.ko.md", "docs/ARCHITECTURE.md", "docs/ARCHITECTURE.ko.md"]


def block_after(readme: str, marker: str) -> list[str]:
    start = readme.index(marker) + len(marker)
    end = readme.index("```", start)
    return [line.rstrip() for line in readme[start:end].strip("\n").splitlines()]


def actual(args: list[str], cwd: pathlib.Path) -> list[str]:
    out = subprocess.run(
        ["orrery", *args], capture_output=True, text=True, check=True, cwd=cwd
    ).stdout
    return [line.rstrip() for line in out.strip("\n").splitlines()]


def sandbox() -> pathlib.Path:
    """A fresh directory holding the fixtures and a world ingested from them, nothing else.

    The commands read the snapshot in the working directory. Run from the repo, they read
    whatever the last `orrery ingest` left there — which, on a laptop, is a snapshot from
    before the fixture changed, and the check then passes against a world nobody ships.
    It did: a renamed rack printed its old name for a day and this said the README matched.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="orrery-readme-"))
    shutil.copytree("fixtures", root / "fixtures")
    subprocess.run(["orrery", "ingest", "fixtures/demo-world.yaml"], cwd=root, check=True,
                   capture_output=True, env={**os.environ})
    return root


def main() -> int:
    root = sandbox()
    try:
        return _check(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _check(root: pathlib.Path) -> int:
    failures = []
    checked = 0
    for name in READMES:
        readme = pathlib.Path(name).read_text(encoding="utf-8")
        for args, marker in CHECKS:
            if marker not in readme:
                continue  # a translation need not carry every example
            checked += 1
            want = [line for line in block_after(readme, marker) if line.strip()]
            got = [line for line in actual(args, root) if line.strip()]
            if want != got:
                failures.append(
                    f"{name}: {' '.join(args)}\n  the README says:\n"
                    + "\n".join(f"    {line}" for line in want)
                    + "\n  the command prints:\n"
                    + "\n".join(f"    {line}" for line in got)
                )
    # An example the English README has and the translation does not is fine. An example
    # nobody has is how the check quietly stops checking anything.
    english = "".join(pathlib.Path(n).read_text(encoding="utf-8") for n in READMES)
    for _, marker in CHECKS:
        if marker not in english:
            failures.append(f"{marker!r} is in none of {', '.join(READMES)} any more")
    if failures:
        print("README example output is stale:\n")
        print("\n\n".join(failures))
        return 1
    print(f"README examples match the tool ({checked} block(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
