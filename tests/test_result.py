"""§11.5 RunResult artifact construction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from forge_mcp.models import RunForgeInput
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.orchestrator.result import _artifact_index, build_result
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import RunState


def test_result_numeric_iteration_sort_and_failed_fields(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = tmp_path / "run"
    (run_dir / "plan").mkdir(parents=True)
    (run_dir / "plan" / "plan.md").write_text("p")
    for n in (10, 9):
        d = run_dir / f"iteration-{n}"
        d.mkdir(parents=True)
        (d / "contract.md").write_text("c")
    (run_dir / "run.log").write_text("secret")
    sm = RunStateMachine(
        run_dir / "state.json",
        RunState(
            state="failed",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=10,
            started_at=datetime.now(UTC),
        ),
    )
    ledger = RunLedger(
        failed_phase="x",
        error_class="RuntimeError",
        error_message="boom",
        traceback_truncated="tb",
        decided_at=datetime.now(UTC),
    )
    result = build_result(
        run_id="abcd1234",
        run_dir=run_dir,
        status="failed",
        inputs=RunForgeInput(target_dir=str(tmp_path), design_doc_content="x"),
        sm=sm,
        ledger=ledger,
        started_at=datetime.now(UTC),
    )
    assert [it.n for it in result.artifacts.iterations] == [9, 10]
    assert result.error_class == "RuntimeError"
    assert "run.log" not in result.model_dump_json()


def test_artifact_index_picks_up_git_violation_txt(tmp_path: Path) -> None:
    """Pin §13 forbidden git mutation artifact extension.

    Design: artifact discovery exposes git-violation.txt, not the legacy
        markdown filename, to match the documented artifact tree.
    Implementation: create one iteration dir with the txt artifact and inspect
        the private artifact-index helper.
    Example: idx.iterations[0].git_violation_path ends with '.txt'.
    """
    iteration_dir = tmp_path / "iteration-1"
    iteration_dir.mkdir()
    (iteration_dir / "contract.md").write_text("c")
    (iteration_dir / "git-violation.txt").write_text("diff")
    idx = _artifact_index(tmp_path, RunLedger())
    assert idx.iterations[0].git_violation_path is not None
    assert idx.iterations[0].git_violation_path.endswith("git-violation.txt")
