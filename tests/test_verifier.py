"""§H1 verifier: deterministic gate exit/timeout outcomes and bounded tail."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.verifier import (
    _OUTPUT_TAIL_MAX_CHARS,
    VerificationOutcome,
    render,
    run_verification,
)


def test_exit_zero_passes(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    outcome = run_verification(target_dir, "exit 0", timeout_seconds=30)
    assert outcome.passed is True
    assert outcome.exit_code == 0
    assert outcome.timed_out is False


def test_nonzero_exit_does_not_pass(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    outcome = run_verification(target_dir, "echo boom >&2; exit 3", timeout_seconds=30)
    assert outcome.passed is False
    assert outcome.exit_code == 3
    assert outcome.timed_out is False
    assert "boom" in outcome.output_tail


def test_timeout_sets_timed_out_and_no_exit_code(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    outcome = run_verification(target_dir, "sleep 5", timeout_seconds=1)
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert outcome.passed is False


def test_output_tail_is_bounded(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    outcome = run_verification(
        target_dir, "for i in $(seq 1 100000); do echo line$i; done", timeout_seconds=60
    )
    assert len(outcome.output_tail) <= _OUTPUT_TAIL_MAX_CHARS


def test_render_includes_header_fields() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    text = render(VerificationOutcome("pytest", 0, True, False, "138 passed"))
    assert "command: pytest" in text
    assert "exit_code: 0" in text
    assert "passed: True" in text
    assert "138 passed" in text
