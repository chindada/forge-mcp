"""The §8 run conductor: wire every module into a single-plan, direct-edit run.

The Orchestrator.run coroutine is the integration capstone. It acquires the
per-target lock, freezes the design into a run-local spec, plans ONCE, then runs
that single plan's iteration loop directly on target_dir (generate → verify →
evaluate → triage → converge, applying design-fault amendments in-loop) until the
plan is `done` or stops honestly, and finalises an honest RunResult. The engine
performs NO git mutation — the Generator's edits are left uncommitted in
target_dir for the human's own git — and its terminal cleanup (mark state, close
drivers, release lock, restore umask) runs in a `finally` and re-raises an
injected CancelledError.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from forge_mcp.artifacts import init_run_layout
from forge_mcp.config import create_run_dir
from forge_mcp.drivers.planner import run_planner
from forge_mcp.lockfile import TargetLock
from forge_mcp.models import EvalResult, Plan, RunResult, TriageResult
from forge_mcp.orchestrator.lifecycle import PlanReport, build_run_result
from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import light_replace, write_json

if TYPE_CHECKING:
    from forge_mcp.artifacts import RunLayout
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner

_RESTRICTIVE_UMASK = 0o077


@dataclass
class _RunState:
    """Mutable bookkeeping carried across the §8 single-plan run.

    Design: §8 with one plan and one tree there is no cross-wave state to
        accumulate — only the loop's total iteration count, an optional
        orchestrator-level stop_reason, and the single PlanLoopResult that
        finalisation reads for honest gap projection and the verified verdict.
    Implementation: plain mutable dataclass; `report` holds the loop's terminal
        PlanLoopResult (None until the loop runs); `stop_reason` stays None unless
        the engine itself records a reason; `iterations` mirrors the loop count.
    Example: _RunState() starts a run; after the loop iterations and report are set.
    """

    iterations: int = 0
    stop_reason: str | None = None
    report: PlanLoopResult | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §9 the run-level state machine records a last_updated_at on every
        transition; the engine is the caller so it supplies the wall clock. This
        is the checkpoint clock only — the run-dir freshness clock (`when`) is
        injected by the caller (I12), never read here.
    Implementation: datetime.now in UTC serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


class Orchestrator:
    """The §8 run conductor wiring every module into a single-plan, direct-edit run.

    Design: §8 a forge run is a single async lifecycle — lock the target, freeze
        design→spec, PLAN ONCE, run that one plan's iteration loop directly on
        target_dir until it is `done` or stops honestly, then finalise an honest
        RunResult. The engine performs NO git mutation (the Generator leaves its
        edits uncommitted for the human's git) and its terminal cleanup runs in a
        `finally` that re-raises CancelledError.
    Implementation: a stateless class whose `run` coroutine owns all per-run state
        locally; the SDK runners are injected so the engine never imports the SDK
        at module scope, and `when` is injected so each call gets a fresh
        timestamped run dir (I12) without reading the wall clock for the dir name.
    Example: ``await Orchestrator().run(target_dir=..., design_text=..., ...)``.
    """

    async def run(
        self,
        *,
        target_dir: Path,
        design_text: str,
        design_fingerprint: str,
        max_iterations: int,
        max_runtime_minutes: int,
        claude_runner: ClaudeRunner,
        codex_runner: CodexRunner,
        when: time.struct_time,
    ) -> RunResult:
        """Drive one single-plan, direct-edit forge run end-to-end (§3.1 superseded).

        Design: §8 the conductor locks the target, freezes design→spec via
            init_run_layout, plans ONCE (run_planner → one Plan), runs that plan's
            iteration loop directly on target_dir, and finalises an honest
            RunResult. status is 'completed' iff the plan is `done`, else
            'incomplete' with a synthesized stop_reason; 'failed' only on an
            orchestrator-internal error. `verified` is True only when the plan is
            done AND a verification_command was declared — an honest False
            otherwise. It performs NO git mutation; terminal cleanup runs in a
            `finally` that re-raises an injected CancelledError.
        Implementation: set a restrictive umask (restored in finally); acquire the
            lock keyed on the run-dir name; build the layout and RunStateMachine;
            transition planning→run_planner(→Plan)→_persist_plan→executing→
            run_plan_loop(plan, target_dir, spec_text, spec_fingerprint, …)→
            finalizing→_finalize. The whole body is wrapped so the `finally` marks
            the terminal state, closes both drivers best-effort, and releases the
            lock with NO git ops; an injected CancelledError runs that cleanup and
            re-raises.
        Example: a one-plan run that writes out.txt finalises status 'completed'
            with out.txt present in target_dir and verified False (no command).
        """
        prev_umask = os.umask(_RESTRICTIVE_UMASK)
        lock = TargetLock()
        sm: RunStateMachine | None = None
        run_dir: Path | None = None
        status = "failed"
        try:
            run_dir = create_run_dir(target_dir, when)
            run_id = run_dir.name
            lock.acquire(target_dir, run_id, _now())
            layout = init_run_layout(run_dir, design_text, design_fingerprint=design_fingerprint)
            sm = RunStateMachine(layout)

            state = _RunState()

            # --- PLAN (once) ---
            sm.transition("planning", now=_now())
            plan = await run_planner(
                claude_runner,
                spec_text=layout.spec_md.read_text(),
                plan_schema=Plan.model_json_schema(),
                cwd=target_dir,
                run_log_path=layout.run_log,
            )
            self._persist_plan(layout, plan)

            # --- EXECUTE (one plan loop, directly on target_dir) ---
            sm.transition("executing", now=_now())
            report = await run_plan_loop(
                layout=layout,
                plan=plan,
                target_dir=target_dir,
                spec_text=layout.spec_md.read_text(),
                spec_fingerprint=layout.spec_fingerprint.read_text(),
                claude_runner=claude_runner,
                codex_runner=codex_runner,
                schemas={
                    "eval": EvalResult.model_json_schema(),
                    "triage": TriageResult.model_json_schema(),
                },
                max_iterations=max_iterations,
            )
            state.report = report
            state.iterations = report.iterations

            # --- FINALIZE ---
            sm.transition("finalizing", now=_now())
            status, result = self._finalize(run_dir=run_dir, plan=plan, state=state)
            return result
        except asyncio.CancelledError:
            # Host disconnect / runtime-cap wait_for: run terminal cleanup, re-raise.
            status = "failed"
            raise
        except Exception as exc:  # noqa: BLE001 — orchestrator-internal error -> failed.
            # §8 an orchestrator-internal/unhandled error finalises a `failed`
            # RunResult with failure_kind (NOT a per-plan or convergence failure).
            status = "failed"
            return build_run_result(
                status="failed",
                run_dir=str(run_dir) if run_dir is not None else "",
                iterations=0,
                non_completed=[],
                stop_reason=None,
                verified=False,
                summary=f"Orchestrator failed: {type(exc).__name__}: {exc}",
                failure_kind=type(exc).__name__,
            )
        finally:
            await self._terminal_cleanup(
                sm=sm,
                status=status,
                claude_runner=claude_runner,
                codex_runner=codex_runner,
                lock=lock,
                prev_umask=prev_umask,
            )

    def _persist_plan(self, layout: RunLayout, plan: Plan) -> None:
        """Write plan.json and plan.md after planning (§10).

        Design: §10 the planner output is durably recorded so the run is
            reconstructable: plan.json holds the structured single Plan and
            plan.md carries its human-readable body. With one plan the artifacts
            flatten to the run root (no plans/<id>/ nesting, no plan-<id>.md).
        Implementation: write_json the Plan model durably to layout.plan_json,
            then light_replace plan.body into layout.plan_md (derived artifact,
            non-durable).
        Example: _persist_plan(layout, plan) writes plan.json and plan.md.
        """
        write_json(layout.plan_json, plan, durable=True)
        light_replace(layout.plan_md, plan.body)

    def _finalize(
        self,
        *,
        run_dir: Path,
        plan: Plan,
        state: _RunState,
    ) -> tuple[str, RunResult]:
        """Project the terminal RunResult and its status from the single plan (§8).

        Design: §8 with one plan, status is 'completed' iff the plan is `done`
            (no stop_reason); otherwise 'incomplete' with a stop_reason synthesized
            from the plan's own reason. `verified` is True ONLY when the plan is
            done AND a verification_command was declared (the done terminal_state IS
            the per-iteration gate) — absent a command it is an honest False. A
            non-completed plan contributes its freshest gap set.
        Implementation: read the loop's PlanLoopResult; done = terminal_state ==
            'done'; verified = done ∧ (plan.verification_command is not None);
            status = 'completed' iff done; for a non-done plan build one PlanReport
            from its last_gaps/synthesized/stop_reason and synthesize a run-level
            stop_reason from the plan's reason when none is set; call
            build_run_result.
        Example: one done plan, no verification_command → status 'completed',
            verified False, stop_reason None.
        """
        report = state.report
        assert report is not None  # set before _finalize on every non-error path
        done = report.terminal_state == "done"
        verified = done and (plan.verification_command is not None)

        if done:
            status = "completed"
            non_completed: list[PlanReport] = []
            summary = "Plan completed."
            stop_reason = None
        else:
            status = "incomplete"
            non_completed = [
                PlanReport(
                    plan_id="plan",
                    terminal_state=report.terminal_state,
                    gaps=report.last_gaps,
                    synthesized=report.synthesized,
                    failure_reason=report.stop_reason,
                )
            ]
            summary = "Run finished incomplete; see unresolved_gaps."
            # I7/§8: an incomplete run MUST carry a stop_reason. Synthesize it from
            # the plan's own reason (iteration cap, gap non-progress, amendment
            # thrash, or a plan-loop failure) when the engine set none itself.
            stop_reason = state.stop_reason or report.stop_reason or "plan did not complete"

        result = build_run_result(
            status=status,
            run_dir=str(run_dir),
            iterations=state.iterations,
            non_completed=non_completed,
            stop_reason=stop_reason,
            verified=verified,
            summary=summary,
        )
        return status, result

    async def _terminal_cleanup(
        self,
        *,
        sm: RunStateMachine | None,
        status: str,
        claude_runner: ClaudeRunner,
        codex_runner: CodexRunner,
        lock: TargetLock,
        prev_umask: int,
    ) -> None:
        """Mark terminal state, close drivers, release the lock, restore umask — no git.

        Design: §8 the run's `finally` must always converge: record the terminal
            run state, close both SDK drivers best-effort, release the per-target
            lock so a later run can acquire it, and restore the umask. It performs
            NO git operations — the Generator's edits are left for the human's git —
            and must never raise so it cannot mask an in-flight CancelledError being
            re-raised.
        Implementation: transition the state machine to the terminal `status` when
            it is not already terminal (best-effort); await aclose() on each runner
            inside a try/except; release the lock; os.umask(prev_umask). Every step
            is guarded so cleanup never raises.
        Example: after a completed run the lock file is gone and umask is restored.
        """
        if sm is not None:
            try:
                if sm.payload.state not in ("completed", "incomplete", "failed"):
                    sm.transition(status, now=_now())
            except Exception:  # noqa: BLE001 — best-effort terminal checkpoint.
                pass
        for runner in (claude_runner, codex_runner):
            await self._close_runner(runner)
        lock.release()
        os.umask(prev_umask)

    async def _close_runner(self, runner: object) -> None:
        """Best-effort await one SDK driver's aclose, swallowing every error (§8).

        Design: §8 terminal cleanup closes drivers but must never raise; a driver
            whose aclose fails (or that is already closed) cannot be allowed to mask
            the run's real outcome or a re-raised CancelledError.
        Implementation: if the runner exposes an aclose, await it inside a
            try/except that swallows Exception (NOT BaseException, so a propagating
            CancelledError still unwinds). The fakes and real drivers both expose an
            async aclose() no-op/coroutine.
        Example: await _close_runner(FakeClaudeRunner([])) returns without raising.
        """
        aclose = getattr(runner, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:  # noqa: BLE001 — best-effort driver close.
            pass
