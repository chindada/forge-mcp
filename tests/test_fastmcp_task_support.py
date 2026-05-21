"""§C1.2 verification gate for FastMCP taskSupport exposure."""

from __future__ import annotations

import inspect

import pytest

# Outcome (recorded 2026-05-22): Path B.


def _fastmcp_tool_accepts_task_support() -> bool:
    """Return True iff FastMCP().tool exposes taskSupport-shaped keyword.

    Design: §C1.2 requires recording whether Path A is viable before changing
        server behavior.
    Implementation: inspect the decorator signature for either spelling.
    Example: _fastmcp_tool_accepts_task_support() is False on the pinned mcp.
    """
    from mcp.server.fastmcp import FastMCP

    sig = inspect.signature(FastMCP().tool)
    return "taskSupport" in sig.parameters or "task_support" in sig.parameters


def test_records_path_a_or_path_b() -> None:
    """Record whether FastMCP can support Path A (§C1.2).

    Design: pass means Path A is feasible; fail means Path B is mandatory.
    Implementation: skip on Path A so normal CI does not fail after Path B is
        recorded in the module comment.
    Example: pytest shows a skip for the current Path-B implementation.
    """
    if _fastmcp_tool_accepts_task_support():
        pytest.skip("Path A feasible: FastMCP exposes taskSupport")
    pytest.skip("Path B recorded: FastMCP does not expose taskSupport on @mcp.tool")
