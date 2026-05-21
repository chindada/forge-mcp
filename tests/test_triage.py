"""§11.1 strict verbatim citations and coercion drift surfacing."""

from __future__ import annotations

from forge_mcp.models import EvalGap, EvalResult, GapTriage, TriageResult
from forge_mcp.orchestrator.triage import classify_gaps

CANON = (
    "The evaluator must cite design sections by quoting at least twenty "
    "characters of verbatim text from the design document."
)


def _gap(title: str) -> EvalGap:
    """Build one high-severity test gap.

    Design: triage tests focus on title/citation behavior only.
    Implementation: fill required EvalGap fields with compact strings.
    Example: _gap('g1').title == 'g1'.
    """
    return EvalGap(
        title=title,
        severity="high",
        design_doc_section="§x",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )


def _tri(title: str, *, design_fault: bool, cited: list[str], kind: str | None = None) -> GapTriage:
    """Build one triage row.

    Design: tests need concise construction of design-fault and code-bug rows.
    Implementation: pass fields through to GapTriage with a fixed explanation.
    Example: _tri('g1', design_fault=False, cited=[]).
    """
    return GapTriage(
        gap_title=title,
        design_fault=design_fault,
        fault_kind=kind if design_fault else None,  # type: ignore[arg-type]
        cited_sections=cited,
        explanation="x",
    )


def test_short_citation_demotes_to_code_bug() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    out = classify_gaps(
        EvalResult(no_gaps=False, gaps=[_gap("g1")], summary="s"),
        TriageResult(
            triages=[_tri("g1", design_fault=True, cited=["too short"], kind="ambiguity")],
            summary="s",
        ),
        CANON,
        1,
    )
    assert len(out.design_flaws) == 0
    assert len(out.code_bug_gaps) == 1


def test_valid_citation_promotes_to_design_flaw() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    out = classify_gaps(
        EvalResult(no_gaps=False, gaps=[_gap("g1")], summary="s"),
        TriageResult(
            triages=[
                _tri(
                    "g1",
                    design_fault=True,
                    cited=["The evaluator must cite design sections by quoting"],
                    kind="ambiguity",
                )
            ],
            summary="s",
        ),
        CANON,
        1,
    )
    assert len(out.design_flaws) == 1
    assert out.design_flaws[0].iteration_n == 1


def test_title_collision_demotes_all_collided() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    out = classify_gaps(
        EvalResult(no_gaps=False, gaps=[_gap("dup"), _gap("dup")], summary="s"),
        TriageResult(
            triages=[
                _tri(
                    "dup",
                    design_fault=True,
                    cited=["The evaluator must cite design sections by quoting"],
                    kind="ambiguity",
                )
            ],
            summary="s",
        ),
        CANON,
        1,
    )
    assert len(out.design_flaws) == 0
    assert len(out.code_bug_gaps) == 2
    assert any("collision" in w for w in out.warnings)


def test_coercion_drift_surfaced_once() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    er = EvalResult(no_gaps=False, gaps=[_gap("g1")], summary="s")
    row = GapTriage(
        gap_title="g1",
        design_fault=False,
        fault_kind="ambiguity",
        cited_sections=[],
        explanation="x",
    )
    out = classify_gaps(er, TriageResult(triages=[row], summary="s"), CANON, 1)
    assert sum("coerced" in w for w in out.warnings) == 1
