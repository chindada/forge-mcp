"""§9 phase orchestration helpers over injected dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import atomic_write_text
from ..config import RunConfig
from ..gitguard import capture_state, diff_state
from ..models import EvalGap, EvalResult, RunForgeInput
from ..runcontext import RunContext
from .ledger import RunLedger
from .retry import with_schema_retry
from .statemachine import RunStateMachine
from .triage import classify_gaps


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


async def run_plan_phase(deps: PhaseDeps, sm: RunStateMachine, ledger: RunLedger) -> None:
    """Run the planner phase and record warnings/completion.

    Design: §9.1 — planner predates the loop, so its RunContext carries
        run_dir + Claude config only (no target_dir, no iteration_n).
    Implementation: build the planner ctx with keyword args to avoid the §8.4
        field-order pitfall; append the literal 'plan' to completed_phases for
        §7/§9.1 parity.
    Example: await run_plan_phase(deps, sm, ledger).
    """
    sm.transition("planning")
    await deps.status.update(phase="planning", agent="planner", message="writing plan")
    planner_ctx = RunContext(
        run_dir=deps.run_dir,
        claude_config_dir=deps.config.claude_config_dir,
        claude_cli_path=deps.config.claude_cli_path,
    )
    warning = await deps.drivers.planner.write_plan(planner_ctx)
    if warning:
        ledger.warnings.append(warning)
    ledger.completed_phases.append("plan")  # §7 / §9.1
    sm.transition("planned")


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


def _make_eval_call(deps: PhaseDeps, iteration_n: int) -> Any:
    """Create a schema-retry callback for evaluation.

    Design: §11.6 expects a retry-flag callback without loop late binding.
    Implementation: bind iteration_n in this helper and call evaluator.
    Example: await with_schema_retry(_make_eval_call(deps, 1)).
    """

    async def call(retry: bool) -> EvalResult:
        """Run evaluator.evaluate with a retry flag.

        Design: each retry uses the same iteration context.
        Implementation: build a fresh RunContext and pass retry through.
        Example: await call(False).
        """
        return await deps.drivers.evaluator.evaluate(_ctx(deps, iteration_n), retry=retry)

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
    deps: PhaseDeps, sm: RunStateMachine, ledger: RunLedger, base_git: str | None
) -> tuple[str, int]:
    """Run generator/evaluator/remediation iterations (§9.2).

    Design: preserves load-bearing ordering: triage before git-violation
        synthesis, two-conjunct completion, and iter_remediating transition
        before remediation writing.
    Implementation: for each iteration create contract if needed, run Codex,
        evaluate with schema retry, triage gaps, check git diff, and either
        complete or write next contract.
    Example: status, used = await run_iteration_loop(deps, sm, ledger, base_git).
    """
    design_text = (deps.run_dir / "inputs" / "design.md").read_text()
    current_contract = deps.run_dir / "iteration-1" / "contract.md"
    if not current_contract.exists():
        atomic_write_text(current_contract, (deps.run_dir / "plan" / "plan.md").read_text())
    for iteration_n in range(1, deps.inputs.max_iterations + 1):
        iteration_dir = deps.run_dir / f"iteration-{iteration_n}"
        iteration_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        sm.transition("iter_generating", iteration=iteration_n)
        await deps.status.update(
            phase="iter_generating",
            agent="generator",
            message="implementing",
            iteration=iteration_n,
        )
        await deps.drivers.generator.implement(
            _ctx(deps, iteration_n),
            codex_bin=deps.config.codex_bin,
            status_cb=_make_status_cb(deps, "iter_generating", iteration_n),
        )
        sm.transition("iter_evaluating", iteration=iteration_n)
        er = await with_schema_retry(_make_eval_call(deps, iteration_n))
        triage_ran = False
        eval_for_loop = er
        if er.gaps:
            sm.transition("iter_triaging", iteration=iteration_n)
            triage_result = await with_schema_retry(_make_triage_call(deps, iteration_n, er))
            triage_ran = True
            outcome = classify_gaps(er, triage_result, design_text, iteration_n)
            ledger.design_flaw_gaps.extend(outcome.design_flaws)
            ledger.warnings.extend(outcome.warnings)
            eval_for_loop = EvalResult(
                no_gaps=False, gaps=outcome.code_bug_gaps, summary=er.summary
            )
        git_diff = diff_state(base_git, capture_state(deps.target_dir))
        if git_diff:
            violation_path = iteration_dir / "git-violation.txt"  # §13 artifact tree
            atomic_write_text(violation_path, git_diff)
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
        sm.transition("iter_done", iteration=iteration_n)
        ledger.completed_phases.append(f"iter-{iteration_n}")  # §7 / §9.2 step 6
        effective_no_gaps = (not eval_for_loop.gaps) and (er.no_gaps or triage_ran)
        if effective_no_gaps:
            ledger.decided_at = None
            return ("completed", iteration_n)
        if iteration_n == deps.inputs.max_iterations:
            # §8.3: unresolved_gaps is filled by the engine from the latest
            # eval.json via collect_unresolved_gaps — not by the loop.
            return ("incomplete", iteration_n)
        sm.transition("iter_remediating", iteration=iteration_n)
        warning = await deps.drivers.evaluator.write_remediation(
            _ctx(deps, iteration_n),
            next_iteration_n=iteration_n + 1,
            eval_result=eval_for_loop,  # §9.2 step 8: triage-filtered subset
        )
        if warning:
            ledger.warnings.append(warning)
    return ("incomplete", deps.inputs.max_iterations)


async def run_phases(
    deps: PhaseDeps, sm: RunStateMachine, ledger: RunLedger, base_git: str | None
) -> tuple[str, int]:
    """Run plan phase followed by the iteration loop.

    Design: §8 keeps engine thin by delegating normal phase order to this
        helper while lifecycle owns terminal exceptional paths.
    Implementation: call run_plan_phase then run_iteration_loop with the same
        dependencies and ledger.
    Example: status, n = await run_phases(deps, sm, ledger, base_git).
    """
    await run_plan_phase(deps, sm, ledger)
    return await run_iteration_loop(deps, sm, ledger, base_git)
