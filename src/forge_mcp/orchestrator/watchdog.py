"""§H4 advisory per-phase watchdog (heartbeat + hang flag; H-Inv 5)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from typing import Any, TypeVar

T = TypeVar("T")


async def with_phase_watchdog(
    coro: Awaitable[T],
    *,
    status: Any,
    phase: str,
    iteration: int,
    heartbeat_interval: float = 60.0,
    hang_after: float = 300.0,
) -> T:
    """Run a phase coroutine while emitting advisory liveness (§H4).

    Design: distinguishes stuck from deep work without adding failure authority;
        the global runtime cap remains the sole termination path.
    Implementation: run the coroutine beside a heartbeat task, emit a one-shot
        warning after status silence, cancel sidecar on completion/exception.
    Example: await with_phase_watchdog(gen.implement(...), status=s,
        phase='iter_generating', iteration=3).
    """
    task: asyncio.Task[T] = asyncio.ensure_future(coro)

    async def _beat() -> None:
        """Emit periodic heartbeats and one hang warning (§H4).

        Design: advisory-only liveness must never fail the run or transition
            durable state.
        Implementation: suppress status errors and loop until cancelled.
        Example: spawned internally by with_phase_watchdog.
        """
        warned = False
        while True:
            await asyncio.sleep(heartbeat_interval)
            try:
                last = getattr(status, "last_update_ts", time.time())
                await status.update(
                    phase=phase,
                    agent="orchestrator",
                    message="phase alive",
                    kind="heartbeat",
                    iteration=iteration,
                )
                if not warned and time.time() - last > hang_after:
                    warned = True
                    await status.update(
                        phase=phase,
                        agent="orchestrator",
                        message="possible hang: no status activity",
                        kind="warning",
                        iteration=iteration,
                    )
            except Exception:
                pass

    sidecar = asyncio.ensure_future(_beat())
    try:
        return await task
    finally:
        sidecar.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sidecar
