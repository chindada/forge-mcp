"""Slow real-CLI end-to-end smoke test, excluded from default CI."""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.slow


async def test_real_clis_tiny_feature_smoke() -> None:
    """Run real Claude/Codex CLIs through the MCP tool when available.

    Design: §18 keeps one slow wiring proof outside normal CI.
    Implementation: skip when binaries are absent; otherwise call the in-memory
        MCP transport with the tiny example and a one-iteration cap.
    Example: pytest -m slow tests/test_e2e_real_clis.py.
    """
    if shutil.which("claude") is None or shutil.which("codex") is None:
        pytest.skip("real claude/codex CLIs unavailable")
    from mcp.shared.memory import create_connected_server_and_client_session

    from forge_mcp.server import server

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "run_forge",
            {
                "target_dir": "examples/scratch-app",
                "design_doc_path": "examples/tiny-feature.md",
                "max_iterations": 1,
                "max_runtime_minutes": 20,
            },
        )
    assert result.isError is False
