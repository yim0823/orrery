"""The clean-room guard: names, shapes, and history.

A list of names only catches names someone remembered to write down, and a tree scan only
sees the tree — a token added in one commit and removed in the next is still published.
"""
from __future__ import annotations

import subprocess
import sys

SCRIPT = "scripts/check_identifiers.py"


def _run(denylist, *args, cwd=None):
    return subprocess.run([sys.executable, SCRIPT, "--denylist", str(denylist), *args],
                          capture_output=True, text=True, cwd=cwd, check=False)


def test_a_regex_line_catches_a_shape_not_just_a_name(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 'host:XY0008ABCD'\n")
    deny = tmp_path / "deny.txt"
    deny.write_text("re:\\bxy0[0-9a-z]{7}\\b\n")
    r = _run(deny, "--root", str(tmp_path / "src"))
    assert r.returncode == 1 and "re:" in r.stdout


def test_plain_tokens_still_work_and_comments_are_ignored(tmp_path):
    (tmp_path / "a.md").write_text("nothing to see\nSecretCorp inside\n")
    deny = tmp_path / "deny.txt"
    deny.write_text("# a comment\nsecretcorp\n")
    assert _run(deny, "--root", str(tmp_path)).returncode == 1
    deny.write_text("# only a comment\n")
    assert _run(deny, "--root", str(tmp_path)).returncode == 0


def test_the_range_scan_sees_what_a_later_commit_removed(tmp_path):
    g = ["git", "-C", str(tmp_path)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*g, "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("base\n")
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "base"], check=True)
    base = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "f.txt").write_text("base\nleak zzshape:4242\n")
    subprocess.run([*g, "commit", "-qam", "add"], check=True)
    (tmp_path / "f.txt").write_text("base\n")
    subprocess.run([*g, "commit", "-qam", "remove it again"], check=True)
    deny = tmp_path / "deny.txt"
    deny.write_text("re:zzshape:\\d{3,}\n")
    script = str((__import__("pathlib").Path(SCRIPT)).resolve())
    tree = subprocess.run([sys.executable, script, "--denylist", str(deny), "--root", str(tmp_path)],
                          capture_output=True, text=True, check=False)
    assert tree.returncode == 0  # the tree is clean …
    pushed = subprocess.run([sys.executable, script, "--denylist", str(deny),
                             "--git-range", f"{base}..HEAD"], capture_output=True, text=True,
                            cwd=tmp_path, check=False)
    assert pushed.returncode == 1  # … the history being pushed is not


def test_a_forbidden_commit_message_is_caught(tmp_path):
    g = ["git", "-C", str(tmp_path)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*g, "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("a\n")
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "fix for SecretCorp"], check=True)
    deny = tmp_path / "deny.txt"
    deny.write_text("secretcorp\n")
    script = str((__import__("pathlib").Path(SCRIPT)).resolve())
    r = subprocess.run([sys.executable, script, "--denylist", str(deny), "--git-range", "HEAD"],
                       capture_output=True, text=True, cwd=tmp_path, check=False)
    assert r.returncode == 1
