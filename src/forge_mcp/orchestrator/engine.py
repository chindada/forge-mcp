"""§8.1 Orchestrator engine — thin conductor over collaborators."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..artifacts import atomic_write_text, create_run_dir, prune_old_runs
from ..doctor import disk_space_warn_if_low
from ..gitguard import capture_state, capture_uncommitted
from ..models import EvalResult, RunForgeInput, RunResult
from ..preflight import PreparedRun
from ..state import RunState
from ..status import Status
from .convergence import fingerprint_gaps
from .ledger import RunLedger
from .lifecycle import (
    apply_caps_and_overflow,
    collect_unresolved_gaps,
    emit_terminal_status,
    handle_cancellation,
    handle_failure,
    handle_timeout,
)
from .phases import PhaseDeps, run_iteration_loop, run_phases
from .result import build_result
from .resume import ResumePoint, prepare_resume
from .statemachine import RunStateMachine

DESIGN_DOC_LARGE_BYTES = 1024 * 1024  # §8.1 — warn over 1 MB without truncating


def _reconstruct_fingerprints(run_dir: Path, point: ResumePoint) -> list[frozenset[str]]:
    """Rebuild oscillation history from durable eval.json files (§H2.5, §H3).

    Design: non-progress detection must survive resume without live context, so
        history is reconstructed from completed iteration artifacts.
    Implementation: read eval.json for iterations 1..last_completed and append
        fingerprint_gaps(gaps), skipping missing/corrupt artifacts.
    Example: history = _reconstruct_fingerprints(run_dir, point).
    """
    history: list[frozenset[str]] = []
    for iteration_n in range(1, point.last_completed_iteration + 1):
        eval_path = run_dir / f"iteration-{iteration_n}" / "eval.json"
        if not eval_path.exists():
            continue
        try:
            gaps = EvalResult.model_validate_json(eval_path.read_text()).gaps
        except Exception:
            continue
        history.append(fingerprint_gaps(gaps))
    return history


def canonicalize_design(inputs: RunForgeInput, run_dir: Path, ledger: RunLedger) -> None:
    """Resolve the design doc source and write it to inputs/design.md.

    Design: §8.1 — preflight only validated the design inputs; this step
        reads/resolves them into the canonical copy every later phase reads.
        Empty/whitespace is rejected; oversized docs warn but are NOT truncated.
    Implementation: read path or inline content, raise on empty, warn when
        >1 MB, atomic_write_text into inputs/design.md.
    Example: canonicalize_design(inputs, run_dir, ledger).
    """
    if inputs.design_doc_path is not None:
        text = Path(inputs.design_doc_path).read_text()
    else:
        text = inputs.design_doc_content or ""
    if not text.strip():
        raise ValueError("design document is empty")
    if len(text.encode("utf-8")) > DESIGN_DOC_LARGE_BYTES:
        ledger.warnings.append(
            f"design document exceeds 1 MB ({len(text)} chars); written without truncation"
        )
    atomic_write_text(run_dir / "inputs" / "design.md", text)


async def warn_if_missing_target_agents_md(
    target_dir: Path, ledger: RunLedger, status: Status
) -> None:
    """Warn when target_dir lacks AGENTS.md and AGENTS.override.md.

    Design: §8.1 — Codex auto-loads AGENTS.md from cwd upward; absence
        silently strips project guidance from the Generator. Surface this as a
        non-fatal warning; if CLAUDE.md is present, add a hint that it does not
        substitute.
    Implementation: probe both AGENTS files; if neither exists, build a
        message (with a CLAUDE.md hint when applicable), append to
        ledger.warnings, await one status.update(kind='warning').
    Example: await warn_if_missing_target_agents_md(target_dir, ledger, status).
    """
    if (target_dir / "AGENTS.md").exists() or (target_dir / "AGENTS.override.md").exists():
        return
    msg = f"target_dir has no AGENTS.md or AGENTS.override.md: {target_dir}"
    if (target_dir / "CLAUDE.md").exists():
        msg += (
            " (CLAUDE.md is present but Codex does not read it; create AGENTS.md or "
            "AGENTS.override.md to thread project guidance into the Generator)"
        )
    ledger.warnings.append(msg)
    await status.update(
        phase="init",
        agent="orchestrator",
        message=msg,
        kind="warning",
    )


class Orchestrator:
    """Thin conductor for one forge-mcp run (§8.1).

    Design: the engine wires collaborators but delegates state, lifecycle,
        phase sequencing, policy, and SDK work to focused modules.
    Implementation: prepare run dirs/logging/status, execute phases under
        timeout handling, build RunResult, and always release resources.
    Example: result = await Orchestrator(prepared, inputs, config, ctx, drivers).run().
    """

    def __init__(
        self, prepared: PreparedRun, inputs: RunForgeInput, config: Any, ctx: Any, drivers: Any
    ) -> None:
        """Store constructor dependencies for run().

        Design: preflight owns validation/lock acquisition; orchestrator accepts
            the PreparedRun handoff and injected drivers.
        Implementation: plain attribute storage, no IO until run().
        Example: Orchestrator(prepared, inputs, config, ctx, drivers).
        """
        self._prepared = prepared
        self._inputs = inputs
        self._config = config
        self._ctx = ctx
        self._drivers = drivers

    async def run(self) -> RunResult:
        """Execute the run and return a terminal RunResult.

        Design: §6.3 terminal completed/incomplete/failed states are normal
            returns, with cancellation and timeout paths distinguished. §8.1
            control-flow invariant: logger creation, disk-space warn, and
            PhaseDeps construction occur BEFORE the outer try so their
            failures cannot be funneled into handle_failure and deps is bound
            for every except handler. previous_umask is captured immediately
            before the try.
        Implementation: build collaborators (run_dir, sm, status, logger,
            deps, disk warn) before the try; enforce private umask just before
            entering the try; await phase task under timeout, apply caps for
            inline outcomes, release lock and restore umask in finally.
        Example: result = await orchestrator.run().
        """
        started_at = datetime.now(UTC)
        run_dir = create_run_dir(self._prepared.harness_dir, self._prepared.run_id)
        ledger = RunLedger()
        sm = RunStateMachine(
            run_dir / "state.json",
            RunState(
                state="init",
                run_id=self._prepared.run_id,
                iteration=0,
                target_dir=str(self._prepared.harness_dir.parent),
                started_at=started_at,
                last_updated_at=started_at,
            ),
        )
        status = Status(self._prepared.run_id, self._ctx, run_dir / "status.log")
        status.set_max_iterations(self._inputs.max_iterations)
        logger = logging.getLogger(f"forge_mcp.run.{self._prepared.run_id}")
        logger.propagate = False
        handler = logging.FileHandler(run_dir / "run.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        await disk_space_warn_if_low(run_dir, status, logger, ledger)  # §8.1
        deps = PhaseDeps(
            drivers=self._drivers,
            status=status,
            logger=logger,
            run_dir=run_dir,
            target_dir=self._prepared.harness_dir.parent,
            inputs=self._inputs,
            config=self._config,
        )
        previous_umask = os.umask(0o077)  # §8.1 — captured just before the try
        try:
            try:
                prune_old_runs(
                    self._prepared.harness_dir,
                    keep_last=self._config.keep_runs,
                    current_run_id=self._prepared.run_id,
                )
            except Exception:
                ledger.warnings.append("run retention pruning failed (non-fatal)")
            terminal_status = "failed"
            try:
                resume_point = self._prepared.resume_point
                if resume_point is None:
                    sm.transition("canonicalizing")  # §8.1
                    canonicalize_design(self._inputs, run_dir, ledger)
                    git_state = capture_state(deps.target_dir)
                    if git_state is not None:
                        atomic_write_text(run_dir / "inputs" / "git-state.txt", git_state)
                    uncommitted = capture_uncommitted(deps.target_dir)
                    if uncommitted:
                        path = run_dir / "inputs" / "git-uncommitted.txt"
                        atomic_write_text(path, uncommitted)
                        ledger.git_uncommitted_path = str(path)
                    await warn_if_missing_target_agents_md(deps.target_dir, ledger, status)  # §8.1
                    phase_task = run_phases(deps, sm, ledger, git_state)
                else:
                    ledger.resumed_from_iteration = resume_point.last_completed_iteration
                    prepare_resume(run_dir, resume_point)
                    git_state_path = run_dir / "inputs" / "git-state.txt"
                    git_state = git_state_path.read_text() if git_state_path.exists() else None
                    ledger.gap_fingerprints = _reconstruct_fingerprints(run_dir, resume_point)
                    phase_task = run_iteration_loop(
                        deps,
                        sm,
                        ledger,
                        git_state,
                        start_iteration=resume_point.start_iteration,
                    )
                terminal_status, _ = await asyncio.wait_for(
                    phase_task, timeout=self._inputs.max_runtime_minutes * 60
                )
                if terminal_status == "incomplete":
                    # §8.1 / §8.3 — iteration-cap branch reads latest eval.json
                    # via the same helper handle_timeout uses.
                    ledger.unresolved_gaps = collect_unresolved_gaps(run_dir)
                sm.transition("finalizing")
                ledger.decided_at = datetime.now(UTC)
                sm.transition(terminal_status)  # type: ignore[arg-type]
                await emit_terminal_status(status, terminal_status)  # §8.1
            except TimeoutError:
                terminal_status = "incomplete"
                await handle_timeout(sm, ledger, deps)
            except asyncio.CancelledError:
                # §8.1 / §8.5 step 5 — re-raises; no RunResult produced on this path.
                await handle_cancellation(sm, ledger, deps, self._prepared.lock)
                raise
            except Exception as exc:  # §8.1 — KeyboardInterrupt/SystemExit must propagate
                terminal_status = "failed"
                await handle_failure(sm, ledger, deps, exc)
            if terminal_status in ("completed", "incomplete"):
                # §8.1: caps run on every result-producing inline outcome.
                apply_caps_and_overflow(ledger, run_dir, logger)
            result = build_result(
                run_id=self._prepared.run_id,
                run_dir=run_dir,
                status=terminal_status,
                inputs=self._inputs,
                sm=sm,
                ledger=ledger,
                started_at=started_at,
            )
            return result
        finally:
            if not ledger.lock_released:
                self._prepared.lock.release()
                ledger.lock_released = True
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            os.umask(previous_umask)
