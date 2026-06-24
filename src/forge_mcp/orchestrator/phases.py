"""Per-plan iteration loop driving the §3.2 phase order and §6.5 gate.

run_plan_loop runs ONE plan's iteration loop, isolated in its sandbox, against
the wave's frozen spec.md. It is consumed by the scheduler (Task 27) and the
engine (Task 28). The loop is wrapped in failure isolation (I8) so an unhandled
error in one plan never aborts a sibling plan's loop.
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
from forge_mcp.gitguard import capture_state, diff_state
from forge_mcp.models import EvalGap, GapSummary, GapTriage, Plan
from forge_mcp.orchestrator.plan_state import PlanState
from forge_mcp.sandbox import Change, capture_manifest, detect_changes
from forge_mcp.state import light_replace, write_json
from forge_mcp.triage import effective_code_bug_titles, passes_citation_gate
from forge_mcp.verifier import VerifyOutcome, run_verification

if TYPE_CHECKING:
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner

# Sentinel design_doc_section values for non-demotable synthesized gaps (§9/§6.5).
_GIT_SENTINEL_SECTION = "§9"
_VERIFY_SENTINEL_SECTION = "§6.5"


@dataclass
class PlanLoopResult:
    """Terminal report from one plan's §3.2 iteration loop.

    Design: §6.4/§6.5 the loop reports a single terminal state plus the freshest
        full post-synthesize gap set so the orchestrator can project honest
        unresolved gaps, apply any merge (change_set), or stop the wave for a
        validated design-fault amendment.
    Implementation: plain dataclass; proposed_amendment carries (plan_id, row)
        only for awaiting_amendment; change_set is populated only for done.
    Example: PlanLoopResult('done', 1, [], [], None, None, [Change(...)]).
    """

    terminal_state: Literal["done", "incomplete", "failed", "awaiting_amendment"]
    iterations: int
    last_gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    proposed_amendment: tuple[str, GapTriage] | None = None
    stop_reason: str | None = None
    change_set: list[Change] | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §3.3 PlanState mutations record a last_updated_at timestamp; the loop
        is the sole writer of per-plan state so it supplies the clock.
    Implementation: datetime.now in UTC, serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


def _seed_contract(plan: Plan, layout: RunLayout, n: int) -> str:
    """Write iteration 1's contract.md from the plan body and return its text.

    Design: §3.2 the first iteration's contract is the plan body itself; later
        iterations use a remediation contract written at the end of the prior
        iteration (see _write_remediation_contract).
    Implementation: ensure the iteration dir, write plan.body to contract.md via
        light_replace (durability not required for derived artifacts), return it.
    Example: _seed_contract(plan, layout, 1) writes plan.body to iteration-1/contract.md.
    """
    ensure_iteration_dir(layout, plan.id, n)
    light_replace(layout.contract(plan.id, n), plan.body)
    return plan.body


def _write_remediation_contract(
    plan: Plan,
    layout: RunLayout,
    n: int,
    *,
    gaps: list[EvalGap],
    synthesized: list[GapSummary],
    nudge: bool,
) -> str:
    """Write the next iteration's remediation contract incorporating open gaps.

    Design: §3.2 a non-completing iteration produces a remediation contract that
        instructs the next Generator turn to close the still-open gaps; synthesized
        git/verify blockers are surfaced too (a plan whose only open issue is a
        failing verification would otherwise see no signal); a NUDGE signal appends
        an anti-oscillation note so the agent varies its approach.
    Implementation: render each eval gap as a title/severity/fix bullet, prefix the
        plan body for context, append a brief plain-text note per synthesized
        blocker (GapSummary carries only the title), append the nudge note when
        nudge is True, then write to iteration-(n)/contract.md and return the text.
    Example: _write_remediation_contract(plan, layout, 2, gaps=[g], synthesized=[],
        nudge=False) writes a contract listing g and returns it.
    """
    lines = [plan.body, "", "## Remaining gaps to close"]
    for g in gaps:
        lines.append(f"- {g.title} ({g.severity}): {g.suggested_fix}")
    for s in synthesized:
        lines.append(f"- Also: {s.title} — resolve this blocker.")
    if nudge:
        lines.append("")
        lines.append(
            "## Note: prior iterations did not make progress on these gaps — "
            "try a different approach."
        )
    text = "\n".join(lines)
    ensure_iteration_dir(layout, plan.id, n)
    light_replace(layout.contract(plan.id, n), text)
    return text


def _synthesize_blocking_gaps(
    *,
    git_diff: str,
    last_verification: VerifyOutcome | None,
    verification_command: str | None,
) -> list[GapSummary]:
    """Build the non-demotable git and verification gaps for this iteration (§6.5/§9).

    Design: §6.5 a verification failure and §9 a git-state mutation are both
        hard, non-demotable blockers; they are synthesized as high-severity gaps
        BEFORE the gap fingerprint so they enter the convergence signal and block
        completion exactly like an unresolved code bug.
    Implementation: append a §9 gap when git_diff is non-empty; append a §6.5 gap
        when a verification command exists and the cached outcome did not pass.
    Example: _synthesize_blocking_gaps(git_diff='', last_verification=failed,
        verification_command='false') returns one §6.5 verification gap.
    """
    synthesized: list[GapSummary] = []
    if git_diff:
        synthesized.append(
            GapSummary(
                title="git state mutated during iteration",
                severity="high",
                design_doc_section=_GIT_SENTINEL_SECTION,
            )
        )
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

    Design: §3.2 step 9 / §5.3 a triage row stops the wave only when it proposes
        a concrete amendment AND passes the citation gate against the frozen spec;
        such a row needs human-or-orchestrator amendment before the plan can make
        further progress.
    Implementation: scan triages for the first row whose proposed_amendment is set
        and that passes_citation_gate against spec_text.
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
    sandbox: Path,
    spec_text: str,
    claude_runner: ClaudeRunner,
    codex_runner: CodexRunner,
    schemas: dict,
    max_iterations: int,
    base_git_state: str | None,
    git_surface: Path | None = None,
) -> PlanLoopResult:
    """Run one plan's §3.2 iteration loop and return its §6.5 terminal report.

    Design: §3.2 drives the phase order per iteration (generating → verifying →
        evaluating → triaging → synthesize → fingerprint → done → amendment? →
        completion? → non-progress? → cap? → remediating); §6.5 the four-conjunct
        completion gate (no remaining code bugs incl. synthesized, verify passed,
        no git violation, no pending amendment) decides `done`. Synthesize runs
        BEFORE the fingerprint so git/verify blockers feed convergence and block
        completion. I8 wraps the whole loop so an unhandled error returns `failed`
        rather than propagating into a sibling plan's loop.
    Implementation: capture a baseline manifest for the change_set; per iteration
        run the generator, optional verification, git diff, evaluator, optional
        triage; synthesize blocking gaps; write gap_fingerprint.json over the full
        post-synthesize set and append to the per-plan history; record completion;
        return on validated amendment, completion, EARLY_STOP non-progress, or the
        iteration cap, else write a (possibly nudged) remediation contract and
        continue. On done, return detect_changes(sandbox, baseline) as change_set.
        The §9 git backstop snapshots git_surface (the real repo, e.g. target_dir)
        at each iteration end and diffs it against base_git_state — both captured
        from the SAME surface; when git_surface is None both ends are None (empty
        diff, no false violation).
    Example: a clean plan with no verify command and no eval gaps returns
        PlanLoopResult(terminal_state='done', iterations=1, ...).
    """
    plan_state = PlanState(layout, plan_id=plan.id, sandbox_path=str(sandbox))
    try:
        baseline = capture_manifest(sandbox)
        history: list[frozenset[str]] = []
        last_gaps: list[EvalGap] = []
        last_synthesized: list[GapSummary] = []
        nudge_next = False

        for n in range(1, max_iterations + 1):
            plan_state.bump_iteration(_now())
            ensure_iteration_dir(layout, plan.id, n)

            # 1. generating
            plan_state.set_state("generating", now=_now())
            contract_text = (
                _seed_contract(plan, layout, n)
                if n == 1
                else _write_remediation_contract(
                    plan,
                    layout,
                    n,
                    gaps=last_gaps,
                    synthesized=last_synthesized,
                    nudge=nudge_next,
                )
            )
            nudge_next = False
            await run_generator(
                codex_runner,
                contract_text=contract_text,
                sandbox=sandbox,
                surface=plan.surface,
                run_log_path=layout.run_log,
            )
            light_replace(
                layout.summary(plan.id, n), f"Iteration {n} generated for plan {plan.id}."
            )

            # 2. verifying (only when a command is declared)
            last_verification: VerifyOutcome | None = None
            if plan.verification_command is not None:
                plan_state.set_state("verifying", now=_now())
                last_verification = run_verification(plan.verification_command, sandbox)
                light_replace(layout.verify_txt(plan.id, n), last_verification.output)

            # 3. git backstop — diff the SAME real git surface (base vs end).
            end_git_state = capture_state(git_surface) if git_surface is not None else None
            git_diff = diff_state(base_git_state, end_git_state)
            if git_diff:
                light_replace(layout.git_violation(plan.id, n), git_diff)

            # 4. evaluating
            plan_state.set_state("evaluating", now=_now())
            eval_result = await run_evaluator(
                claude_runner,
                spec_text=spec_text,
                sandbox=sandbox,
                eval_schema=schemas["eval"],
                cwd=sandbox,
                run_log_path=layout.run_log,
            )
            write_json(layout.eval(plan.id, n), eval_result, durable=False)

            # 5. triaging (only when the eval found gaps)
            triages: list[GapTriage] = []
            triage_ran = False
            if eval_result.gaps:
                plan_state.set_state("triaging", now=_now())
                triage_result = await run_triage(
                    claude_runner,
                    spec_text=spec_text,
                    eval_result=eval_result,
                    triage_schema=schemas["triage"],
                    cwd=sandbox,
                    run_log_path=layout.run_log,
                )
                write_json(layout.triage(plan.id, n), triage_result, durable=False)
                triages = triage_result.triages
                triage_ran = True

            # 6. synthesize (BEFORE fingerprint): non-demotable git + verify gaps.
            synthesized = _synthesize_blocking_gaps(
                git_diff=git_diff,
                last_verification=last_verification,
                verification_command=plan.verification_command,
            )
            last_gaps = eval_result.gaps
            last_synthesized = synthesized

            # 7. fingerprint over the FULL post-synthesize set (eval ∪ synthesized).
            fp_items = sorted(
                [f"{g.title}|{g.severity}" for g in eval_result.gaps]
                + [f"{g.title}|{g.severity}" for g in synthesized]
            )
            write_json(layout.gap_fingerprint(plan.id, n), fp_items, durable=False)
            history.append(fingerprint(fp_items))

            # 8. done(record): mark this iteration completed.
            plan_state.record_completed(n, _now())

            # 9. amendment-needed? a validated design-fault row STOPS the wave.
            amendment_row = _validated_amendment_row(triages, spec_text)
            if amendment_row is not None:
                plan_state.set_state("awaiting_amendment", now=_now())
                return PlanLoopResult(
                    terminal_state="awaiting_amendment",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    proposed_amendment=(plan.id, amendment_row),
                )

            # 10. completion? §6.5 four-conjunct gate.
            code_bug_titles = effective_code_bug_titles(eval_result.gaps, triages, spec_text)
            # Synthesized gaps are non-demotable code-bugs: the set of remaining
            # code bugs is empty iff there are no eval code-bugs AND none synthesized.
            no_remaining_code_bugs = not code_bug_titles and not synthesized
            effective_no_gaps = no_remaining_code_bugs and (eval_result.no_gaps or triage_ran)
            verify_passed = plan.verification_command is None or (
                last_verification is not None and last_verification.passed
            )
            no_git_violation = not git_diff
            no_pending_amend = True  # handled in step 9

            if effective_no_gaps and verify_passed and no_git_violation and no_pending_amend:
                plan_state.set_state("done", now=_now())
                return PlanLoopResult(
                    terminal_state="done",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    change_set=detect_changes(sandbox, baseline),
                )

            # 11. non-progress?
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

            # 12. cap?
            if n == max_iterations:
                plan_state.set_state("incomplete", now=_now())
                return PlanLoopResult(
                    terminal_state="incomplete",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    stop_reason="iteration cap",
                )

            # 13. remediating — fall through to the next iteration (contract
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
    except Exception as exc:  # noqa: BLE001 — I8: isolate per-plan failure.
        try:
            plan_state.set_state("failed", now=_now())
        except Exception:  # noqa: BLE001 — best-effort checkpoint on failure path.
            pass
        return PlanLoopResult(
            terminal_state="failed",
            iterations=0,
            stop_reason=f"plan loop failed: {type(exc).__name__}: {exc}",
        )
