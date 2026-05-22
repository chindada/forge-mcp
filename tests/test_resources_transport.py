"""§R9.4 — in-memory MCP transport pins for list_resources / read_resource.

These tests drive the registered low-level `server` through an in-memory
ClientSession (the same pattern as tests/test_mcp_transport.py) to pin the
SDK-decorator wrapping of resource handlers. Direct handler-function tests
(tests/test_resources_handlers.py) bypass `ReadResourceResult` /
`ListResourcesResult` construction by the SDK; these tests cover what those
cannot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextResourceContents
from pydantic import AnyUrl

from forge_mcp.resources import (
    _ACTIVE_RUNS,
    _ResourceScope,
    compute_harness_token,
    decode_uri,
    register_active_run,
)

pytestmark = pytest.mark.mcp


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Empty the active-run registry and FORGE_HARNESS_ROOTS env around each test.

    Design: §R9.4 transport tests must not see scopes leaked from earlier tests
        in the same process, and they must not pick up a developer's local
        FORGE_HARNESS_ROOTS value.
    Implementation: clear _ACTIVE_RUNS before and after; delete the env var via
        monkeypatch so RunConfig.from_env() in the test body sees no roots.
    Example: pytest runs this fixture automatically.
    """
    _ACTIVE_RUNS.clear()
    monkeypatch.delenv("FORGE_HARNESS_ROOTS", raising=False)
    yield
    _ACTIVE_RUNS.clear()


def _populate_run_dir(harness: Path, run_id: str) -> Path:
    """Create a populated run dir with one artifact from each walk location.

    Design: §R9.4 transport tests need a fixture covering top-level, inputs,
        plan, and iteration walks so `session.list_resources()` returns a
        representative cross-section of allowlisted URIs.
    Implementation: mkdir the inputs/, plan/, iteration-1/ subtrees and write
        minimal text artifacts each smaller than 32 bytes.
    Example: run_dir = _populate_run_dir(tmp_path / '.harness', '12345678').
    """
    run_dir = harness / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "plan").mkdir()
    (run_dir / "iteration-1").mkdir()
    (run_dir / "state.json").write_text("{}")
    (run_dir / "status.log").write_text("")
    (run_dir / "inputs" / "design.md").write_text("# spec")
    (run_dir / "plan" / "plan.md").write_text("# plan body")
    (run_dir / "iteration-1" / "eval.md").write_text("# e")
    return run_dir


async def test_session_list_resources_round_trips_decode_uri(tmp_path, monkeypatch):
    """§R9.4 session.list_resources() returns URIs that decode_uri parses.

    Design: §R9.4 pins the SDK-decorator wrapping by driving the registered
        low-level `server` through an in-memory ClientSession; every returned
        Resource.uri must round-trip through decode_uri to (harness_token,
        run_id, subpath) for the registered active run.
    Implementation: populate a tmp .harness run, register an active scope, then
        call client.list_resources() over the in-memory transport and assert
        that decode_uri succeeds for every returned URI, that the registered
        (token, run_id) tuple appears, and that representative subpaths from
        each walk location surface.
    Example: pytest -m mcp tests/test_resources_transport.py.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.server import server

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))

    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_resources()

    assert result.resources, "expected at least one Resource on the wire"
    decoded = [decode_uri(str(r.uri)) for r in result.resources]
    # Every URI parses cleanly (no decode_uri ValueError) and points at the
    # registered scope.
    for harness_token, run_id, _subpath in decoded:
        assert harness_token == token
        assert run_id == "12345678"
    subpaths = {sp for _, _, sp in decoded}
    # One artifact from each walk location (§R9.3 fixture requirement).
    assert "state.json" in subpaths
    assert "inputs/design.md" in subpaths
    assert "plan/plan.md" in subpaths
    assert "iteration-1/eval.md" in subpaths


async def test_session_read_resource_returns_text_resource_contents(tmp_path, monkeypatch):
    """§R9.4 session.read_resource() yields TextResourceContents on the wire.

    Design: §R9.4 pins that the SDK decorator wraps the handler's
        list[ReadResourceContents] into a ReadResourceResult with one
        TextResourceContents entry carrying the artifact text and MIME type.
        Direct handler tests in test_resources_handlers.py never observe this
        wrapping; this test does.
    Implementation: populate plan/plan.md under a tmp harness, register the
        scope, open the in-memory ClientSession, await
        client.read_resource(AnyUrl(uri)), assert .contents has length 1 and
        the first entry is a TextResourceContents with text='# plan body' and
        mimeType='text/markdown'.
    Example: pytest -m mcp tests/test_resources_transport.py.
    """
    from forge_mcp.config import RunConfig
    from forge_mcp.server import server

    harness = tmp_path / ".harness"
    _populate_run_dir(harness, "12345678")
    monkeypatch.setattr("forge_mcp.server._RESOURCE_CONFIG", RunConfig.from_env())
    token = compute_harness_token(harness)
    register_active_run(_ResourceScope("12345678", harness, token))
    uri = f"forge://{token}/12345678/plan/plan.md"

    async with create_connected_server_and_client_session(server) as client:
        result = await client.read_resource(AnyUrl(uri))

    assert len(result.contents) == 1
    entry = result.contents[0]
    assert isinstance(entry, TextResourceContents)
    assert entry.text == "# plan body"
    assert entry.mimeType == "text/markdown"
    # The wire URI round-trips through decode_uri (R-Inv 3 readside).
    assert decode_uri(str(entry.uri)) == (token, "12345678", "plan/plan.md")
