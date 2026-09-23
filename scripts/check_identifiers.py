"""Scan the repo for forbidden identifiers listed in an external denylist.

Usage:
    python scripts/check_identifiers.py --denylist /path/to/denylist.txt [--root .]
    python scripts/check_identifiers.py --denylist ... --git-range origin/main..HEAD

Denylist: one entry per line, '#' comments allowed. A plain line is a case-insensitive
substring. A line starting with ``re:`` is a case-insensitive regular expression — for
shapes rather than names: an inventory id format, a host naming scheme, a rack code.
A list of names only ever catches the names someone remembered to write down.

``--git-range`` scans what a push would publish — every added line and every commit
message in the range — instead of the working tree. A token removed from the tree in a
later commit is still in the history that goes out, and the tree scan cannot see it.
Exit code 1 if anything is found.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ".cfg", ".ini",
                 ".mjs", ".sh"}


def load_denylist(path: pathlib.Path) -> list[tuple[str, re.Pattern | None]]:
    """(label, pattern) pairs. Plain tokens get pattern None and are matched by `in`."""
    entries: list[tuple[str, re.Pattern | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("re:"):
            expr = line.lstrip()[3:].strip()
            if expr:
                entries.append((f"re:{expr}", re.compile(expr, re.IGNORECASE)))
            continue
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append((line.lower(), None))
    return entries


def _hits_in(line: str, entries) -> list[str]:
    low = line.lower()
    return [label for label, pat in entries
            if (pat.search(line) if pat is not None else label in low)]


def scan(root: pathlib.Path, entries) -> list[tuple[str, int, str]]:
    hits = []
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for label in _hits_in(line, entries):
                    hits.append((str(p), i, label))
        except UnicodeDecodeError:
            continue
    return hits


def scan_range(rev_range: str, entries) -> list[tuple[str, int, str]]:
    """Added lines and commit messages in `rev_range`, as `git log -p` shows them."""
    out = subprocess.run(
        ["git", "log", "-p", "--no-color", "--format=commit %H%n%B", *rev_range.split()],
        capture_output=True, text=True, check=True,
    ).stdout
    hits = []
    commit = "?"
    for i, line in enumerate(out.splitlines(), 1):
        if line.startswith("commit "):
            commit = line.split()[1][:9]
            continue
        if line.startswith(("---", "+++", "-", "@@", "diff ", "index ")):
            continue  # removed lines are not published by this push
        for label in _hits_in(line, entries):
            hits.append((f"commit {commit}", i, label))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--git-range", default=None)
    a = ap.parse_args()
    entries = load_denylist(pathlib.Path(a.denylist))
    if not entries:
        print("denylist is empty; nothing to check")
        return 0
    hits = scan_range(a.git_range, entries) if a.git_range else scan(pathlib.Path(a.root), entries)
    for where, i, label in hits:
        print(f"{where}:{i}: forbidden '{label}'")
    print(f"{len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
