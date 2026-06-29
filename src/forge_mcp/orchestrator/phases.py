"""Single-plan iteration loop driving the §4 phase order and §6.5 verify gate.

run_plan_loop runs the one plan's iteration loop directly on target_dir against
the current spec.md, applying validated design-fault amendments in-loop. It is
consumed by the single-plan engine (§8). The loop is wrapped in failure
isolation so an unhandled error returns a `failed` report rather than
propagating into the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from forge_mcp.artifacts import RunLayout, ensure_iteration_dir
from forge_mcp.convergence import detect_non_progress, fingerprint
from forge_mcp.drivers.evaluator import run_evaluator, run_triage
from forge_mcp.drivers.generator import run_generator
from forge_mcp.drivers.remediator import run_remediation
from forge_mcp.models import EvalGap, GapSummary, GapTriage, Plan
from forge_mcp.orchestrator.amend import apply_amendments
from forge_mcp.orchestrator.plan_state import PlanState
from forge_mcp.state import light_replace, write_json
from forge_mcp.triage import effective_code_bug_titles, passes_citation_gate
from forge_mcp.verifier import VerifyOutcome, run_verification

if TYPE_CHECKING:
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner

# Sentinel design_doc_section value for the non-demotable synthesized verify gap (§6.5).
_VERIFY_SENTINEL_SECTION = "§6.5"


@dataclass
class PlanLoopResult:
    """Terminal report from the single plan's §4 iteration loop.

    Design: §4 the loop reports a single terminal state plus the freshest full
        post-synthesize gap set so the engine can project honest unresolved
        gaps; with direct edits there is no change_set and amendments are
        applied in-loop, so the proposed_amendment hand-off field is gone too.
    Implementation: plain dataclass; terminal_state is one of done / incomplete
        / failed (no awaiting_amendment); last_gaps and synthesized carry the
        freshest gap set; stop_reason explains a non-done stop.
    Example: PlanLoopResult(terminal_state='done', iterations=1).
    """

    terminal_state: Literal["done", "incomplete", "failed"]
    iterations: int
    last_gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    stop_reason: str | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §4 PlanState mutations record a last_updated_at timestamp; the loop
        is the sole writer of plan state so it supplies the clock.
    Implementation: datetime.now in UTC, serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


def _seed_contract(plan: Plan, layout: RunLayout, n: int) -> str:
    """Write iteration 1's contract.md from the plan body and return its text.

    Design: §4 the first iteration's contract is the plan body itself; later
        iterations use a remediation contract written from the still-open gaps.
    Implementation: ensure the (flat, run-level) iteration dir, write plan.body
        to contract.md via light_replace (durability not required for derived
        artifacts), return it.
    Example: _seed_contract(plan, layout, 1) writes plan.body to iteration-1/contract.md.
    """
    ensure_iteration_dir(layout, n)
    light_replace(layout.contract(n), plan.body)
    return plan.body


async def _write_remediation_contract(
    plan: Plan,
    layout: RunLayout,
    n: int,
    *,
    claude_runner: ClaudeRunner,
    spec_text: str,
    target_dir: Path,
    gaps: list[EvalGap],
    synthesized: list[GapSummary],
    nudge: bool,
    run_log_path: Path | None = None,
) -> str:
    """Write the next iteration's remediation contract as a Claude-authored plan.

    Design: §4 a non-completing iteration produces a remediation contract that
        instructs the next Generator turn to close the still-open gaps. Claude
        writes a focused, repo-grounded plan from the open gaps (via the
        Remediation stage) rather than re-emitting the whole plan body with a
        gaps footnote — so each iteration's contract is a genuine plan to fix the
        previous gap. The synthesized verify blocker is surfaced too (a plan whose
        only open issue is a failing verification would otherwise see no signal);
        a NUDGE signal asks the agent to vary its approach.
    Implementation: delegate to run_remediation (Claude, git-deny, rooted at
        target_dir, returning the structured `contract` field), then write the
        returned Markdown to iteration-(n)/contract.md and return it.
    Example: await _write_remediation_contract(plan, layout, 2, claude_runner=r,
        spec_text='# spec', target_dir=p, gaps=[g], synthesized=[], nudge=False)
        writes a contract closing g and returns it.
    """
    ensure_iteration_dir(layout, n)
    text = await run_remediation(
        claude_runner,
        spec_text=spec_text,
        plan_body=plan.body,
        gaps=gaps,
        synthesized=synthesized,
        nudge=nudge,
        cwd=target_dir,
        run_log_path=run_log_path,
    )
    light_replace(layout.contract(n), text)
    return text


def _synthesize_blocking_gaps(
    *,
    last_verification: VerifyOutcome | None,
    verification_command: str | None,
) -> list[GapSummary]:
    """Build the non-demotable verification gap for this iteration (§6.5).

    Design: §6.5 a verification failure is a hard, non-demotable blocker; it is
        synthesized as a high-severity gap BEFORE the gap fingerprint so it
        enters the convergence signal and blocks completion exactly like an
        unresolved code bug. The §9 git backstop gap is removed — git state does
        not gate completion under the direct-edit model.
    Implementation: append a §6.5 gap only when a verification command exists and
        the cached outcome did not pass; otherwise return the empty list.
    Example: _synthesize_blocking_gaps(last_verification=failed,
        verification_command='false') returns one §6.5 verification gap.
    """
    synthesized: list[GapSummary] = []
    if (
        verification_command is not None
        and last_verification is not None
        and not last_verification.passed
    ):
        synthesized.append(
            GapSummary(
                title="verification command failed",
                severity="high",
                design_doc_section=_VERIFY_SENTINEL_SECTION,
            )
        )
    return synthesized


def _validated_amendment_row(triages: list[GapTriage], spec_text: str) -> GapTriage | None:
    """Return the first triage row that is a validated design-fault amendment, or None.

    Design: §4 step 8 a triage row is applied in-loop only when it proposes a
        concrete amendment AND passes the citation gate against the current spec;
        such a row durably rewrites spec.md before the next iteration evaluates.
    Implementation: scan triages for the first row whose proposed_amendment is
        set and that passes_citation_gate against spec_text.
    Example: _validated_amendment_row([row], spec) returns row when it is a cited
        design fault carrying a proposed_amendment.
    """
    for row in triages:
        if row.proposed_amendment is not None and passes_citation_gate(row, spec_text):
            return row
    return None


async def run_plan_loop(
    *,
    layout: RunLayout,
    plan: Plan,
    target_dir: Path,
    spec_text: str,
    spec_fingerprint: str,
    claude_runner: ClaudeRunner,
    codex_runner: CodexRunner,
    schemas: dict,
    max_iterations: int,
) -> PlanLoopResult:
    """Iterate one plan directly on target_dir to an honest terminal report (§4).

    Design: §4 drives generate→verify→evaluate→triage→synthesize→converge each
        iteration; a validated design-fault triage is applied to spec.md IN-LOOP
        (spec_text/fingerprint rebound, churn appended to the amendment history)
        and the loop continues; completion is the two-conjunct gate (no remaining
        code bugs incl. the synthesized verify gap, AND verify passed). There is
        no sandbox, no manifest/change_set, and no git backstop — the edits ARE
        the output, left in target_dir. The iteration cap is the hard termination
        bound. An unhandled error returns `failed` rather than propagating.
    Implementation: per iteration write the contract, run_generator on
        target_dir, optional run_verification(target_dir), run_evaluator and
        run_triage with cwd=target_dir against the CURRENT spec_text, synthesize
        the verify gap only, fingerprint over eval ∪ synthesized, record the
        iteration; if a validated amendment exists apply_amendments([row]),
        rebind spec_text/spec_fingerprint, append churn to amendment_history,
        EARLY_STOP→incomplete 'amendment thrash' else honor the cap and continue;
        otherwise test completion / gap-non-progress / cap and either return or
        write a (possibly nudged) remediation contract and continue.
    Example: a clean plan with no verify command and no gaps returns
        PlanLoopResult(terminal_state='done', iterations=1).
    """
    plan_state = PlanState(layout)
    try:
        history: list[frozenset[str]] = []
        amendment_history: list[frozenset[str]] = []
        last_gaps: list[EvalGap] = []
        last_remediation_gaps: list[EvalGap] = []
        last_synthesized: list[GapSummary] = []
        nudge_next = False

        for n in range(1, max_iterations + 1):
            plan_state.bump_iteration(_now())
            ensure_iteration_dir(layout, n)

            # 1. generate — Codex edits target_dir directly.
            plan_state.set_state("generating", now=_now())
            contract_text = (
                _seed_contract(plan, layout, n)
                if n == 1
                else await _write_remediation_contract(
                    plan,
                    layout,
                    n,
                    claude_runner=claude_runner,
                    spec_text=spec_text,
                    target_dir=target_dir,
                    gaps=last_remediation_gaps,
                    synthesized=last_synthesized,
                    nudge=nudge_next,
                    run_log_path=layout.run_log,
                )
            )
            nudge_next = False
            await run_generator(
                codex_runner,
                contract_text=contract_text,
                target_dir=target_dir,
                surface=plan.surface,
                run_log_path=layout.run_log,
            )
            light_replace(layout.summary(n), f"Iteration {n} generated.")

            # 2. verify (only when a command is declared) in target_dir.
            last_verification: VerifyOutcome | None = None
            if plan.verification_command is not None:
                plan_state.set_state("verifying", now=_now())
                last_verification = run_verification(plan.verification_command, target_dir)
                light_replace(layout.verify_txt(n), last_verification.output)

            # 3. evaluate against the CURRENT (possibly amended) spec.
            plan_state.set_state("evaluating", now=_now())
            eval_result = await run_evaluator(
                claude_runner,
                spec_text=spec_text,
                eval_schema=schemas["eval"],
                cwd=target_dir,
                run_log_path=layout.run_log,
            )
            write_json(layout.eval(n), eval_result, durable=False, indent=2)

            # 4. triage (only when the eval found gaps).
            triages: list[GapTriage] = []
            triage_ran = False
            if eval_result.gaps:
                plan_state.set_state("triaging", now=_now())
                triage_result = await run_triage(
                    claude_runner,
                    spec_text=spec_text,
                    eval_result=eval_result,
                    triage_schema=schemas["triage"],
                    cwd=target_dir,
                    run_log_path=layout.run_log,
                )
                write_json(layout.triage(n), triage_result, durable=False, indent=2)
                triages = triage_result.triages
                triage_ran = True

            # 5. synthesize (BEFORE fingerprint): the §6.5 verify gap only.
            synthesized = _synthesize_blocking_gaps(
                last_verification=last_verification,
                verification_command=plan.verification_command,
            )
            last_gaps = eval_result.gaps
            last_synthesized = synthesized
            # The triage-filtered code-bug set drives BOTH the completion gate
            # (step 9) and the next iteration's remediation contract: a validly
            # demoted design fault is the spec's problem, not a code fix, so it is
            # excluded from what the Generator is told to close (the step 8
            # amendment resolves it only when it carries a cited proposed_amendment;
            # otherwise it persists and the run stops honestly via non-progress).
            # last_gaps stays RAW so the engine still gets an honest FULL
            # unresolved-gaps report (PlanLoopResult.last_gaps).
            code_bug_titles = effective_code_bug_titles(eval_result.gaps, triages, spec_text)
            last_remediation_gaps = [g for g in eval_result.gaps if g.title in code_bug_titles]

            # 6. fingerprint over the FULL post-synthesize set (eval ∪ synthesized).
            fp_items = sorted(
                [f"{g.title}|{g.severity}" for g in eval_result.gaps]
                + [f"{g.title}|{g.severity}" for g in synthesized]
            )
            write_json(layout.gap_fingerprint(n), fp_items, durable=False)
            history.append(fingerprint(fp_items))

            # 7. record this iteration completed.
            plan_state.record_completed(n, _now())

            # 8. amendment? (in-loop) — apply a validated design-fault row to spec.md.
            amendment_row = _validated_amendment_row(triages, spec_text)
            if amendment_row is not None:
                outcome = apply_amendments(
                    layout,
                    spec_text=spec_text,
                    spec_fingerprint=spec_fingerprint,
                    proposed=[amendment_row],
                    now=_now(),
                )
                spec_text = outcome.new_spec
                spec_fingerprint = outcome.new_fingerprint
                amendment_history.append(outcome.churn_fingerprint)
                if detect_non_progress(amendment_history) == "EARLY_STOP":
                    plan_state.set_state("incomplete", now=_now())
                    return PlanLoopResult(
                        terminal_state="incomplete",
                        iterations=n,
                        last_gaps=last_gaps,
                        synthesized=last_synthesized,
                        stop_reason="amendment thrash",
                    )
                if n == max_iterations:
                    plan_state.set_state("incomplete", now=_now())
                    return PlanLoopResult(
                        terminal_state="incomplete",
                        iterations=n,
                        last_gaps=last_gaps,
                        synthesized=last_synthesized,
                        stop_reason="iteration cap",
                    )
                # The gap cannot close until the generator builds to the amended
                # spec; carry the open gaps forward and continue.
                plan_state.set_state("remediating", now=_now())
                continue

            # 9. completion? §4 two-conjunct gate (effective_no_gaps ∧ verify_passed).
            # code_bug_titles was computed above (after triage) and is reused here.
            # Synthesized gaps are non-demotable code-bugs: the set of remaining
            # code bugs is empty iff there are no eval code-bugs AND none synthesized.
            no_remaining_code_bugs = not code_bug_titles and not synthesized
            effective_no_gaps = no_remaining_code_bugs and (eval_result.no_gaps or triage_ran)
            verify_passed = plan.verification_command is None or (
                last_verification is not None and last_verification.passed
            )

            if effective_no_gaps and verify_passed:
                plan_state.set_state("done", now=_now())
                return PlanLoopResult(
                    terminal_state="done",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                )

            # 10. non-progress (gap history)?
            signal = detect_non_progress(history)
            if signal == "EARLY_STOP":
                plan_state.set_state("incomplete", now=_now())
                return PlanLoopResult(
                    terminal_state="incomplete",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    stop_reason="non-progress: gap-set stable",
                )
            if signal == "NUDGE":
                nudge_next = True

            # 11. cap?
            if n == max_iterations:
                plan_state.set_state("incomplete", now=_now())
                return PlanLoopResult(
                    terminal_state="incomplete",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    stop_reason="iteration cap",
                )

            # 12. remediate — fall through to the next iteration (contract
            # written at the top of iteration n+1 via _write_remediation_contract).
            plan_state.set_state("remediating", now=_now())

        # Unreachable: the cap check at n == max_iterations always returns.
        return PlanLoopResult(
            terminal_state="incomplete",
            iterations=max_iterations,
            last_gaps=last_gaps,
            synthesized=last_synthesized,
            stop_reason="iteration cap",
        )
    except Exception as exc:  # noqa: BLE001 — isolate the loop failure.
        try:
            plan_state.set_state("failed", now=_now())
        except Exception:  # noqa: BLE001 — best-effort checkpoint on failure path.
            pass
        return PlanLoopResult(
            terminal_state="failed",
            iterations=0,
            stop_reason=f"plan loop failed: {type(exc).__name__}: {exc}",
        )
