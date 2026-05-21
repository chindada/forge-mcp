"""§8.5 lifecycle helpers for cancellation, timeout, failure, and caps."""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..artifacts import atomic_write_text
from ..models import EvalGap, EvalResult
from .caps import GAP_LIST_CAP, build_gap_overflow, split_warnings
from .ledger import RunLedger
from .statemachine import RunStateMachine


class TaskCancellationProbe(Protocol):
    """Duck-typed task cancellation source (§C1.6).

    Design: lifecycle only reads is_cancelled so it should not depend on the
        concrete experimental MCP context at runtime.
    Implementation: Protocol supports fakes and ServerTaskContext alike.
    Example: if probe.is_cancelled: raise CancelledError.
    """

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested.

        Design: models ServerTaskContext.is_cancelled as a read-only property.
        Implementation: concrete task contexts compute the current task state.
        Example: if probe.is_cancelled: ...
        """
        ...


def poll_task_cancellation(task: TaskCancellationProbe | None) -> None:
    """Raise CancelledError when a live MCP task is cancelled (§C1.6).

    Design: C-Inv 1 centralizes the bridge from task.is_cancelled to the
        existing §8.5 cancellation path; this helper never writes state.
    Implementation: duck-type task.is_cancelled and raise asyncio.CancelledError
        with a stable forensic message only when true.
    Example: poll_task_cancellation(task) after a phase boundary.
    """
    if task is None:
        return
    if getattr(task, "is_cancelled", False):
        raise asyncio.CancelledError("client cancelled via cancel_task")


async def close_drivers(deps: Any) -> None:
    """Close all phase drivers with interrupt and terminate escalation (§H10).

    Design: §8.5 cancellation first asks SDK runners to close before escalating;
        §H10 adds best-effort SDK-native interrupt before that close.
    Implementation: inspect drivers and `_runner`, wait_for interrupt with 2s,
        wait_for aclose with 5s, then call terminate on timeout/error.
    Example: await close_drivers(deps).
    """
    for driver in (deps.drivers.planner, deps.drivers.generator, deps.drivers.evaluator):
        runner = getattr(driver, "_runner", driver)
        if runner is None:
            continue
        interrupt = getattr(runner, "interrupt", None)
        if interrupt is not None:
            try:
                await asyncio.wait_for(interrupt(), timeout=2)
            except Exception:
                pass
        try:
            await asyncio.wait_for(runner.aclose(), timeout=5)
        except Exception:
            try:
                runner.terminate()
            except Exception:
                pass


async def emit_terminal_status(status: Any, terminal_status: str) -> None:
    """Emit exactly one terminal status.update for the outcome.

    Design: §8.1 / §12 — every terminal path (success inline, timeout
        handler, failure handler) issues a single phase-kind status event so
        the NDJSON / ctx.info stream has a canonical run-finished marker.
    Implementation: thin wrapper over Status.update with the terminal phase as
        both `phase` and the human message; kind='phase'.
    Example: await emit_terminal_status(status, 'completed').
    """
    await status.update(
        phase=terminal_status,
        agent="orchestrator",
        message=f"run {terminal_status}",
        kind="phase",
    )


async def handle_cancellation(sm: RunStateMachine, ledger: RunLedger, deps: Any, lock: Any) -> None:
    """Apply the exact §8.5 client-cancellation terminal ordering, then re-raise.

    Design: §8.5 step 5 — caller must observe CancelledError; the engine
        produces no RunResult on this path. Observers seeing failed/
        cancelled=True must be able to assume the lock is already free, so
        final failed state is written after release.
    Implementation: capture last_phase, transition cancelling, close drivers,
        release lock, mark ledger, transition failed, raise CancelledError.
    Example: await handle_cancellation(sm, ledger, deps, lock) raises.
    """
    ledger.failed_phase = sm.last_phase
    sm.transition("cancelling", reason="client cancelled", cancelled=True)
    await close_drivers(deps)
    lock.release()
    ledger.lock_released = True
    ledger.decided_at = datetime.now(UTC)
    sm.transition("failed", reason="client cancelled", cancelled=True)
    raise asyncio.CancelledError()


async def handle_timeout(sm: RunStateMachine, ledger: RunLedger, deps: Any) -> None:
    """Finalize a runtime cap as incomplete, not cancelled.

    Design: §6.3/§8.5 separate runtime cap from client disconnect; timeout
        goes finalizing → incomplete after closing in-flight SDK sessions so
        they cannot outlive the cap.
    Implementation: transition finalizing, close drivers, collect latest
        unresolved gaps, set decided_at, transition incomplete, apply caps.
    Example: await handle_timeout(sm, ledger, deps).
    """
    sm.transition("finalizing")
    await close_drivers(deps)
    ledger.unresolved_gaps = collect_unresolved_gaps(deps.run_dir)
    ledger.decided_at = datetime.now(UTC)
    sm.transition("incomplete")
    apply_caps_and_overflow(ledger, deps.run_dir, deps.logger)
    await emit_terminal_status(deps.status, "incomplete")


async def handle_failure(sm: RunStateMachine, ledger: RunLedger, deps: Any, exc: Exception) -> None:
    """Record a non-cancellation failure as terminal failed state.

    Design: §6.3/§8.5 — failure returns a RunResult; traceback must be
        truncated to ≤4096 before RunResult construction, and unresolved_gaps
        are best-effort collected from disk so a crash still surfaces known
        gaps.
    Implementation: close drivers, record metadata, truncate traceback,
        best-effort collect unresolved gaps while preserving the original
        exception, transition failed, apply caps.
    Example: await handle_failure(sm, ledger, deps, RuntimeError('boom')).
    """
    await close_drivers(deps)
    ledger.failed_phase = sm.last_phase
    ledger.error_class = exc.__class__.__name__
    ledger.error_message = str(exc)
    ledger.traceback_truncated = "".join(traceback.format_exception(exc))[:4096]
    try:
        ledger.unresolved_gaps = collect_unresolved_gaps(deps.run_dir)
    except Exception:
        # §8.5: malformed eval.json must not mask the original exception.
        ledger.unresolved_gaps = []
    ledger.decided_at = datetime.now(UTC)
    sm.transition("failed", reason=str(exc))
    apply_caps_and_overflow(ledger, deps.run_dir, deps.logger)
    await emit_terminal_status(deps.status, "failed")


def collect_unresolved_gaps(run_dir: Path) -> list[EvalGap]:
    """Return the latest iteration's eval.json gaps only.

    Design: §11.5 / §8.5 — unresolved_gaps reflects the freshest evaluation;
        aggregating older iterations would leak stale gaps into the terminal
        RunResult.
    Implementation: enumerate iteration-N directories, pick the highest N
        whose eval.json exists, parse once, return its `gaps` list. Skip
        missing or in-progress directories.
    Example: gaps = collect_unresolved_gaps(Path('.harness/abcd1234')).
    """
    candidates: list[tuple[int, Path]] = []
    for path in run_dir.glob("iteration-*"):
        try:
            candidates.append((int(path.name.split("-", 1)[1]), path))
        except (IndexError, ValueError):
            continue
    for _, iteration_dir in sorted(candidates, reverse=True):
        eval_path = iteration_dir / "eval.json"
        if eval_path.exists():
            return EvalResult.model_validate_json(eval_path.read_text()).gaps
    return []


def apply_caps_and_overflow(ledger: RunLedger, run_dir: Path, logger: Any) -> None:
    """Apply RunResult caps and write overflow artifacts when needed.

    Design: §11.2 keeps the tool result bounded while preserving dropped gap
        details in private artifacts.
    Implementation: write overflow markdown via atomic_write_text, cap lists,
        and append a warning when warnings themselves are truncated.
    Example: apply_caps_and_overflow(ledger, run_dir, logger).
    """
    unresolved_total = len(ledger.unresolved_gaps)
    unresolved_overflow = build_gap_overflow(
        ledger.unresolved_gaps, kind="unresolved", total=unresolved_total
    )
    if unresolved_overflow is not None:
        path = run_dir / "unresolved-gaps-overflow.md"
        atomic_write_text(path, unresolved_overflow)
        ledger.unresolved_overflow_path = str(path)
        ledger.unresolved_gaps = ledger.unresolved_gaps[:GAP_LIST_CAP]
    design_total = len(ledger.design_flaw_gaps)
    design_overflow = build_gap_overflow(
        ledger.design_flaw_gaps, kind="design-flaw", total=design_total
    )
    if design_overflow is not None:
        path = run_dir / "design-flaw-gaps-overflow.md"
        atomic_write_text(path, design_overflow)
        ledger.design_flaw_overflow_path = str(path)
        ledger.design_flaw_gaps = ledger.design_flaw_gaps[:GAP_LIST_CAP]
    kept, dropped = split_warnings(ledger.warnings)
    if dropped:
        kept.append(f"warnings truncated: {len(dropped)} additional warning(s) omitted")
    ledger.warnings = kept
    if logger is not None:
        logger.info("applied result caps")
