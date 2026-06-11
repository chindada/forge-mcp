"""§9 phase orchestration helpers over injected dependencies."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.experimental.task_context import ServerTaskContext

from ..artifacts import atomic_write_json, atomic_write_text, write_sessions_json
from ..config import RunConfig
from ..drivers._claude import is_transient_error as is_transient_claude
from ..drivers._codex import is_transient_error as is_transient_codex
from ..errors import PlannerNoOutputError
from ..gitguard import capture_state, changed_files, diff_state
from ..models import EvalGap, EvalResult, RunForgeInput
from ..runcontext import RunContext
from ..verifier import render as render_verification
from ..verifier import run_verification
from . import lifecycle
from .convergence import NON_PROGRESS_WINDOW, detect_non_progress, fingerprint_gaps
from .emitter import _Emitter, _NullNotifier
from .handoff import validate_contract, validate_plan
from .ledger import RunLedger
from .retry import with_schema_retry, with_transient_retry
from .statemachine import RunStateMachine
from .triage import classify_gaps
from .watchdog import with_phase_watchdog


@dataclass(frozen=True, slots=True)
class PhaseDeps:
    """Frozen dependency bundle passed to phase helpers.

    Design: §8.3/§9 keep the engine a thin conductor and make phase helpers
        explicit about drivers, status, config, and artifact roots.
    Implementation: frozen slots dataclass, no behavior beyond grouping.
    Example: deps = PhaseDeps(drivers=d, status=s, logger=l, ...).
    """

    drivers: Any
    status: Any
    logger: Any
    run_dir: Path
    target_dir: Path
    inputs: RunForgeInput
    config: RunConfig
    emitter: _Emitter = field(default_factory=lambda: _Emitter(_NullNotifier(), None, "00000000"))


def _ctx(deps: PhaseDeps, iteration_n: int | None = None) -> RunContext:
    """Build a fresh RunContext for one driver call.

    Design: Invariant 3 forbids mutable shared driver context; each phase gets
        a new value object.
    Implementation: copy stable paths/config and optional iteration number.
    Example: ctx = _ctx(deps, iteration_n=1).
    """
    return RunContext(
        run_dir=deps.run_dir,
        target_dir=deps.target_dir,
        claude_config_dir=deps.config.claude_config_dir,
        claude_cli_path=deps.config.claude_cli_path,
        iteration_n=iteration_n,
    )


def _utcnow_iso() -> str:
    """Return a UTC timestamp for sessions.json records (§C2.4).

    Design: sessions.json is append-free forensic data, so stable sortable UTC
        strings make phase ordering easy to inspect.
    Implementation: use timezone-aware datetime and replace the UTC offset with
        Z for concise JSON artifacts.
    Example: ts = _utcnow_iso().
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _session_id_from(driver: Any) -> str | None:
    """Return a JSON-safe driver session id or None (§C2.5).

    Design: sessions.json is fail-soft forensic data, so test doubles or SDK
        drift must not make artifact writing fail.
    Implementation: read last_session_id and keep only non-empty strings.
    Example: sid = _session_id_from(deps.drivers.evaluator).
    """
    sid = getattr(driver, "last_session_id", None)
    return sid if isinstance(sid, str) and sid else None


async def run_plan_phase(
    deps: PhaseDeps,
    sm: RunStateMachine,
    ledger: RunLedger,
    *,
    task: ServerTaskContext | None = None,
) -> None:
    """Run the planner phase and record warnings/completion (§C2.5, §C1.6).

    Design: §9.1 — planner predates the loop, so its RunContext carries
        run_dir + Claude config only (no target_dir, no iteration_n). §C2.5
        writes plan/sessions.json before planned; §C1.6 polls task cancellation.
    Implementation: build the planner ctx with keyword args to avoid the §8.4
        field-order pitfall; append the literal 'plan' to completed_phases for
        §7/§9.1 parity and record the planner session id fail-soft.
    Example: await run_plan_phase(deps, sm, ledger, task=None).
    """
    sm.transition("planning")
    lifecycle.poll_task_cancellation(task)
    await deps.status.update(phase="planning", agent="planner", message="writing plan")
    planner_ctx = RunContext(
        run_dir=deps.run_dir,
        claude_config_dir=deps.config.claude_config_dir,
        claude_cli_path=deps.config.claude_cli_path,
    )
    started = _utcnow_iso()
    warning = await with_transient_retry(
        lambda: deps.drivers.planner.write_plan(planner_ctx), is_transient=is_transient_claude
    )
    completed = _utcnow_iso()
    if warning:
        ledger.warnings.append(warning)
    plan_path = deps.run_dir / "plan" / "plan.md"
    problems = validate_plan(plan_path.read_text()) if plan_path.exists() else ["plan.md missing"]
    if not plan_path.exists() or problems:
        warning = await with_transient_retry(
            lambda: deps.drivers.planner.write_plan(planner_ctx),
            is_transient=is_transient_claude,
        )
        if warning:
            ledger.warnings.append(warning)
        if not plan_path.exists():
            raise PlannerNoOutputError(
                "planner produced no plan.md and Write tool_use recovery failed after two attempts"
            )
        problems = validate_plan(plan_path.read_text())
        if problems:
            ledger.warnings.append(f"plan.md degenerate after re-author: {problems}")
    await deps.emitter.emit_path("plan/plan.md")  # §S5.2
    ledger.completed_phases.append("plan")  # §7 / §9.1
    write_sessions_json(
        deps.run_dir / "plan" / "sessions.json",
        iteration=0,
        entries=[
            {
                "phase": "planning",
                "sdk": "claude",
                "session_id": _session_id_from(deps.drivers.planner),
                "started_at": started,
                "completed_at": completed,
            }
        ],
    )
    await deps.emitter.emit_path("plan/sessions.json")  # §S5.2
    sm.transition("planned")
    lifecycle.poll_task_cancellation(task)


async def _status_cb(deps: PhaseDeps, phase: str, iteration_n: int, **kwargs: Any) -> None:
    """Forward generator stream updates to Status with phase context.

    Design: §12 stream and phase events share one status sink.
    Implementation: fill phase/iteration defaults around driver-supplied fields.
    Example: await _status_cb(deps, 'iter_generating', 1, message='x').
    """
    await deps.status.update(phase=phase, iteration=iteration_n, **kwargs)


def _make_status_cb(deps: PhaseDeps, phase: str, iteration_n: int) -> Any:
    """Create a status callback bound to one iteration.

    Design: §9.2 generator stream events need iteration context without late
        binding loop variables in lambdas.
    Implementation: close over copied arguments in an async nested function.
    Example: cb = _make_status_cb(deps, 'iter_generating', 1).
    """

    async def callback(**kwargs: Any) -> None:
        """Forward one driver status event.

        Design: driver callbacks use keyword-only event fields.
        Implementation: delegate to _status_cb with bound phase/iteration.
        Example: await callback(agent='generator', message='x').
        """
        await _status_cb(deps, phase, iteration_n, **kwargs)

    return callback


async def _run_generator(deps: PhaseDeps, iteration_n: int) -> None:
    """Invoke generator.implement for one iteration (§F2.4).

    Design: §F2.4 deletes the §H7 signature-inspection branch along with the
        network knob; the generator seam takes codex_bin and status_cb only.
    Implementation: build the bound status callback and call implement
        directly with the production arguments.
    Example: await _run_generator(deps, 1).
    """
    await deps.drivers.generator.implement(
        _ctx(deps, iteration_n),
        codex_bin=deps.config.codex_bin,
        status_cb=_make_status_cb(deps, "iter_generating", iteration_n),
    )


async def _evaluate(
    deps: PhaseDeps, iteration_n: int, retry: bool, changed: list[str] | None
) -> EvalResult:
    """Invoke evaluator.evaluate with backward-compatible fake support (§H8).

    Design: production evaluation accepts changed_files, but older focused fakes
        should continue to exercise the same loop behavior without that keyword.
    Implementation: inspect the bound method and pass changed_files only when
        accepted; retry is part of the original schema-retry seam.
    Example: er = await _evaluate(deps, 1, False, ['x.py']).
    """
    evaluate = deps.drivers.evaluator.evaluate
    kwargs: dict[str, Any] = {"retry": retry}
    if "changed_files" in inspect.signature(evaluate).parameters:
        kwargs["changed_files"] = changed
    return await evaluate(_ctx(deps, iteration_n), **kwargs)


async def _write_remediation(
    deps: PhaseDeps,
    iteration_n: int,
    *,
    next_iteration_n: int,
    eval_result: EvalResult,
    pivot: bool,
) -> str | None:
    """Invoke write_remediation with backward-compatible fake support (§H3).

    Design: production remediation accepts pivot, but legacy tests may override
        the method without that keyword while still validating loop behavior.
    Implementation: inspect the bound method and pass pivot only when accepted.
    Example: await _write_remediation(deps, 1, next_iteration_n=2, eval_result=er, pivot=False).
    """
    write_remediation = deps.drivers.evaluator.write_remediation
    kwargs: dict[str, Any] = {"next_iteration_n": next_iteration_n, "eval_result": eval_result}
    if "pivot" in inspect.signature(write_remediation).parameters:
        kwargs["pivot"] = pivot
    return await write_remediation(_ctx(deps, iteration_n), **kwargs)


def _supports_hardened_remediation(deps: PhaseDeps) -> bool:
    """Return True when the evaluator exposes the hardened remediation seam (§H6).

    Design: §H6 validation is wired to the new pivot-capable remediation seam;
        legacy focused fakes remain inert so old behavior tests still isolate
        unrelated loop semantics.
    Implementation: inspect the bound write_remediation signature for pivot.
    Example: _supports_hardened_remediation(deps) is True for EvaluatorDriver.
    """
    return "pivot" in inspect.signature(deps.drivers.evaluator.write_remediation).parameters


def _make_remediation_call(
    deps: PhaseDeps,
    iteration_n: int,
    next_iteration_n: int,
    eval_result: EvalResult,
    pivot: bool,
) -> Any:
    """Bind remediation arguments for transient retry (§H3, §H5).

    Design: retry must re-invoke the same remediation request without Python
        loop-variable late binding hazards.
    Implementation: copy arguments into an async zero-argument callback.
    Example: await with_transient_retry(_make_remediation_call(...), is_transient=p).
    """

    async def call() -> str | None:
        """Run one bound remediation attempt.

        Design: each transient retry uses the same fresh RunContext values.
        Implementation: delegate to _write_remediation with copied arguments.
        Example: warning = await call().
        """
        return await _write_remediation(
            deps,
            iteration_n,
            next_iteration_n=next_iteration_n,
            eval_result=eval_result,
            pivot=pivot,
        )

    return call


async def _seed_start_contract(deps: PhaseDeps, ledger: RunLedger, start_iteration: int) -> None:
    """Seed iteration-{start}/contract.md, branching on resume (§H13, §H2.5).

    Design: §H13 seeds plan.md for start==1, but a resumed iteration (start>1)
        must re-author from the prior remediation/eval reconstructed from
        iteration-(start-1)/eval.json so accumulated direction is not lost;
        seeding must never crash a resume, so unreadable artifacts fall back.
    Implementation: skip if a contract already exists; for start==1 copy plan.md;
        for start>1 first reuse the newest iteration-<start>.interrupted-*/contract.md
        when it passes §H6 validate_contract, else reconstruct EvalResult from the
        prior eval.json and call write_remediation, validating once via §H6 with a
        single re-author, then fall back to plan.md (with a ledger warning) if still
        degenerate.
    Example: await _seed_start_contract(deps, ledger, 2).
    """
    contract = deps.run_dir / f"iteration-{start_iteration}" / "contract.md"
    if contract.exists():
        return
    contract.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan_text = (deps.run_dir / "plan" / "plan.md").read_text()
    if start_iteration == 1:
        atomic_write_text(contract, plan_text)
        await deps.emitter.emit_iteration(start_iteration, "contract.md")  # §S5.2
        return
    # §H2.2 reuse durable contract: prepare_resume archived the in-flight
    # iteration to iteration-<start>.interrupted-<ts>/; recover the prior run's
    # remediation contract from the newest such archive before re-authoring.
    archives = sorted(
        deps.run_dir.glob(f"iteration-{start_iteration}.interrupted-*"),
        key=lambda p: p.name,
    )
    if archives:
        archived_contract = archives[-1] / "contract.md"
        try:
            archived_text = archived_contract.read_text()
        except OSError:
            archived_text = None
        # §H2.5 reuse only a well-formed (§H6) durable contract; otherwise fall
        # through to the re-author path below.
        if archived_text is not None and not validate_contract(archived_text):
            atomic_write_text(contract, archived_text)
            await deps.emitter.emit_iteration(start_iteration, "contract.md")
            ledger.warnings.append(
                f"Resume reused durable contract for iteration-{start_iteration} from archive"
            )
            return
    prior_eval_path = deps.run_dir / f"iteration-{start_iteration - 1}" / "eval.json"
    try:
        eval_result = EvalResult.model_validate_json(prior_eval_path.read_text())
    except Exception:
        # §H2.5: prior eval missing/corrupt — degrade to plan.md so resume proceeds.
        atomic_write_text(contract, plan_text)
        await deps.emitter.emit_iteration(start_iteration, "contract.md")
        ledger.warnings.append(
            f"Resume could not reconstruct eval for iteration-{start_iteration}; "
            "seeded contract from plan.md"
        )
        return
    await _write_remediation(
        deps,
        start_iteration - 1,
        next_iteration_n=start_iteration,
        eval_result=eval_result,
        pivot=False,
    )
    if not _supports_hardened_remediation(deps):
        await deps.emitter.emit_iteration(start_iteration, "contract.md")
        return
    problems = (
        validate_contract(contract.read_text()) if contract.exists() else ["contract.md missing"]
    )
    if problems:
        await _write_remediation(
            deps,
            start_iteration - 1,
            next_iteration_n=start_iteration,
            eval_result=eval_result,
            pivot=False,
        )
        problems = (
            validate_contract(contract.read_text())
            if contract.exists()
            else ["contract.md missing"]
        )
    if problems:
        atomic_write_text(contract, plan_text)
        await deps.emitter.emit_iteration(start_iteration, "contract.md")
        ledger.warnings.append(
            f"Resume re-author for iteration-{start_iteration} stayed degenerate "
            f"({problems}); seeded contract from plan.md"
        )
    else:
        await deps.emitter.emit_iteration(start_iteration, "contract.md")


def _make_eval_call(deps: PhaseDeps, iteration_n: int, changed: list[str] | None) -> Any:
    """Create a schema-retry callback for evaluation (§11.6, §H8).

    Design: §11.6 expects a retry-flag callback without loop late binding, and
        §H8 adds a changed-files manifest as starting context.
    Implementation: bind iteration_n and changed paths, then call evaluator.
    Example: await with_schema_retry(_make_eval_call(deps, 1)).
    """

    async def call(retry: bool) -> EvalResult:
        """Run evaluator.evaluate with retry flag and changed-files manifest.

        Design: each retry uses the same iteration context and manifest.
        Implementation: build a fresh RunContext and pass retry/changed files.
        Example: await call(False).
        """
        return await _evaluate(deps, iteration_n, retry, changed)

    return call


def _make_triage_call(deps: PhaseDeps, iteration_n: int, er: EvalResult) -> Any:
    """Create a schema-retry callback for triage.

    Design: §11.6 triage retry needs stable EvalResult and iteration context.
    Implementation: bind both values in this helper and call the evaluator.
    Example: await with_schema_retry(_make_triage_call(deps, 1, er)).
    """

    async def call(retry: bool) -> Any:
        """Run evaluator.triage_design_flaws with a retry flag.

        Design: retry attempts classify the same evaluator gaps.
        Implementation: build a fresh RunContext and pass eval_result/retry.
        Example: await call(True).
        """
        return await deps.drivers.evaluator.triage_design_flaws(
            _ctx(deps, iteration_n), eval_result=er, retry=retry
        )

    return call


async def run_iteration_loop(
    deps: PhaseDeps,
    sm: RunStateMachine,
    ledger: RunLedger,
    base_git: str | None,
    *,
    start_iteration: int = 1,
    task: ServerTaskContext | None = None,
) -> tuple[str, int]:
    """Run generator/evaluator/remediation iterations (§9.2, §C2.5).

    Design: preserves load-bearing ordering: triage before git-violation
        synthesis, two-conjunct completion, and iter_remediating transition
        before remediation writing. §C2.5 writes sessions.json before iter_done
        and after remediation; §C1.6 polls cancellation at phase boundaries.
    Implementation: for each iteration create contract if needed, run Codex,
        evaluate with schema retry, triage gaps, check git diff, and either
        complete or write next contract while collecting phase session entries.
    Example: status, used = await run_iteration_loop(deps, sm, ledger, base_git, task=None).
    """
    design_text = (deps.run_dir / "inputs" / "design.md").read_text()
    # §H13/§H2.5: start==1 seeds plan.md; resume (start>1) re-authors from the
    # prior remediation/eval so accumulated direction survives the restart.
    await _seed_start_contract(deps, ledger, start_iteration)
    for iteration_n in range(start_iteration, deps.inputs.max_iterations + 1):
        iteration_dir = deps.run_dir / f"iteration-{iteration_n}"
        iteration_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        phase_sessions: list[dict[str, Any]] = []
        sm.transition("iter_generating", iteration=iteration_n)
        await deps.status.update(
            phase="iter_generating",
            agent="generator",
            message="implementing",
            iteration=iteration_n,
        )
        gen_iteration = iteration_n
        gen_started = _utcnow_iso()
        await with_phase_watchdog(
            with_transient_retry(
                lambda gen_iteration=gen_iteration: _run_generator(deps, gen_iteration),
                is_transient=is_transient_codex,
            ),
            status=deps.status,
            phase="iter_generating",
            iteration=iteration_n,
        )
        phase_sessions.append(
            {
                "phase": "iter_generating",
                "sdk": "codex",
                "session_id": _session_id_from(deps.drivers.generator),
                "started_at": gen_started,
                "completed_at": _utcnow_iso(),
            }
        )
        # §S5.2 — generator phase fsync lands iteration-N/summary.md; emit so
        # forge://<t>/<r>/iteration-N/summary.md subscribers get resources/updated.
        await deps.emitter.emit_iteration(iteration_n, "summary.md")
        lifecycle.poll_task_cancellation(task)
        if deps.inputs.verify_command:
            sm.transition("iter_verifying", iteration=iteration_n)
            await deps.status.update(
                phase="iter_verifying",
                agent="orchestrator",
                message="running verification command",
                iteration=iteration_n,
            )
            verify_started = _utcnow_iso()
            outcome = run_verification(
                deps.target_dir,
                deps.inputs.verify_command,
                timeout_seconds=deps.inputs.verify_timeout_seconds,
            )
            atomic_write_text(iteration_dir / "verify.txt", render_verification(outcome))
            await deps.emitter.emit_iteration(iteration_n, "verify.txt")  # §S5.2
            ledger.last_verification = outcome
            phase_sessions.append(
                {
                    "phase": "iter_verifying",
                    "sdk": None,
                    "session_id": None,
                    "started_at": verify_started,
                    "completed_at": _utcnow_iso(),
                }
            )
            lifecycle.poll_task_cancellation(task)
        sm.transition("iter_evaluating", iteration=iteration_n)
        changed = changed_files(deps.target_dir)
        eval_iteration = iteration_n
        eval_changed = changed
        eval_started = _utcnow_iso()
        er = await with_transient_retry(
            lambda eval_iteration=eval_iteration, eval_changed=eval_changed: with_schema_retry(
                _make_eval_call(deps, eval_iteration, eval_changed)
            ),
            is_transient=is_transient_claude,
        )
        # §S5.2 — evaluator phase fsync lands both eval.md and eval.json; each is
        # independently subscribable, so emit both so neither subscriber set is starved.
        await deps.emitter.emit_iteration(iteration_n, "eval.md")
        await deps.emitter.emit_iteration(iteration_n, "eval.json")
        phase_sessions.append(
            {
                "phase": "iter_evaluating",
                "sdk": "claude",
                "session_id": _session_id_from(deps.drivers.evaluator),
                "started_at": eval_started,
                "completed_at": _utcnow_iso(),
            }
        )
        lifecycle.poll_task_cancellation(task)
        triage_ran = False
        eval_for_loop = er
        if er.gaps:
            sm.transition("iter_triaging", iteration=iteration_n)
            triage_iteration = iteration_n
            triage_er = er
            triage_started = _utcnow_iso()
            triage_result = await with_transient_retry(
                lambda triage_iteration=triage_iteration, triage_er=triage_er: with_schema_retry(
                    _make_triage_call(deps, triage_iteration, triage_er)
                ),
                is_transient=is_transient_claude,
            )
            await deps.emitter.emit_iteration(iteration_n, "triage.json")  # §S5.2
            triage_ran = True
            phase_sessions.append(
                {
                    "phase": "iter_triaging",
                    "sdk": "claude",
                    "session_id": _session_id_from(deps.drivers.evaluator),
                    "started_at": triage_started,
                    "completed_at": _utcnow_iso(),
                }
            )
            lifecycle.poll_task_cancellation(task)
            outcome = classify_gaps(er, triage_result, design_text, iteration_n)
            ledger.design_flaw_gaps.extend(outcome.design_flaws)
            ledger.warnings.extend(outcome.warnings)
            eval_for_loop = EvalResult(
                no_gaps=False, gaps=outcome.code_bug_gaps, summary=er.summary
            )
        # §H6.3: drain gaps carried from the prior iteration (e.g. a degenerate
        # remediation contract detected after that iteration's fingerprint ran)
        # into this iteration's eval_for_loop, so they are fingerprinted, block a
        # false "completed", land in unresolved_gaps, and feed the next contract —
        # mirroring the git-violation / verify-fail synthesis placement below.
        if ledger.carried_gaps:
            eval_for_loop.gaps.extend(ledger.carried_gaps)
            ledger.carried_gaps = []
        git_diff = diff_state(base_git, capture_state(deps.target_dir))
        if git_diff:
            violation_path = iteration_dir / "git-violation.txt"  # §13 artifact tree
            atomic_write_text(violation_path, git_diff)
            await deps.emitter.emit_iteration(iteration_n, "git-violation.txt")  # §S5.2
            eval_for_loop.gaps.append(
                EvalGap(
                    title="Rule 11 git mutation detected",
                    severity="high",
                    design_doc_section="§11.3",
                    current_state="git refs changed during generator iteration",
                    expected_state="generator does not mutate git refs",
                    suggested_fix="undo git mutations and rerun without prohibited git commands",
                )
            )
        if (
            deps.inputs.verify_command
            and ledger.last_verification is not None
            and not ledger.last_verification.passed
        ):
            v = ledger.last_verification
            eval_for_loop.gaps.append(
                EvalGap(
                    title="Verification command failed",
                    severity="high",
                    design_doc_section="§H1",
                    current_state=(
                        f"`{deps.inputs.verify_command}` exited {v.exit_code} "
                        f"(timed_out={v.timed_out})"
                    ),
                    expected_state="verification command exits 0",
                    suggested_fix="make the verification command pass; see iteration-N/verify.txt",
                )
            )
        write_sessions_json(
            iteration_dir / "sessions.json", iteration=iteration_n, entries=list(phase_sessions)
        )
        await deps.emitter.emit_iteration(iteration_n, "sessions.json")  # §S5.2
        fingerprint = fingerprint_gaps(eval_for_loop.gaps)
        atomic_write_json(iteration_dir / "gap_fingerprint.json", sorted(fingerprint))
        await deps.emitter.emit_iteration(iteration_n, "gap_fingerprint.json")  # §S5.2
        sm.transition("iter_done", iteration=iteration_n, last_completed_iteration=iteration_n)
        lifecycle.poll_task_cancellation(task)
        ledger.completed_phases.append(f"iter-{iteration_n}")  # §7 / §9.2 step 6
        ledger.gap_fingerprints.append(fingerprint)
        signal = detect_non_progress(ledger.gap_fingerprints, window=NON_PROGRESS_WINDOW)
        effective_no_gaps = (not eval_for_loop.gaps) and (er.no_gaps or triage_ran)
        verify_passed = deps.inputs.verify_command is None or bool(
            ledger.last_verification and ledger.last_verification.passed
        )
        if effective_no_gaps and verify_passed:
            ledger.decided_at = None
            return ("completed", iteration_n)
        if signal.kind == "break":  # §H13 step 7: break before the cap (H-Inv 4)
            ledger.stop_reason = signal.reason
            return ("incomplete", iteration_n)
        if iteration_n == deps.inputs.max_iterations:
            # §8.3: unresolved_gaps is filled by the engine from the latest
            # eval.json via collect_unresolved_gaps — not by the loop.
            return ("incomplete", iteration_n)
        sm.transition("iter_remediating", iteration=iteration_n)
        next_n = iteration_n + 1
        remediation_started = _utcnow_iso()
        warning = await with_transient_retry(
            _make_remediation_call(
                deps,
                iteration_n,
                next_n,
                eval_for_loop,
                signal.kind == "pivot",
            ),
            is_transient=is_transient_claude,
        )
        if warning:
            ledger.warnings.append(warning)
        next_contract = deps.run_dir / f"iteration-{next_n}" / "contract.md"
        problems: list[str] = []
        if _supports_hardened_remediation(deps):
            problems = (
                validate_contract(next_contract.read_text())
                if next_contract.exists()
                else ["contract.md missing"]
            )
        if problems:
            warning = await with_transient_retry(
                _make_remediation_call(
                    deps,
                    iteration_n,
                    next_n,
                    eval_for_loop,
                    signal.kind == "pivot",
                ),
                is_transient=is_transient_claude,
            )
            if warning:
                ledger.warnings.append(warning)
            problems = (
                validate_contract(next_contract.read_text())
                if next_contract.exists()
                else ["contract.md missing"]
            )
            if problems:
                ledger.warnings.append(
                    f"Degenerate remediation contract for iteration-{next_n}: {problems}"
                )
                # §H6.3: park the synthesized gap on the ledger so it reaches the
                # NEXT iteration's eval_for_loop (drained at the top of the loop) —
                # appending to eval_for_loop here would be dead, since the fingerprint
                # and remediation for this iteration already consumed it.
                ledger.carried_gaps.append(
                    EvalGap(
                        title="Degenerate remediation contract",
                        severity="high",
                        design_doc_section="§H6",
                        current_state=(
                            f"contract.md for iteration-{next_n} failed validation: {problems}"
                        ),
                        expected_state="a well-formed remediation contract",
                        suggested_fix=(
                            "re-author a contract with a heading and actionable acceptance criteria"
                        ),
                    )
                )
        # §C2.2 — record AFTER any post-validation re-author so the captured
        # session_id reflects the LAST successful remediation attempt, not the
        # first. The runner overwrites last_session_id on each call; reading
        # immediately before the final write captures the most recent id.
        phase_sessions.append(
            {
                "phase": "iter_remediating",
                "sdk": "claude",
                "session_id": _session_id_from(deps.drivers.evaluator),
                "started_at": remediation_started,
                "completed_at": _utcnow_iso(),
            }
        )
        write_sessions_json(
            iteration_dir / "sessions.json", iteration=iteration_n, entries=list(phase_sessions)
        )
        await deps.emitter.emit_iteration(iteration_n, "sessions.json")  # §S5.2
        lifecycle.poll_task_cancellation(task)
    return ("incomplete", deps.inputs.max_iterations)


async def run_phases(
    deps: PhaseDeps,
    sm: RunStateMachine,
    ledger: RunLedger,
    base_git: str | None,
    *,
    task: ServerTaskContext | None = None,
) -> tuple[str, int]:
    """Run plan phase followed by the iteration loop (§C1.6).

    Design: §8 keeps engine thin by delegating normal phase order to this
        helper while lifecycle owns terminal exceptional paths; §C1.6 threads
        task cancellation polling into both sub-phases.
    Implementation: call run_plan_phase then run_iteration_loop with the same
        dependencies, ledger, and task value.
    Example: status, n = await run_phases(deps, sm, ledger, base_git, task=None).
    """
    await run_plan_phase(deps, sm, ledger, task=task)
    return await run_iteration_loop(deps, sm, ledger, base_git, task=task)
