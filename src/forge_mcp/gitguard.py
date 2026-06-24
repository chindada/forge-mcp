"""Read-only git-mutation detector (§9): snapshot, diff, and deny matcher."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _run_git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stdout, or '' on failure/timeout.

    Design: §9 all git probes must be non-destructive and must never propagate
        exceptions to callers — a timeout or non-zero exit is treated as an
        empty/unknown result rather than an error.
    Implementation: subprocess.run with shell=False, capture_output, text, and
        a 10-second timeout; TimeoutExpired is caught and returns ''; non-zero
        returncode also returns '' so callers can treat absence as falsy.
    Example: _run_git(Path('/tmp/not-a-repo'), 'rev-parse', 'HEAD') == ''.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def capture_state(repo: Path) -> str | None:
    """Return a comparable snapshot of the mutable git surface, or None.

    Design: §9 callers need a stable, comparable string they can diff across
        iterations; None signals that *repo* is not a git repository at all,
        which lets the orchestrator skip guard logic gracefully.
    Implementation: probe with 'git rev-parse --is-inside-work-tree'; if the
        probe returns '' (non-repo or error) return None.  Otherwise
        concatenate in a fixed order: HEAD hash, all refs (show-ref), and the
        porcelain worktree list.  Any mutation to a commit, branch, tag, or
        worktree changes at least one of these three sections.
    Example: capture_state(Path('/tmp')) is None; after a commit the returned
        string differs from the pre-commit snapshot.
    """
    probe = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if not probe:
        return None

    head = _run_git(repo, "rev-parse", "HEAD")
    refs = _run_git(repo, "show-ref")
    worktrees = _run_git(repo, "worktree", "list", "--porcelain")

    return f"HEAD\n{head}\nREFS\n{refs}\nWORKTREES\n{worktrees}"


def diff_state(base: str | None, end: str | None) -> str:
    """Return '' when base and end are identical, otherwise a human-readable diff.

    Design: §9 the orchestrator calls this after each tool iteration to detect
        whether the agent's bash commands mutated the repo; a non-empty return
        value is the signal to abort or log the violation.
    Implementation: simple equality check; if different, embed both snapshots
        in a labelled string so operators can inspect what changed.
    Example: diff_state('x', 'x') == ''; diff_state('x', 'y') != ''.
    """
    if base == end:
        return ""
    return f"git state changed:\n--- base\n{base}\n--- end\n{end}"


# Verbs that constitute a git mutation when found after 'git' in a command.
_MUTATING_VERBS = r"commit|push|branch|tag|rebase|reset|worktree"
_DENY_PATTERN = re.compile(r"\bgit\b.*?(?:" + _MUTATING_VERBS + r"|checkout\s+-b)")


def git_deny_matches(command: str) -> bool:
    """Return True if *command* appears to invoke a git-mutating operation.

    Design: §9 defense-in-depth layer checked before executing any bash
        command; the snapshot diff (capture_state / diff_state) is the real
        backstop, but an early deny avoids running the command at all.
    Implementation: lower-case the full command string (handles &&-chained
        forms) then regex-search for 'git' followed anywhere in the same
        string by a mutating verb or 'checkout -b'.  This is intentionally
        best-effort — it will not catch obfuscated forms, which the snapshot
        diff catches instead.
    Example: git_deny_matches('ls && git commit -m x') is True;
        git_deny_matches('git status') is False.
    """
    return bool(_DENY_PATTERN.search(command.lower()))
