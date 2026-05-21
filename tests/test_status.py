"""§12 status sink NDJSON and unknown phase labels."""

from __future__ import annotations

import json
from pathlib import Path

from forge_mcp.status import Status


class Ctx:
    """Fake MCP context collecting info/progress calls.

    Design: status tests need async methods without a real MCP transport.
    Implementation: append calls to in-memory lists.
    Example: ctx = Ctx(); await ctx.info('line').
    """

    def __init__(self) -> None:
        """Initialize empty call lists.

        Design: each test gets isolated recorded calls.
        Implementation: create progress and infos lists.
        Example: Ctx().infos == [].
        """
        self.progress: list[dict] = []
        self.infos: list[str] = []

    async def report_progress(self, **kwargs) -> None:
        """Record one progress call.

        Design: Status awaits this method as FastMCP contexts do.
        Implementation: append kwargs to progress list.
        Example: await ctx.report_progress(progress=1).
        """
        self.progress.append(kwargs)

    async def info(self, line: str) -> None:
        """Record one info line.

        Design: Status awaits this method as FastMCP contexts do.
        Implementation: append the line to infos.
        Example: await ctx.info('message').
        """
        self.infos.append(line)


async def test_unknown_pw_phase_passes_through_and_writes_ndjson(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = Ctx()
    status = Status("abcd1234", ctx, tmp_path / "status.log")
    status.set_max_iterations(2)
    await status.update(phase="iter_removed_probe", agent="x", message="m", iteration=1)
    assert "iter_removed_probe" in ctx.infos[0]
    row = json.loads((tmp_path / "status.log").read_text().strip())
    assert {"ts", "run_id", "iter", "phase", "agent", "message", "kind"} <= set(row)


async def test_progress_carries_total_max_iterations(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = Ctx()
    status = Status("abcd1234", ctx, tmp_path / "status.log")
    status.set_max_iterations(10)
    await status.update(phase="iter_generating", agent="generator", message="m", iteration=3)
    assert ctx.progress[-1]["progress"] == 3.0
    assert ctx.progress[-1]["total"] == 10.0


async def test_heartbeat_kind_recorded_in_ndjson(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = Ctx()
    log = tmp_path / "status.log"
    status = Status("abcd1234", ctx, log)
    status.set_max_iterations(10)
    await status.update(
        phase="iter_generating",
        agent="orchestrator",
        message="phase alive",
        kind="heartbeat",
        iteration=2,
    )
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert any(rec["kind"] == "heartbeat" for rec in lines)


async def test_heartbeat_does_not_refresh_last_update_ts(tmp_path: Path) -> None:
    """Pin §H4.3: heartbeats are advisory and must not reset the hang clock.

    Design: only genuine phase/stream activity counts as liveness, so the
        watchdog can detect a wedged phase even while heartbeats keep emitting.
    Implementation: take a genuine update, snapshot last_update_ts, emit a
        heartbeat, and assert the timestamp is unchanged; then a genuine
        update advances it.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = Ctx()
    status = Status("abcd1234", ctx, tmp_path / "status.log")
    status.set_max_iterations(10)
    await status.update(phase="iter_generating", agent="generator", message="real", iteration=1)
    after_real = status.last_update_ts
    await status.update(
        phase="iter_generating",
        agent="orchestrator",
        message="phase alive",
        kind="heartbeat",
        iteration=1,
    )
    assert status.last_update_ts == after_real
    await status.update(phase="iter_generating", agent="generator", message="real2", iteration=1)
    assert status.last_update_ts >= after_real


async def test_heartbeat_is_ndjson_only(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §H4.3 heartbeats are NDJSON-only advisory pings and must not fan
        out to MCP progress or ctx.info, nor refresh the hang clock.
    Implementation: drive one heartbeat update and assert no progress/info
        calls, an NDJSON row, and an unchanged last_update_ts.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = Ctx()
    status = Status("abcd1234", ctx, tmp_path / "status.log")
    status.set_max_iterations(2)
    before_ts = status.last_update_ts
    await status.update(
        phase="iter_generating", agent="watchdog", message="beat", kind="heartbeat", iteration=1
    )
    assert ctx.progress == []
    assert ctx.infos == []
    assert status.last_update_ts == before_ts
    row = json.loads((tmp_path / "status.log").read_text().strip())
    assert row["kind"] == "heartbeat"
    assert row["phase"] == "iter_generating"
    assert row["message"] == "beat"
