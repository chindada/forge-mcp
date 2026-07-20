# tests/test_gitguard.py
from __future__ import annotations

import forge_mcp.gitguard as gitguard
from forge_mcp.gitguard import git_deny_matches


def test_deny_matcher_catches_chained_and_alt_spellings():
    """Design: §7 the matcher is defense-in-depth over the full command string.
    Implementation: catch &&-chained and 'checkout -b' spellings; pass read-only git.
    Example: git_deny_matches('ls && git commit -m x') is True.
    """
    assert git_deny_matches("ls && git commit -m x")
    assert git_deny_matches("git checkout -b feature")
    assert git_deny_matches("git push origin main")
    assert git_deny_matches("GIT REBASE main")
    assert not git_deny_matches("git status")
    assert not git_deny_matches("grep -r commit .")


def test_backstop_helpers_removed():
    """Design: §7 the snapshot/diff git backstop is removed (git does not gate completion).
    Implementation: the module exposes neither capture_state, diff_state, nor _run_git.
    Example: hasattr(gitguard, 'capture_state') is False.
    """
    for attr in ("capture_state", "diff_state", "_run_git"):
        assert not hasattr(gitguard, attr), f"{attr} should be removed"
