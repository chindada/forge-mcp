"""§8.2 last_phase tracks last non-terminal phase only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import RunState


def _initial(p: Path) -> RunStateMachine:
    """Create a RunStateMachine in init state.

    Design: tests need a private state.json writer rooted in tmp dirs.
    Implementation: construct RunState and pass it to RunStateMachine.
    Example: sm = _initial(tmp_path).
    """
    return RunStateMachine(
        p / "state.json",
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(p),
            iteration=0,
            started_at=datetime.now(UTC),
            last_updated_at=datetime.now(UTC),
        ),
    )


def test_last_phase_advances_for_non_terminal(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    sm = _initial(tmp_path)
    sm.transition("planning")
    assert sm.last_phase == "planning"
    sm.transition("iter_generating", iteration=1)
    assert sm.last_phase == "iter_generating"


def test_last_phase_does_not_advance_for_terminal(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    sm = _initial(tmp_path)
    sm.transition("iter_generating", iteration=1)
    sm.transition("failed", reason="boom")
    assert sm.last_phase == "iter_generating"


def test_last_phase_does_not_advance_for_cancelling(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    sm = _initial(tmp_path)
    sm.transition("iter_evaluating", iteration=2)
    sm.transition("cancelling", reason="client", cancelled=True)
    assert sm.last_phase == "iter_evaluating"


def test_iteration_pinned_via_kwargs(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    sm = _initial(tmp_path)
    sm.transition("iter_generating", iteration=3)
    assert sm.iteration == 3
    assert sm.current.iteration == 3
