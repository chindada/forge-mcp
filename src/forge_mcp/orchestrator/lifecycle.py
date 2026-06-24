"""Terminal honesty and result projection for forge-mcp orchestrator.

Pure projection functions — no I/O. Transforms per-plan terminal reports
into a RunResult with honest unresolved_gaps (§6.4/§4.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_mcp.models import EvalGap, GapSummary, RunResult

# Sentinel design_doc_section used for synthesized failure gaps (§6.4).
_FAILURE_SENTINEL_SECTION = "§6.4-failure"


@dataclass
class PlanReport:
    """Carries the terminal state and freshest gap set for one non-completed plan.

    Design: §6.4 each non-completed plan contributes its per-plan freshest
        full post-synthesize gap set (eval gaps ∪ synthesized git/verify gaps)
        to the RunResult.
    Implementation: plain dataclass; no I/O; consumed by project_unresolved_gaps.
    Example: PlanReport('p1', 'incomplete', [EvalGap(...)], [], None).
    """

    plan_id: str
    terminal_state: str
    gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    failure_reason: str | None = None


def synthesized_gap_for_failed_plan(plan_id: str, reason: str) -> GapSummary:
    """Build a synthesized failure GapSummary for a failed plan with no gap set.

    Design: §6.4 a failed plan that has no eval gaps or synthesized gaps must
        still contribute a visible gap so the caller is never silently absent
        of failure information.
    Implementation: title names the plan_id and includes the per-plan reason;
        severity is hard-coded 'high'; design_doc_section is a fixed sentinel
        so the non-optional field is always populated.
    Example: synthesized_gap_for_failed_plan('p2', 'crashed: OSError') returns
        a GapSummary whose title contains 'p2' and design_doc_section is set.
    """
    return GapSummary(
        title=f"Plan {plan_id} failed: {reason}",
        severity="high",
        design_doc_section=_FAILURE_SENTINEL_SECTION,
    )


def _eval_gap_to_summary(gap: EvalGap) -> GapSummary:
    """Project an EvalGap to a GapSummary by copying the shared fields.

    Design: §4.3 EvalGap carries implementation detail fields not needed in
        RunResult; only title, severity, and design_doc_section are projected.
    Implementation: construct GapSummary from the three shared fields.
    Example: _eval_gap_to_summary(EvalGap(title='t', ...)) returns GapSummary(title='t', ...).
    """
    return GapSummary(
        title=gap.title,
        severity=gap.severity,
        design_doc_section=gap.design_doc_section,
    )


def project_unresolved_gaps(non_completed: list[PlanReport]) -> list[GapSummary]:
    """Union over all non-completed plans of each plan's freshest full gap set.

    Design: §4.3/§6.4/I7 per-plan freshest, NOT aggregated across iterations,
        NOT collapsed across plans — two plans with distinct gaps yield distinct
        rows; do not dedupe across plans.
    Implementation: for each PlanReport, project its EvalGaps to GapSummary
        via {title, severity, design_doc_section}, then extend with its
        synthesized GapSummaries; for a 'failed' plan with no eval gaps AND
        no synthesized gaps, contribute a synthesized_gap_for_failed_plan.
    Example: two PlanReports each with one distinct gap → two GapSummary rows.
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

    Design: §4.3/§6.4 the RunResult must be an honest summary of what happened;
        unresolved_gaps are derived from non-completed plans; failure_kind is
        set ONLY when status='failed' (orchestrator-internal error), never for
        per-plan failure or non-convergence.
    Implementation: call project_unresolved_gaps to derive unresolved_gaps;
        pass failure_kind through only when status='failed'; construct RunResult.
    Example: build_run_result(status='incomplete', ...) yields RunResult with
        unresolved_gaps populated from non_completed plans and failure_kind=None.
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
