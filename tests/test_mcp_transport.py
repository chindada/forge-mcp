"""§18 transport-level FastMCP pinning tests (in-memory client).

These tests drive `run_forge` through the in-memory MCP transport and
assert the observed wire shape: `CallToolResult.isError is True` plus
a message substring. They MUST NOT assert any JSON-RPC numeric code
(`-32602`/`-32000`) — §6.3‡ documents that FastMCP turns body-raised
`McpError` exceptions into `isError=true` text results with no code on
the wire.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult

pytestmark = pytest.mark.mcp


async def _call_run_forge(args: dict) -> CallToolResult:
    """Drive run_forge through the in-memory MCP transport once.

    Design: §18 transport test — every case shares the same client setup;
        this helper keeps the per-case asserts focused on the result shape.
    Implementation: open an in-memory connected session against the live
        FastMCP server object and await one call_tool roundtrip.
    Example: result = await _call_run_forge({'target_dir': '/tmp', ...}).
    """
    from forge_mcp.server import mcp

    async with create_connected_server_and_client_session(mcp._mcp_server) as client:  # noqa: SLF001
        return await client.call_tool("run_forge", args)


async def test_out_of_range_cap_returns_isError(tmp_path) -> None:
    """Pin a forge-mcp behavior.

    Design: §6.3‡ — pydantic Field(ge=, le=) bounds raise before the body,
        and FastMCP converts the resulting exception into an isError text
        result. This test pins that shape.
    Implementation: call run_forge with max_iterations=0 (below ge=1) and
        assert isError=True with a message substring; do NOT assert a
        numeric JSON-RPC code (none is transmitted — §6.3‡).
    Example: pytest -m mcp tests/test_mcp_transport.py.
    """
    result = await _call_run_forge(
        {
            "target_dir": str(tmp_path),
            "design_doc_content": "anything",
            "max_iterations": 0,
            "max_runtime_minutes": 1,
        }
    )
    assert result.isError is True
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "max_iterations" in text or "greater than or equal to 1" in text


async def test_design_doc_xor_violation_returns_isError(tmp_path) -> None:
    """Pin a forge-mcp behavior.

    Design: §6.1 — exactly one of design_doc_path / design_doc_content is
        required; setting both (or neither) raises McpError in the body,
        which FastMCP surfaces as isError=true text.
    Implementation: pass both design_doc_path and design_doc_content; assert
        the call returns isError=True with a message substring identifying
        the xor violation.
    Example: pytest -m mcp tests/test_mcp_transport.py.
    """
    doc_path = tmp_path / "design.md"
    doc_path.write_text("a")
    result = await _call_run_forge(
        {
            "target_dir": str(tmp_path),
            "design_doc_path": str(doc_path),
            "design_doc_content": "also content",
            "max_iterations": 1,
            "max_runtime_minutes": 1,
        }
    )
    assert result.isError is True
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "design_doc" in text or "exactly one" in text.lower() or "design doc" in text.lower()


async def test_preflight_failure_returns_isError() -> None:
    """Pin a forge-mcp behavior.

    Design: §6.4 — a nonexistent target_dir is rejected by preflight before
        the orchestrator starts; the McpError surfaces as isError=true on
        the wire.
    Implementation: pass a target_dir that does not exist and assert the
        result is isError=True with a message substring referring to the
        missing directory; do NOT assert a numeric code.
    Example: pytest -m mcp tests/test_mcp_transport.py.
    """
    result = await _call_run_forge(
        {
            "target_dir": "/nonexistent/forge-mcp-test-path-xyz-12345",
            "design_doc_content": "anything",
            "max_iterations": 1,
            "max_runtime_minutes": 1,
        }
    )
    assert result.isError is True
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "/nonexistent/forge-mcp-test-path-xyz-12345" in text or "target_dir" in text
