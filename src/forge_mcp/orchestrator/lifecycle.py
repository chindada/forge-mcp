"""Terminal honesty and result projection for forge-mcp orchestrator.

Pure projection functions — no I/O. Transforms the single plan's terminal
report into a RunResult with honest unresolved_gaps (§8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_mcp.models import EvalGap, GapSummary, RunResult

# Sentinel design_doc_section used for synthesized failure gaps (§8).
_FAILURE_SENTINEL_SECTION = "§8-failure"


@dataclass
class PlanReport:
    """Carries the terminal state and freshest gap set for the plan when it did not complete.

    Design: §8 a non-completed run contributes the plan's freshest full
        post-synthesize gap set (eval gaps ∪ synthesized verify gap) to the
        RunResult; plan_id is retained as a stable label for the synthesized
        failure gap even though there is only one plan.
    Implementation: plain dataclass; no I/O; consumed by project_unresolved_gaps.
    Example: PlanReport('plan', 'incomplete', [EvalGap(...)], [], None).
    """

    plan_id: str
    terminal_state: str
    gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    failure_reason: str | None = None


def synthesized_gap_for_failed_plan(plan_id: str, reason: str) -> GapSummary:
    """Build a synthesized failure GapSummary for a failed plan with no gap set.

    Design: §8 a failed plan that has no eval gaps or synthesized gaps must still
        contribute a visible gap so the caller is never silently absent of
        failure information.
    Implementation: title names the plan_id and includes the reason; severity is
        hard-coded 'high'; design_doc_section is a fixed sentinel so the
        non-optional field is always populated.
    Example: synthesized_gap_for_failed_plan('plan', 'crashed: OSError') returns
        a GapSummary whose title contains 'plan' and design_doc_section is set.
    """
    return GapSummary(
        title=f"Plan {plan_id} failed: {reason}",
        severity="high",
        design_doc_section=_FAILURE_SENTINEL_SECTION,
    )


def _eval_gap_to_summary(gap: EvalGap) -> GapSummary:
    """Project an EvalGap to a GapSummary by copying the shared fields.

    Design: §8 EvalGap carries implementation detail fields not needed in
        RunResult; only title, severity, and design_doc_section are projected.
    Implementation: construct GapSummary from the three shared fields.
    Example: _eval_gap_to_summary(EvalGap(title='t', ...)) returns
        GapSummary(title='t', ...).
    """
    return GapSummary(
        title=gap.title,
        severity=gap.severity,
        design_doc_section=gap.design_doc_section,
    )


def project_unresolved_gaps(non_completed: list[PlanReport]) -> list[GapSummary]:
    """Project the non-completed plan report(s) into the RunResult gap rows.

    Design: §8 the single-plan run passes at most one PlanReport here; the
        function stays list-shaped so build_run_result has one code path whether
        the plan completed (empty list) or not. Each report contributes its
        freshest full gap set — the per-iteration freshest set, NOT aggregated
        across iterations; gaps are not deduped (the projection is honest about
        every distinct row).
    Implementation: for each PlanReport, project its EvalGaps to GapSummary via
        {title, severity, design_doc_section}, then extend with its synthesized
        GapSummaries; for a 'failed' report with no eval gaps AND no synthesized
        gaps, contribute a synthesized_gap_for_failed_plan.
    Example: one incomplete PlanReport with one eval gap and one synthesized gap
        yields two GapSummary rows.
    """
    result: list[GapSummary] = []
    for report in non_completed:
        eval_summaries = [_eval_gap_to_summary(g) for g in report.gaps]
        plan_gaps = eval_summaries + list(report.synthesized)
        if not plan_gaps and report.terminal_state == "failed":
            reason = report.failure_reason or "unknown reason"
            plan_gaps = [synthesized_gap_for_failed_plan(report.plan_id, reason)]
        result.extend(plan_gaps)
    return result


def build_run_result(
    *,
    status: str,
    run_dir: str,
    iterations: int,
    non_completed: list[PlanReport],
    stop_reason: str | None,
    verified: bool,
    summary: str,
    failure_kind: str | None = None,
) -> RunResult:
    """Assemble a RunResult from orchestrator state and projected gap data.

    Design: §8 the RunResult must be an honest summary of what happened;
        unresolved_gaps are derived from the non-completed plan (empty when the
        plan completed); failure_kind is set ONLY when status='failed' (an
        orchestrator-internal error), never for plan non-convergence.
    Implementation: call project_unresolved_gaps to derive unresolved_gaps; pass
        failure_kind through only when status='failed'; construct RunResult.
    Example: build_run_result(status='incomplete', ...) yields a RunResult whose
        unresolved_gaps come from non_completed and failure_kind=None.
    """
    unresolved_gaps = project_unresolved_gaps(non_completed)
    resolved_failure_kind = failure_kind if status == "failed" else None
    return RunResult(
        status=status,  # type: ignore[arg-type]
        run_dir=run_dir,
        iterations=iterations,
        unresolved_gaps=unresolved_gaps,
        failure_kind=resolved_failure_kind,
        stop_reason=stop_reason,
        verified=verified,
        summary=summary,
    )
