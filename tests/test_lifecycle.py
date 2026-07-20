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
    """Design: §8 a failed plan with no gaps contributes a synthesized failure gap.
    Implementation: build the synthesized GapSummary.
    Example: title names the plan; section is a sentinel.
    """
    g = synthesized_gap_for_failed_plan("plan", "crashed: OSError")
    assert "plan" in g.title and g.design_doc_section == "§8-failure"


def test_single_plan_projection_keeps_freshest_gaps():
    """Design: §8 the one non-completed plan contributes its freshest full gap set.
    Implementation: one PlanReport with an eval gap plus a synthesized verify gap.
    Example: both 'g1' and 'verify failed' appear as GapSummary rows.
    """
    reports = [
        PlanReport(
            "plan",
            "incomplete",
            [_gap("g1")],
            [GapSummary(title="verify failed", severity="high", design_doc_section="§6.5")],
            None,
        ),
    ]
    rows = project_unresolved_gaps(reports)
    titles = {r.title for r in rows}
    assert len(rows) == 2 and {"g1", "verify failed"} == titles


def test_completed_run_has_no_unresolved_gaps():
    """Design: §8 a completed single-plan run has an empty non_completed list.
    Implementation: build_run_result with non_completed=[] and verified True.
    Example: status 'completed', verified True, no unresolved gaps.
    """
    r = build_run_result(
        status="completed",
        run_dir="/r",
        iterations=1,
        non_completed=[],
        stop_reason=None,
        verified=True,
        summary="done",
    )
    assert r.status == "completed" and r.verified is True
    assert r.unresolved_gaps == [] and r.failure_kind is None


def test_build_run_result_incomplete():
    """Design: §8 a non-convergent run is 'incomplete' with stop_reason + gaps.
    Implementation: build and read anchors for the single non-completed plan.
    Example: status incomplete, verified False, gap 'g1' present.
    """
    r = build_run_result(
        status="incomplete",
        run_dir="/r",
        iterations=5,
        non_completed=[PlanReport("plan", "incomplete", [_gap("g1")], [], None)],
        stop_reason="non-progress",
        verified=False,
        summary="incomplete",
    )
    assert r.status == "incomplete" and r.stop_reason == "non-progress"
    assert r.unresolved_gaps[0].title == "g1"


def test_failure_kind_only_on_failed_status():
    """Design: §8 failure_kind is set ONLY when status='failed' (orchestrator-internal error).
    Implementation: pass failure_kind for both incomplete and failed and compare.
    Example: incomplete drops failure_kind; failed keeps it.
    """
    inc = build_run_result(
        status="incomplete",
        run_dir="/r",
        iterations=1,
        non_completed=[PlanReport("plan", "incomplete", [], [], None)],
        stop_reason="cap",
        verified=False,
        summary="x",
        failure_kind="should-be-dropped",
    )
    assert inc.failure_kind is None
    failed = build_run_result(
        status="failed",
        run_dir="/r",
        iterations=0,
        non_completed=[],
        stop_reason="boom",
        verified=False,
        summary="x",
        failure_kind="internal",
    )
    assert failed.failure_kind == "internal"
