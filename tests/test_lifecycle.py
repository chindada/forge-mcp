from __future__ import annotations

from forge_mcp.models import EvalGap, GapSummary
from forge_mcp.orchestrator.lifecycle import (
    PlanReport,
    build_run_result,
    project_unresolved_gaps,
    synthesized_gap_for_failed_plan,
)


def _gap(t):
    """Build an EvalGap fixture with the given title.

    Design: test helper to reduce boilerplate in lifecycle test cases.
    Implementation: construct EvalGap with fixed fields except the title.
    Example: _gap('g1') yields an EvalGap with title='g1'.
    """
    return EvalGap(
        title=t,
        severity="high",
        design_doc_section="§7.4",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )


def test_failed_plan_gets_synthesized_gap():
    """Design: §6.4 a failed plan with no gaps contributes a synthesized failure gap.
    Implementation: build the synthesized GapSummary.
    Example: title names the plan; section is a sentinel.
    """
    g = synthesized_gap_for_failed_plan("p2", "crashed: OSError")
    assert "p2" in g.title and g.design_doc_section


def test_unresolved_gaps_are_per_plan_freshest_not_collapsed():
    """Design: §4.3/I7 union over non-completed plans, per-plan freshest, not collapsed.
    Implementation: two plans each with a distinct gap.
    Example: two GapSummary rows.
    """
    reports = [
        PlanReport("p1", "incomplete", [_gap("g1")], [], None),
        PlanReport(
            "p2",
            "incomplete",
            [_gap("g2")],
            [GapSummary(title="verify failed", severity="high", design_doc_section="§6.5")],
            None,
        ),
    ]
    rows = project_unresolved_gaps(reports)
    titles = {r.title for r in rows}
    assert {"g1", "g2", "verify failed"} <= titles


def test_build_run_result_incomplete():
    """Design: §4.3 a non-convergent run is 'incomplete' with stop_reason + gaps.
    Implementation: build and read anchors.
    Example: status incomplete, verified False.
    """
    r = build_run_result(
        status="incomplete",
        run_dir="/r",
        iterations=5,
        non_completed=[PlanReport("p1", "incomplete", [_gap("g1")], [], None)],
        stop_reason="non-progress",
        verified=False,
        summary="1/2",
    )
    assert r.status == "incomplete" and r.stop_reason == "non-progress"
    assert r.unresolved_gaps[0].title == "g1"
