"""§H3 convergence: order-independent fingerprints and pivot/break detection."""

from __future__ import annotations

from forge_mcp.models import EvalGap
from forge_mcp.orchestrator.convergence import (
    NON_PROGRESS_WINDOW,
    detect_non_progress,
    fingerprint_gaps,
)


def _gap(title: str, severity: str = "high") -> EvalGap:
    """Build a minimal EvalGap for fingerprint/loop tests.

    Design: convergence identity keys on title|severity, so other fields are
        filler and need only satisfy the model's min_length=1 constraint.
    Implementation: fill every required EvalGap field with a constant string.
    Example: _gap('A', 'low').severity == 'low'.
    """
    return EvalGap(
        title=title,
        severity=severity,
        design_doc_section="§H3",
        current_state="x",
        expected_state="y",
        suggested_fix="z",
    )


def test_fingerprint_is_order_independent() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    a, b = _gap("A"), _gap("B")
    assert fingerprint_gaps([a, b]) == fingerprint_gaps([b, a])


def test_fingerprint_differs_on_changed_gap_set() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert fingerprint_gaps([_gap("A")]) != fingerprint_gaps([_gap("A", "low")])
    assert fingerprint_gaps([]) == frozenset()


def test_detect_none_when_history_too_short() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    fp = fingerprint_gaps([_gap("A")])
    assert detect_non_progress([fp], window=2).kind == "none"


def test_detect_pivot_after_one_stuck_window() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    fp = fingerprint_gaps([_gap("A")])
    assert detect_non_progress([fp, fp], window=2).kind == "pivot"


def test_detect_break_after_two_stuck_windows() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    fp = fingerprint_gaps([_gap("A")])
    sig = detect_non_progress([fp, fp, fp, fp], window=2)
    assert sig.kind == "break"
    assert "unchanged" in sig.reason


def test_detect_resets_to_none_on_progress() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    a = fingerprint_gaps([_gap("A")])
    b = fingerprint_gaps([_gap("B")])
    assert detect_non_progress([a, a, b], window=2).kind == "none"


def test_detect_none_when_latest_empty() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    empty = frozenset()
    assert detect_non_progress([empty, empty, empty, empty], window=2).kind == "none"


def test_window_constant_default_is_two() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert NON_PROGRESS_WINDOW == 2
