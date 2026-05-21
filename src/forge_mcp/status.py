"""§12 status sink: MCP progress + ctx.info + NDJSON sidelog."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_PHASE_LABEL = {
    "planning": "plan",
    "planned": "plan",
    "iter_generating": "gen",
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

    def __init__(self, run_id: str, ctx: Any, ndjson_path: Path) -> None:
        """Bind sink dependencies and initialize the status log.

        Design: the orchestrator constructs status only after the run dir
            exists, avoiding deferred rebinding state.
        Implementation: mkdir parent, touch the file, chmod private on POSIX,
            and initialize progress counters.
        Example: Status('abcd1234', ctx, Path('status.log')).
        """
        self._run_id = run_id
        self._ctx = ctx
        self._path = ndjson_path
        self._progress = 0
        self._max_iters: int | None = None
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
        """Emit one status event to MCP progress, info, and NDJSON (§12).

        Design: phase labels deliberately omit removed removed states; an
            unknown phase passes through unchanged as a §15 regression pin.
        Implementation: increment progress, await ctx methods defensively, then
            append one JSON object with documented keys.
        Example: await status.update(phase='planning', agent='planner', message='start').
        """
        self._progress += 1
        label = _PHASE_LABEL.get(phase, phase)
        if iteration is not None and self._max_iters is not None:
            head = f"[run {self._run_id} | iter {iteration}/{self._max_iters} | {label}]"
        else:
            head = f"[run {self._run_id} | {label}]"
        line = f"{head} {agent}: {message}"
        try:
            await self._ctx.report_progress(progress=self._progress, total=None)
        except Exception:
            pass
        try:
            await self._ctx.info(line)
        except Exception:
            pass
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
