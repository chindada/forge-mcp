# tests/test_gitguard.py
from __future__ import annotations

import subprocess
from pathlib import Path

from forge_mcp.gitguard import capture_state, diff_state, git_deny_matches


def _git(repo: Path, *args: str) -> None:
    """Run a git command in *repo*, raising on non-zero exit.

    Design: test helper to mutate a temp repo without repeating boilerplate.
    Implementation: subprocess.run with check=True; capture_output suppresses
        noise in test output.
    Example: _git(tmp_path, 'init', '-q') initialises a new repo.
    """
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_non_repo_is_none(tmp_path: Path):
    """Design: §9 capture returns None outside a git repo.
    Implementation: a bare temp dir.
    Example: capture_state(tmp) is None.
    """
    assert capture_state(tmp_path) is None


def test_commit_is_detected(tmp_path: Path):
    """Design: §9 a new commit changes HEAD -> non-empty diff.
    Implementation: snapshot, commit, snapshot, diff.
    Example: diff_state(base, end) != ''.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-qm", "one")
    base = capture_state(tmp_path)
    (tmp_path / "a.txt").write_text("2")
    _git(tmp_path, "commit", "-qam", "two")
    end = capture_state(tmp_path)
    assert diff_state(base, end) != ""


def test_no_change_is_empty(tmp_path: Path):
    """Design: §9 an unchanged repo yields an empty diff.
    Implementation: two snapshots with no mutation between.
    Example: diff_state(a, a) == ''.
    """
    _git(tmp_path, "init", "-q")
    s = capture_state(tmp_path)
    assert diff_state(s, capture_state(tmp_path)) == ""


def test_deny_matcher_catches_chained_and_alt_spellings():
    """Design: §9 the matcher is defense-in-depth over the full command string.
    Implementation: catch &&-chained and 'checkout -b' spellings.
    Example: git_deny_matches('ls && git commit -m x') is True.
    """
    assert git_deny_matches("ls && git commit -m x")
    assert git_deny_matches("git checkout -b feature")
    assert git_deny_matches("git push origin main")
    assert not git_deny_matches("git status")
    assert not git_deny_matches("grep -r commit .")
