"""§8.1 Orchestrator engine — thin conductor over collaborators."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.experimental.task_context import ServerTaskContext

from ..artifacts import (
    atomic_write_text,
    create_run_dir,
    prune_old_runs,
    write_design_fingerprint,
    write_design_flaws,
    write_prior_attempts,
)
from ..doctor import disk_space_warn_if_low
from ..gitguard import capture_state, capture_uncommitted
from ..models import EvalResult, RunForgeInput, RunResult
from ..preflight import PreparedRun
from ..state import RunState
from ..status import Status
from . import lineage
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


def _accepts_task_kw(func: Any) -> bool:
    """Return True when a callable accepts the task keyword (§C5).

    Design: production phase helpers accept task, but focused tests monkeypatch
        older helper fakes; this shim preserves test isolation without changing
        runtime behavior.
    Implementation: inspect the callable signature and treat **kwargs as
        accepting task.
    Example: if _accepts_task_kw(run_phases): pass task=self._task.
    """
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return True
    return "task" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


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
        self,
        prepared: PreparedRun,
        inputs: RunForgeInput,
        config: Any,
        ctx: Any,
        drivers: Any,
        *,
        task: ServerTaskContext | None = None,
        task_id: str | None = None,
        harness_token: str | None = None,
    ) -> None:
        """Store constructor dependencies for run().

        Design: preflight owns validation/lock acquisition; orchestrator accepts
            the PreparedRun handoff and injected drivers. §C5 adds optional task
            context and id while preserving direct-call construction. §R3.2
            adds optional harness_token for active-run resource discovery.
        Implementation: plain attribute storage, no IO until run(). Store task
            for Status/phase polling, task_id for terminal RunResult, and
            harness_token for the §R3 active-run registry.
        Example: Orchestrator(prepared, inputs, config, ctx, drivers,
            task=task, harness_token='aBcDeFgHiJkL').
        """
        self._prepared = prepared
        self._inputs = inputs
        self._config = config
        self._ctx = ctx
        self._drivers = drivers
        self._task = task
        self._task_id = task_id
        self._harness_token = harness_token

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
        status = Status(self._prepared.run_id, self._ctx, run_dir / "status.log", task=self._task)
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
        # §R3.2 — scope is built before the try so finally can see it; registry
        # mutation itself stays inside the try that also releases the lock.
        from ..resources import _ResourceScope, deregister_active_run, register_active_run

        scope: _ResourceScope | None = None
        if self._harness_token is not None:
            scope = _ResourceScope(
                run_id=self._prepared.run_id,
                harness_dir=self._prepared.harness_dir,
                harness_token=self._harness_token,
            )
        try:
            if scope is not None:
                register_active_run(scope)  # §R3.2 — inside try
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
                inputs_dir = run_dir / "inputs"
                if resume_point is None:
                    sm.transition("canonicalizing")  # §8.1
                    canonicalize_design(self._inputs, run_dir, ledger)

                fp: str | None = None
                try:
                    design_text = (inputs_dir / "design.md").read_text(encoding="utf-8")
                    fp = lineage.fingerprint_design(design_text)
                    write_design_fingerprint(inputs_dir, fp)
                except OSError as exc:
                    fp = None  # §L10 — disables lineage block below; §L-Inv 2
                    ledger.warnings.append(
                        f"design.fingerprint write failed (cold start): {type(exc).__name__}: {exc}"
                    )
                    logger.warning("design.fingerprint write failed", exc_info=True)

                if (
                    fp is not None
                    and not self._inputs.ignore_prior_attempts
                    and self._config.lineage_top_k > 0
                ):
                    try:
                        candidates = lineage.find_lineage_runs(
                            self._prepared.harness_dir,
                            fp,
                            current_run_id=self._prepared.run_id,
                            top_k=self._config.lineage_top_k,
                        )
                        summaries = [
                            summary
                            for summary in (
                                lineage.summarize_prior_run(candidate.run_dir)
                                for candidate in candidates
                            )
                            if summary is not None
                        ]
                        if summaries:
                            digest = lineage.render_prior_attempts(summaries)
                            overflow_path = write_prior_attempts(inputs_dir, digest)
                            ledger.linked_prior_runs = [summary.run_id for summary in summaries]
                            ledger.lineage_overflow_path = overflow_path
                            if overflow_path is not None:
                                ledger.warnings.append(
                                    "lineage digest exceeded 32768 bytes; "
                                    f"overflow at {overflow_path.name}"
                                )
                            await status.update(
                                phase="canonicalizing",
                                agent="orchestrator",
                                message=f"lineage: {len(summaries)} prior runs feeding planner",
                            )
                    except Exception as exc:  # noqa: BLE001 — §L-Inv 1 best-effort.
                        ledger.warnings.append(
                            f"lineage discovery failed (cold start): {type(exc).__name__}: {exc}"
                        )
                        logger.warning("lineage discovery failed", exc_info=True)

                if resume_point is None:
                    git_state = capture_state(deps.target_dir)
                    if git_state is not None:
                        atomic_write_text(run_dir / "inputs" / "git-state.txt", git_state)
                    uncommitted = capture_uncommitted(deps.target_dir)
                    if uncommitted:
                        path = run_dir / "inputs" / "git-uncommitted.txt"
                        atomic_write_text(path, uncommitted)
                        ledger.git_uncommitted_path = str(path)
                    await warn_if_missing_target_agents_md(deps.target_dir, ledger, status)  # §8.1
                    if _accepts_task_kw(run_phases):
                        phase_task = run_phases(deps, sm, ledger, git_state, task=self._task)
                    else:
                        phase_task = run_phases(deps, sm, ledger, git_state)
                else:
                    ledger.resumed_from_iteration = resume_point.last_completed_iteration
                    prepare_resume(run_dir, resume_point)
                    git_state_path = run_dir / "inputs" / "git-state.txt"
                    git_state = git_state_path.read_text() if git_state_path.exists() else None
                    ledger.gap_fingerprints = _reconstruct_fingerprints(run_dir, resume_point)
                    if _accepts_task_kw(run_iteration_loop):
                        phase_task = run_iteration_loop(
                            deps,
                            sm,
                            ledger,
                            git_state,
                            start_iteration=resume_point.start_iteration,
                            task=self._task,
                        )
                    else:
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
                if terminal_status == "incomplete" and ledger.stop_reason:
                    sm.transition("incomplete", reason=ledger.stop_reason)
                else:
                    sm.transition(terminal_status)  # type: ignore[arg-type]
                design_flaws_full = [gap.model_copy(deep=True) for gap in ledger.design_flaw_gaps]
                try:
                    write_design_flaws(run_dir, design_flaws_full)
                except OSError as exc:
                    ledger.warnings.append(
                        "design_flaws.json write failed (lineage feed-forward disabled): "
                        f"{type(exc).__name__}: {exc}"
                    )
                    logger.warning("design_flaws.json write failed", exc_info=True)
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
                task_id=self._task_id,
                harness_token=self._harness_token,
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
            if scope is not None:
                # §R3.2 — idempotent and after lock release/umask restore.
                deregister_active_run(scope.harness_token, scope.run_id)
