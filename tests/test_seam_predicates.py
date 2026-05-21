"""§H5/§H10 seam predicates: transient classification and git-deny PreToolUse hook."""

from __future__ import annotations

from forge_mcp.drivers import _claude, _codex


def test_claude_transient_classification() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert _claude.is_transient_error(ConnectionResetError()) is True
    assert _claude.is_transient_error(TimeoutError()) is True
    assert _claude.is_transient_error(BrokenPipeError()) is True
    # §H5.2 logic/filesystem OSErrors are NOT transport faults.
    assert _claude.is_transient_error(OSError()) is False
    assert _claude.is_transient_error(FileNotFoundError()) is False
    assert _claude.is_transient_error(PermissionError()) is False
    assert _claude.is_transient_error(ValueError("logic")) is False
    from claude_agent_sdk import CLIConnectionError  # type: ignore

    assert _claude.is_transient_error(CLIConnectionError("dropped")) is True


def test_codex_transient_classification() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert _codex.is_transient_error(ConnectionError()) is True
    assert _codex.is_transient_error(TimeoutError()) is True
    assert _codex.is_transient_error(BrokenPipeError()) is True
    # §H5.2 logic/filesystem OSErrors are NOT transport faults.
    assert _codex.is_transient_error(OSError()) is False
    assert _codex.is_transient_error(FileNotFoundError()) is False
    assert _codex.is_transient_error(PermissionError()) is False
    assert _codex.is_transient_error(ValueError("logic")) is False
    from openai_codex import ServerBusyError, TransportClosedError  # type: ignore

    assert _codex.is_transient_error(TransportClosedError("closed")) is True
    assert _codex.is_transient_error(ServerBusyError(1, "busy")) is True


async def test_deny_hook_blocks_git_commit() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    result = await _claude._deny_git_mutation_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, "id", None
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_deny_hook_passes_non_bash_and_safe_git() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    non_bash = await _claude._deny_git_mutation_hook({"tool_name": "Read"}, "id", None)
    safe = await _claude._deny_git_mutation_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git status"}}, "id", None
    )
    assert non_bash == {}
    assert safe == {}
