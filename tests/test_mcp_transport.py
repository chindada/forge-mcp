"""§18 transport-level MCP pinning tests (in-memory client).

These tests drive `run_forge` through the in-memory MCP transport and
assert the observed wire shape: `CallToolResult.isError is True` plus
a message substring. They MUST NOT assert any JSON-RPC numeric code
(`-32602`/`-32000`) — §6.3‡ documents that FastMCP turns body-raised
`McpError` exceptions into `isError=true` text results with no code on
the wire.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult

pytestmark = pytest.mark.mcp


async def _call_run_forge(args: dict) -> CallToolResult:
    """Drive run_forge through the in-memory MCP transport once.

    Design: §18 transport test — every case shares the same client setup;
        this helper keeps the per-case asserts focused on the result shape.
    Implementation: open an in-memory connected session against the live
        low-level Server object and await one call_tool roundtrip.
    Example: result = await _call_run_forge({'target_dir': '/tmp', ...}).
    """
    from forge_mcp.server import server

    async with create_connected_server_and_client_session(server) as client:
        return await client.call_tool("run_forge", args)


async def _wait_for_task_terminal(client, task_id: str):
    """Poll a task with a hard test timeout until it reaches a terminal state.

    Design: §C8 transport tests must fail loudly instead of hanging forever if
        the experimental MCP task poll loop changes semantics.
    Implementation: call get_task directly in a bounded loop and sleep briefly
        between non-terminal states.
    Example: terminal = await _wait_for_task_terminal(client, task_id).
    """
    for _ in range(50):
        status = await client.experimental.get_task(task_id)
        if status.status in {"completed", "failed", "cancelled"}:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"task {task_id} did not become terminal")


async def test_low_level_server_advertises_run_forge_with_task_optional() -> None:
    """tools/list advertises TASK_OPTIONAL for run_forge (§C1.4).

    Design: Path B requires the low-level Tool.execution.taskSupport field.
    Implementation: use the in-memory MCP client and inspect tools/list.
    Example: tool.execution.taskSupport == 'optional'.
    """
    from mcp.types import TASK_OPTIONAL

    from forge_mcp.server import server

    async with create_connected_server_and_client_session(server) as client:
        tools = await client.list_tools()
    tool = next(tool for tool in tools.tools if tool.name == "run_forge")
    assert tool.execution is not None
    assert tool.execution.taskSupport == TASK_OPTIONAL


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


def _install_fast_drivers(monkeypatch, target_dir) -> None:
    """Patch server._Drivers so run_forge completes quickly with no SDK calls.

    Design: the transport tests assert wire-shape behavior, not SDK semantics;
        substituting fakes lets each test finish in milliseconds and avoids
        any live claude/codex CLI dependency.
    Implementation: monkeypatch forge_mcp.server._Drivers with a class that
        returns FakeSessionPlanner / FakeSessionGenerator / FakeSessionEvaluator
        instances yielding a single no-gaps EvalResult. target_dir is unused
        but kept in the signature for future use.
    Example: _install_fast_drivers(monkeypatch, tmp_path).
    """
    from fakes import FakeSessionEvaluator, FakeSessionGenerator, FakeSessionPlanner

    import forge_mcp.server as srv
    from forge_mcp.models import EvalResult

    class _FastDrivers:
        """No-SDK driver bundle for transport tests.

        Design: parity with the production _Drivers shape (planner/generator/
            evaluator attributes); orchestrator wiring stays untouched.
        Implementation: instantiate the id-aware fakes; evaluator returns a
            single no-gaps EvalResult so the loop terminates after iteration 1.
        Example: _FastDrivers().planner.write_plan.
        """

        def __init__(self) -> None:
            """Build the three fake drivers.

            Design: orchestrator uses driver attributes by name only.
            Implementation: assign the three doubles.
            Example: _FastDrivers().evaluator.
            """
            self.planner = FakeSessionPlanner(session_id="sess_p")
            self.generator = FakeSessionGenerator(session_id="thr_g")
            self.evaluator = FakeSessionEvaluator(
                [EvalResult(no_gaps=True, gaps=[], summary="ok")],
                evaluate_ids=["sess_e"],
            )

    monkeypatch.setattr(srv, "_Drivers", _FastDrivers)


def _install_fast_preflight(monkeypatch) -> None:
    """Patch server.prepare_run to avoid live CLI/auth probes in transport tests.

    Design: §C8 transport tests exercise low-level task wiring, not §6.4
        doctor probes; bypassing probes keeps tests deterministic while still
        giving the orchestrator a real TargetLock and .harness directory.
    Implementation: build harness under the requested target_dir, acquire a
        TargetLock, and return server.PreparedRun with the caller config.
    Example: _install_fast_preflight(monkeypatch).
    """
    import forge_mcp.server as srv
    from forge_mcp.lockfile import TargetLock
    from forge_mcp.preflight import PreparedRun

    async def prepare_run(inputs, config):
        """Return a minimal PreparedRun without environment probes.

        Design: keeps task transport tests independent from local Claude/Codex
            availability while preserving lock-release behavior.
        Implementation: create .harness, acquire TargetLock, return PreparedRun.
        Example: prepared = await prepare_run(inputs, config).
        """
        harness_dir = target_dir = __import__("pathlib").Path(inputs.target_dir) / ".harness"
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = TargetLock(harness_dir / "run.lock")
        lock.acquire()
        return PreparedRun(
            harness_dir=harness_dir,
            lock=lock,
            run_id=lock.run_id,
            config=config,
            resume_point=None,
        )

    monkeypatch.setattr(srv, "prepare_run", prepare_run)


async def test_call_tool_as_task_returns_run_result_via_get_task_result(
    tmp_path, monkeypatch
) -> None:
    """Pin §C8 — call_tool_as_task + poll_task + get_task_result returns RunResult.

    Design: §C1.4 Path B advertises run_forge with TASK_OPTIONAL; clients
        opt in via session.experimental.call_tool_as_task. §C11 risk 7 says
        ttl SHOULD be >= max_runtime_minutes * 60 * 1000.
    Implementation: install fake drivers so the run completes in <1s; create
        task with ttl=max_runtime_minutes*60*1000; poll until terminal; fetch
        the CallToolResult and assert structuredContent carries RunResult
        with status=='completed'.
    Example: pytest tests/test_mcp_transport.py -k call_tool_as_task_returns -v.
    """
    from forge_mcp.server import server

    _install_fast_drivers(monkeypatch, tmp_path)
    _install_fast_preflight(monkeypatch)
    max_runtime_minutes = 1
    ttl_ms = max_runtime_minutes * 60 * 1000

    async with create_connected_server_and_client_session(server) as client:
        create_result = await client.experimental.call_tool_as_task(
            "run_forge",
            {
                "target_dir": str(tmp_path),
                "design_doc_content": "build it",
                "max_iterations": 1,
                "max_runtime_minutes": max_runtime_minutes,
            },
            ttl=ttl_ms,
        )
        task_id = create_result.task.taskId
        await _wait_for_task_terminal(client, task_id)
        final = await client.experimental.get_task_result(task_id, CallToolResult)

    assert final.isError is False
    assert final.structuredContent is not None
    assert final.structuredContent["status"] == "completed"
    assert final.structuredContent["run_id"]


async def test_cancel_task_mid_run_marks_task_cancelled(tmp_path, monkeypatch) -> None:
    """Pin §C1.6 / §C8 — cancel_task mid-run terminates the task forensically.

    Design: §C1.6 bridges cancel_task to §8.5 via poll_task_cancellation; the
        wire observable is that the task reaches a cancelled terminal status.
        (The Python-internal §8.5 five-step ordering is asserted in
        tests/test_server.py via a direct Orchestrator-level integration test.)
    Implementation: install a driver bundle whose generator awaits a long
        sleep so the task is live when cancel_task fires; create the task,
        cancel it, poll until terminal, and assert the status is 'cancelled'.
    Example: pytest tests/test_mcp_transport.py::test_cancel_task_mid_run_marks_task_cancelled -v.
    """
    from fakes import FakeSessionEvaluator, FakeSessionPlanner

    import forge_mcp.server as srv
    from forge_mcp.models import EvalResult
    from forge_mcp.server import server

    class _SlowGenerator:
        """Generator that blocks long enough for cancel_task to land.

        Design: keeps the orchestrator inside iter_generating until the test
            cancels; cancel_task on the client side flips task.is_cancelled
            and the next poll_task_cancellation() raises CancelledError.
        Implementation: implement() awaits asyncio.sleep that is long relative
            to the test (10 s); the test cancels well before that elapses.
        Example: await _SlowGenerator().implement(ctx, codex_bin='codex', status_cb=cb).
        """

        last_session_id = None

        async def implement(self, ctx, *, codex_bin, status_cb, env=None, network_access=True):
            """Sleep long enough for the cancel to land.

            Design: tests rely on a deterministic mid-run pause point.
            Implementation: await a 10-second sleep; CancelledError propagates
                from the surrounding task when the client cancels.
            Example: await generator.implement(ctx, codex_bin='codex', status_cb=cb).
            """
            await asyncio.sleep(10)

    class _SlowDrivers:
        """Driver bundle with a blocking generator for cancellation tests.

        Design: parity with production _Drivers shape; only generator is slow.
        Implementation: instantiate the three doubles.
        Example: _SlowDrivers().generator.
        """

        def __init__(self) -> None:
            """Build the three drivers (planner/generator/evaluator).

            Design: orchestrator reads driver attributes by name only.
            Implementation: assign each double; evaluator is unused due to cancel.
            Example: _SlowDrivers().planner.
            """
            self.planner = FakeSessionPlanner(session_id="sess_p")
            self.generator = _SlowGenerator()
            self.evaluator = FakeSessionEvaluator(
                [EvalResult(no_gaps=True, gaps=[], summary="ok")],
                evaluate_ids=["sess_e"],
            )

    monkeypatch.setattr(srv, "_Drivers", _SlowDrivers)
    _install_fast_preflight(monkeypatch)

    async with create_connected_server_and_client_session(server) as client:
        create_result = await client.experimental.call_tool_as_task(
            "run_forge",
            {
                "target_dir": str(tmp_path),
                "design_doc_content": "build it",
                "max_iterations": 1,
                "max_runtime_minutes": 1,
            },
            ttl=60_000,
        )
        task_id = create_result.task.taskId
        await asyncio.sleep(0.5)
        await client.experimental.cancel_task(task_id)
        terminal = await _wait_for_task_terminal(client, task_id)
        assert terminal.status == "cancelled", terminal
