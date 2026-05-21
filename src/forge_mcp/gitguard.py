"""§11.3 gitguard — multi-ref capture/diff. READS ONLY (Rule 11)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _git_available() -> bool:
    """Return True when `git` is on PATH.

    Design: §11.3 gitguard silently skips non-git environments rather than
        blocking implementation work.
    Implementation: delegate to shutil.which and return a boolean.
    Example: _git_available() is False on hosts without git.
    """
    return shutil.which("git") is not None


def capture_state(target_dir: Path) -> str | None:
    """Return a multi-section snapshot of HEAD, refs, tags, and worktrees.

    Design: §11.3 compares more than HEAD so branch, tag, and worktree
        mutations are detected after generator iterations.
    Implementation: use read-only git subcommands; non-repo or missing git
        returns None, later section failures degrade to empty text.
    Example: capture_state(Path('/repo')) returns a sectioned string or None.
    """
    if not _git_available():
        return None
    head = _run_git(target_dir, ["rev-parse", "HEAD"])
    if head is None:
        return None
    heads = _run_git(target_dir, ["for-each-ref", "refs/heads"]) or ""
    tags = _run_git(target_dir, ["for-each-ref", "refs/tags"]) or ""
    worktrees = _run_git(target_dir, ["worktree", "list", "--porcelain"]) or ""
    return (
        f"## HEAD\n{head}\n"
        f"## refs/heads\n{heads}\n"
        f"## refs/tags\n{tags}\n"
        f"## worktrees\n{worktrees}\n"
    )


def capture_uncommitted(target_dir: Path) -> str | None:
    """Return `git status --porcelain` output, or None outside git repos.

    Design: §8.1 captures dirty baseline context only when available.
    Implementation: read-only subprocess with the common silent-skip helper.
    Example: capture_uncommitted(Path('/repo')) returns '' for a clean repo.
    """
    if not _git_available():
        return None
    return _run_git(target_dir, ["status", "--porcelain"])


def changed_files(target_dir: Path) -> list[str]:
    """Return uncommitted changed paths for diff-scoped evaluation (§H8).

    Design: the generator never commits, so git status --porcelain is the
        natural starting focus for the evaluator without restricting review.
    Implementation: parse porcelain path columns, using rename targets after
        ' -> ', and return [] outside git repos or on git errors.
    Example: changed_files(Path('/repo')) == ['src/app.py'].
    """
    if not _git_available():
        return []
    out = _run_git(target_dir, ["status", "--porcelain"])
    if not out:
        return []
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        if path_part:
            paths.append(path_part)
    return paths


def diff_state(base: str | None, end: str | None) -> str:
    """Return a report iff any captured git section differs.

    Design: §11.3 uses a non-empty diff to synthesize a high-severity gap for
        the next iteration without letting drivers mutate refs silently.
    Implementation: compare snapshots as strings and embed both sides.
    Example: diff_state(a, a) returns an empty string.
    """
    if base == end:
        return ""
    return f"--- baseline ---\n{base or ''}\n--- after ---\n{end or ''}\n"


def _run_git(cwd: Path, argv: list[str]) -> str | None:
    """Run a git subcommand and return stdout on success.

    Design: gitguard is best-effort and read-only; git failures should not
        crash normal runs outside repositories.
    Implementation: subprocess.run with a timeout, captured output, and no
        shell; nonzero exits return None.
    Example: _run_git(Path('/repo'), ['rev-parse', 'HEAD']).
    """
    try:
        result = subprocess.run(
            ["git", *argv], cwd=str(cwd), capture_output=True, text=True, timeout=10, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
