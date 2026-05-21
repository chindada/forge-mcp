"""§H4 with_phase_watchdog: advisory heartbeat that never fails the run."""

from __future__ import annotations

import asyncio

import pytest

from forge_mcp.orchestrator.watchdog import with_phase_watchdog


class _RecordingStatus:
    """Status double recording update kinds for watchdog tests.

    Design: the watchdog only emits status events, so a recording double proves
        liveness without a real MCP sink.
    Implementation: append each update's kind and expose last_update_ts.
    Example: s = _RecordingStatus(); s.kinds == [].
    """

    def __init__(self) -> None:
        """Initialize the recorded-kind list and timestamp.

        Design: each test gets isolated recordings.
        Implementation: create an empty list and a stale last_update_ts.
        Example: _RecordingStatus().kinds == [].
        """
        self.kinds: list[str] = []
        self.last_update_ts: float = 0.0

    async def update(self, *, phase, agent, message, kind="phase", iteration=None) -> None:
        """Record one status update kind.

        Design: mirrors the production Status.update signature the watchdog calls.
        Implementation: append the kind to the recorded list.
        Example: await s.update(phase='p', agent='a', message='m', kind='heartbeat').
        """
        self.kinds.append(kind)


class _LivenessStatus:
    """Status double mirroring production liveness semantics for the watchdog.

    Design: the hang flag must fire only when genuine activity stops, so the
        double refreshes last_update_ts for non-heartbeat kinds and leaves it
        untouched for heartbeats — exactly like forge_mcp.status.Status.
    Implementation: record kinds and update last_update_ts only when the kind
        is not 'heartbeat'.
    Example: s = _LivenessStatus(); await s.update(..., kind='heartbeat').
    """

    def __init__(self) -> None:
        """Initialize recorded kinds and a fresh liveness timestamp.

        Design: a fresh timestamp models a phase that just started real work.
        Implementation: empty kinds list and last_update_ts set to now.
        Example: _LivenessStatus().kinds == [].
        """
        import time as _time

        self.kinds: list[str] = []
        self.last_update_ts: float = _time.time()

    async def update(self, *, phase, agent, message, kind="phase", iteration=None) -> None:
        """Record a kind and refresh liveness only for non-heartbeat events.

        Design: heartbeats are advisory pings and must not reset the hang clock.
        Implementation: append the kind; bump last_update_ts unless heartbeat.
        Example: await s.update(phase='p', agent='a', message='m', kind='phase').
        """
        import time as _time

        self.kinds.append(kind)
        if kind != "heartbeat":
            self.last_update_ts = _time.time()


async def test_returns_coro_result_and_emits_heartbeat() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    status = _RecordingStatus()

    async def work() -> str:
        """Sleep long enough for one heartbeat then return.

        Design: gives the sidecar time to emit at least one heartbeat.
        Implementation: await a short sleep and return a sentinel.
        Example: await work() returns 'done'.
        """
        await asyncio.sleep(0.05)
        return "done"

    result = await with_phase_watchdog(
        work(),
        status=status,
        phase="iter_generating",
        iteration=1,
        heartbeat_interval=0.01,
        hang_after=0.02,
    )
    assert result == "done"
    assert "heartbeat" in status.kinds


async def test_exception_propagates_and_run_not_failed() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    status = _RecordingStatus()

    async def boom() -> str:
        """Raise to prove the watchdog never swallows phase errors.

        Design: the watchdog is advisory and must let exceptions propagate.
        Implementation: raise RuntimeError after yielding once.
        Example: await boom() raises RuntimeError.
        """
        await asyncio.sleep(0)
        raise RuntimeError("phase failed")

    with pytest.raises(RuntimeError):
        await with_phase_watchdog(
            boom(),
            status=status,
            phase="iter_generating",
            iteration=1,
            heartbeat_interval=0.01,
            hang_after=0.02,
        )


async def test_hang_warning_fires_when_no_genuine_activity() -> None:
    """Pin §H16: a hang flag is emitted after hang_after with no status activity.

    Design: with production liveness semantics, repeated heartbeats must not
        keep resetting the hang clock, so a silent (deep/wedged) phase trips
        exactly one 'warning' even though heartbeat_interval < hang_after.
    Implementation: wrap a coroutine that emits no genuine updates and only
        sleeps, with heartbeat_interval < hang_after, and assert a warning kind
        is recorded.
    Example: pytest runs this test in the non-slow suite.
    """
    status = _LivenessStatus()

    async def quiet_work() -> str:
        """Sleep past several hang windows without emitting genuine updates.

        Design: models a phase doing no observable status activity.
        Implementation: await a sleep that exceeds hang_after, then return.
        Example: await quiet_work() returns 'done'.
        """
        await asyncio.sleep(0.2)
        return "done"

    result = await with_phase_watchdog(
        quiet_work(),
        status=status,
        phase="iter_generating",
        iteration=1,
        heartbeat_interval=0.01,
        hang_after=0.05,
    )
    assert result == "done"
    assert "heartbeat" in status.kinds
    assert status.kinds.count("warning") == 1
