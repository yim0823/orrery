"""Scan the repo for forbidden identifiers listed in an external denylist.

Usage: python scripts/check_identifiers.py --denylist /path/to/denylist.txt [--root .]
Denylist: one token per line, '#' comments allowed, case-insensitive substring match.
Exit code 1 if any token is found.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ".cfg", ".ini"}


def load_denylist(path: pathlib.Path) -> list[str]:
    tokens = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            tokens.append(line.lower())
    return tokens


def scan(root: pathlib.Path, tokens: list[str]) -> list[tuple[pathlib.Path, int, str]]:
    hits = []
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                low = line.lower()
                for t in tokens:
                    if t in low:
                        hits.append((p, i, t))
        except UnicodeDecodeError:
            continue
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", required=True)
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    tokens = load_denylist(pathlib.Path(a.denylist))
    if not tokens:
        print("denylist is empty; nothing to check")
        return 0
    hits = scan(pathlib.Path(a.root), tokens)
    for p, i, t in hits:
        print(f"{p}:{i}: forbidden token '{t}'")
    print(f"{len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
