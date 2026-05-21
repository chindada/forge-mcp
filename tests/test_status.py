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
