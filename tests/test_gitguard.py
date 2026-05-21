"""§11.3 gitguard multi-ref capture/diff."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge_mcp.gitguard import capture_state, diff_state


def test_capture_state_non_repo_returns_none(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert capture_state(tmp_path) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_capture_state_repo_contains_head(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("x")
    subprocess.run(["git", "add", "x.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    state = capture_state(tmp_path)
    assert state is not None
    assert "## HEAD" in state


def test_diff_state_empty_when_same_nonempty_when_different() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert diff_state("a", "a") == ""
    assert diff_state("a", "b") != ""
