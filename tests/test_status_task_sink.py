"""§C1.5 task status sink coverage."""

from __future__ import annotations

import pytest


class _Task:
    """Fake task collecting status lines.

    Design: status tests need only update_status, not the full MCP type.
    Implementation: append messages to a list for assertions.
    Example: task.statuses == ['line'].
    """

    def __init__(self) -> None:
        """Initialize collected status storage.

        Design: each test needs isolated task state.
        Implementation: assign an empty list.
        Example: _Task().statuses == [].
        """
        self.statuses: list[str] = []

    async def update_status(self, message: str) -> None:
        """Record one task status update.

        Design: mirrors ServerTaskContext.update_status for Status fan-out.
        Implementation: append the human line verbatim.
        Example: await task.update_status('hello').
        """
        self.statuses.append(message)


class _FailingTask:
    """Fake task whose sink raises.

    Design: Status must suppress every non-NDJSON sink failure.
    Implementation: update_status always raises RuntimeError.
    Example: await status.update(...) still succeeds.
    """

    async def update_status(self, message: str) -> None:
        """Raise for one attempted task update.

        Design: exercises §C1.5 failure suppression.
        Implementation: ignore the message and raise a deterministic error.
        Example: await _FailingTask().update_status('x') raises.
        """
        _ = message
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_status_update_calls_task_update_status(tmp_path) -> None:
    """Status.update fans out to task.update_status (§C1.5).

    Design: task-mode clients should see the same human status line as ctx.info.
    Implementation: use ctx=None because Status already suppresses ctx failures.
    Example: task.statuses[0] contains the phase and message.
    """
    from forge_mcp.status import Status

    task = _Task()
    status = Status("abcd1234", None, tmp_path / "status.log", task=task)
    await status.update(phase="iter_generating", agent="generator", message="implementing")
    assert len(task.statuses) == 1
    assert "iter_generating" in task.statuses[0] or "gen" in task.statuses[0]
    assert "implementing" in task.statuses[0]


@pytest.mark.asyncio
async def test_status_update_suppresses_task_update_failure(tmp_path) -> None:
    """A failing task status sink does not fail Status.update (§C1.5).

    Design: status emission must never terminate a run.
    Implementation: use a task double whose update_status raises.
    Example: await status.update(...) returns None.
    """
    from forge_mcp.status import Status

    status = Status("abcd1234", None, tmp_path / "status.log", task=_FailingTask())
    await status.update(phase="planning", agent="planner", message="starting")
