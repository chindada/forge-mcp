"""§11.2 caps and overflow."""

from __future__ import annotations

from forge_mcp.models import EvalGap
from forge_mcp.orchestrator.caps import (
    GAP_LIST_CAP,
    WARNINGS_CAP,
    build_gap_overflow,
    split_warnings,
)


def _gaps(n: int) -> list[EvalGap]:
    """Build n simple EvalGap objects.

    Design: cap tests need deterministic titled gaps.
    Implementation: construct minimal valid EvalGap instances.
    Example: _gaps(2)[0].title == 'g0'.
    """
    return [
        EvalGap(
            title=f"g{i}",
            severity="low",
            design_doc_section="§x",
            current_state="c",
            expected_state="e",
            suggested_fix="f",
        )
        for i in range(n)
    ]


def test_split_warnings_keeps_first_cap_drops_rest() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    w = [f"w{i}" for i in range(WARNINGS_CAP + 3)]
    kept, dropped = split_warnings(w)
    assert kept == w[:WARNINGS_CAP]
    assert dropped == w[WARNINGS_CAP:]


def test_overflow_none_when_under_cap() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert build_gap_overflow(_gaps(GAP_LIST_CAP), kind="unresolved", total=GAP_LIST_CAP) is None


def test_overflow_contains_only_tail_with_total_header() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    n = GAP_LIST_CAP + 4
    content = build_gap_overflow(_gaps(n), kind="unresolved", total=n)
    assert content is not None
    assert f"truncated from {n} total" in content
    for i in range(GAP_LIST_CAP, n):
        assert f"g{i}" in content
