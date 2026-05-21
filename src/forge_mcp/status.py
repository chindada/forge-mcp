"""§12 status sink: MCP progress + ctx.info + NDJSON sidelog."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol


class TaskStatusSink(Protocol):
    """Duck-typed task status sink (§C1.5).

    Design: runtime values are MCP ServerTaskContext objects, but tests and
        type-checking only need the update_status method.
    Implementation: Protocol avoids a runtime mcp dependency in this module.
    Example: await sink.update_status('line').
    """

    async def update_status(self, message: str) -> None:
        """Update task-visible status text.

        Design: mirrors ServerTaskContext.update_status without importing it.
        Implementation: concrete sinks perform transport-specific fan-out.
        Example: await task.update_status('running').
        """
        ...


_PHASE_LABEL = {
    "planning": "plan",
    "planned": "plan",
    "iter_generating": "gen",
    "iter_verifying": "verify",
    "iter_evaluating": "eval",
    "iter_triaging": "triage",
    "iter_done": "done",
    "iter_remediating": "remed",
    "finalizing": "final",
    "completed": "completed",
    "incomplete": "incomplete",
    "failed": "failed",
    "cancelling": "cancelling",
}


class Status:
    """Forge run status sink (§12).

    Design: each update fans out to MCP progress, ctx.info, and NDJSON so live
        callers and forensic readers see the same stream.
    Implementation: keep an integer progress counter, format a human line,
        suppress ctx sink failures, and append a JSON line to status.log.
    Example: status = Status('abcd1234', ctx, run_dir / 'status.log').
    """

    def __init__(
        self,
        run_id: str,
        ctx: Any,
        ndjson_path: Path,
        *,
        task: TaskStatusSink | None = None,
    ) -> None:
        """Bind sink dependencies and initialize the status log.

        Design: the orchestrator constructs status only after the run dir
            exists, avoiding deferred rebinding state. §C1.5 adds an optional
            MCP task sink while preserving direct-call behavior.
        Implementation: mkdir parent, touch the file, chmod private on POSIX,
            initialize progress counters, and store the optional task.
        Example: Status('abcd1234', ctx, Path('status.log'), task=task).
        """
        self._run_id = run_id
        self._ctx = ctx
        self._path = ndjson_path
        self._progress = 0
        self._task = task
        self._max_iters: int | None = None
        self._iteration: int | None = None
        self.last_update_ts: float = time.time()
        ndjson_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        ndjson_path.touch(exist_ok=True)
        if os.name == "posix":
            os.chmod(ndjson_path, 0o600)

    def set_max_iterations(self, n: int) -> None:
        """Record the iteration cap for human progress lines (§12).

        Design: status lines include `iter N/M` once the run cap is known.
        Implementation: store the integer for later update formatting.
        Example: status.set_max_iterations(10).
        """
        self._max_iters = n

    async def update(
        self,
        *,
        phase: str,
        agent: str,
        message: str,
        kind: str = "phase",
        iteration: int | None = None,
    ) -> None:
        """Emit one status event to MCP progress, info, task, and NDJSON (§12).

        Design: §H4 uses real iteration/total progress when known and records
            last_update_ts so the advisory watchdog can distinguish silence
            from deep work; heartbeat events are advisory pings that do NOT
            refresh last_update_ts, so the hang clock measures genuine activity.
            §C1.5 task.update_status is one additional best-effort sink.
        Implementation: track current iteration, skip progress/info fan-out for
            heartbeat pings, otherwise report progress with a real denominator,
            suppress ctx/task failures, refresh last_update_ts for genuine
            activity, and append NDJSON.
        Example: await status.update(phase='planning', agent='planner', message='start').
        """
        if iteration is not None:
            self._iteration = iteration
        label = _PHASE_LABEL.get(phase, phase)
        if iteration is not None and self._max_iters is not None:
            head = f"[run {self._run_id} | iter {iteration}/{self._max_iters} | {label}]"
        else:
            head = f"[run {self._run_id} | {label}]"
        line = f"{head} {agent}: {message}"
        if kind != "heartbeat":
            # §H4.3 advisory liveness: a "heartbeat" is an NDJSON-only ping. It must
            # not fan out to MCP progress or ctx.info, and must not advance the bar.
            try:
                if self._iteration is not None and self._max_iters is not None:
                    await self._ctx.report_progress(
                        progress=float(self._iteration), total=float(self._max_iters), message=line
                    )
                else:
                    self._progress += 1
                    await self._ctx.report_progress(
                        progress=self._progress, total=None, message=line
                    )
            except Exception:
                pass
            try:
                await self._ctx.info(line)
            except Exception:
                pass
            if self._task is not None:
                try:
                    await self._task.update_status(line)
                except Exception:
                    pass
            # Only genuine phase/stream activity resets the hang clock (§H4.3).
            self.last_update_ts = time.time()
        record = {
            "ts": time.time(),
            "run_id": self._run_id,
            "iter": iteration,
            "phase": phase,
            "agent": agent,
            "message": message,
            "kind": kind,
        }
        with self._path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
