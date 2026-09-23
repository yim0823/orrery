"""Scan the repo for forbidden identifiers listed in an external denylist.

Usage:
    python scripts/check_identifiers.py --denylist /path/to/denylist.txt [--root .]
    python scripts/check_identifiers.py --denylist ... --git-range origin/main..HEAD

Denylist: one entry per line, '#' comments allowed. A plain line is a case-insensitive
substring. A line starting with ``re:`` is a case-insensitive regular expression — for
shapes rather than names: an inventory id format, a host naming scheme, a rack code.
A list of names only ever catches the names someone remembered to write down.

``--git-range`` scans what a push would publish instead of the working tree: every
commit message and author, every added line (merge resolutions included), every path, and
it refuses binary files — a token removed from the tree in a later commit is still in the
history that goes out, and a name inside a PNG or a file name is published just the same.
``--stdin`` scans text piped in (the pre-push hook uses it for annotated tag messages).

A denylist line ``allow-binary:<glob>`` lets a binary path through, for the rare file that
has to be one. Exit code 1 if anything is found.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ".cfg", ".ini",
                 ".mjs", ".sh", ".html", ".htm", ".svg", ".csv", ".js", ".ts", ".css", ".lock", ""}


ALLOW_BINARY: list[str] = []


def load_denylist(path: pathlib.Path) -> list[tuple[str, re.Pattern | None]]:
    """(label, pattern) pairs. Plain tokens get pattern None and are matched by `in`."""
    entries: list[tuple[str, re.Pattern | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("allow-binary:"):
            ALLOW_BINARY.append(line.split(":", 1)[1].strip())
            continue
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
        for label in _hits_in(str(p.relative_to(root)), entries):
            hits.append((str(p), 0, f"{label} (in the path)"))
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for label in _hits_in(line, entries):
                    hits.append((str(p), i, label))
        except UnicodeDecodeError:
            continue
    return hits


def _git(*args: str) -> str:
    # quotepath 를 끄지 않으면 한글 파일명이 8진수 이스케이프로 나와 어떤 항목에도 안 맞는다
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], capture_output=True, text=True,
                          check=True).stdout


def scan_range(rev_range: str, entries) -> list[tuple[str, int, str]]:
    """Everything a push of `rev_range` publishes: messages, authors, added lines, paths."""
    revs = rev_range.split()
    hits: list[tuple[str, int, str]] = []

    def check(where: str, i: int, text: str) -> None:
        for label in _hits_in(text, entries):
            hits.append((where, i, label))

    # 1. messages and authors, unfiltered — a bullet line starting with "- " is still published
    for block in _git("log", "--format=%H%x00%an <%ae> / %cn <%ce>%x00%B%x01", *revs).split("\x01"):
        if not block.strip():
            continue
        sha, author, body = (block.strip("\n").split("\x00") + ["", ""])[:3]
        check(f"commit {sha[:9]} author/committer", 0, author)
        for i, line in enumerate(body.splitlines(), 1):
            check(f"commit {sha[:9]} message", i, line)
    # 2. added lines, merge resolutions included (--cc). Only the diff markers are stripped:
    #    a content line that itself starts with "++" or "@@" is still content.
    sha = "?"
    for i, line in enumerate(_git("log", "-p", "--cc", "--no-color", "--format=@@@commit %H",
                                  *revs).splitlines(), 1):
        if line.startswith("@@@commit "):
            sha = line.split()[1][:9]
            continue
        if line.startswith(("+++ b/", "+++ /dev/null", "--- a/", "--- /dev/null")):
            continue
        marker = line[:2]
        if line.startswith("+") and not line.startswith("++ "):
            content = line[1:]
        elif len(marker) == 2 and "+" in marker and "-" not in marker and marker.strip(" +") == "":
            content = line[2:]  # combined diff of a merge
        else:
            continue
        check(f"commit {sha}", i, content)
    # 3. paths
    for path in set(_git("log", "--name-only", "--format=", *revs).splitlines()):
        if path.strip():
            check("path", 0, path)
    # 4. binaries — their content cannot be scanned, so they are refused unless allowed
    import fnmatch
    for line in _git("log", "--numstat", "--format=", *revs).splitlines():
        parts = line.split("\t")
        binary = len(parts) == 3 and parts[0] == "-" and parts[1] == "-"
        if binary and not any(fnmatch.fnmatch(parts[2], g) for g in ALLOW_BINARY):
            hits.append(("binary", 0, f"binary file {parts[2]} (allow with allow-binary:<glob>)"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--git-range", default=None)
    ap.add_argument("--stdin", action="store_true")
    a = ap.parse_args()
    entries = load_denylist(pathlib.Path(a.denylist))
    if not entries:
        print("denylist is empty; nothing to check")
        return 0
    if a.stdin:
        hits = [("stdin", i, label) for i, line in enumerate(sys.stdin.read().splitlines(), 1)
                for label in _hits_in(line, entries)]
    elif a.git_range:
        hits = scan_range(a.git_range, entries)
    else:
        hits = scan(pathlib.Path(a.root), entries)
    for where, i, label in hits:
        print(f"{where}:{i}: forbidden '{label}'")
    print(f"{len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
