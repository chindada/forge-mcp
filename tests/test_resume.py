"""§H2 resume: locate newest non-terminal run and archive the partial iteration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from forge_mcp.orchestrator.resume import (
    ResumePoint,
    find_resumable_run,
    prepare_resume,
)
from forge_mcp.state import RunState, StateLiteral, write_state


def _write_run(
    harness: Path,
    run_id: str,
    state: StateLiteral,
    *,
    last_completed: int,
    started: datetime,
    cancelled: bool = False,
) -> Path:
    """Write a minimal run dir with a state.json for resume tests.

    Design: find_resumable_run scans <harness>/<run-id>/state.json, so tests
        synthesize that exact layout.
    Implementation: mkdir the run dir and persist a RunState via write_state.
    Example: _write_run(h, 'aaaaaaaa', 'iter_done', last_completed=2, started=now).
    """
    run_dir = harness / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_state(
        run_dir / "state.json",
        RunState(
            state=state,
            run_id=run_id,
            target_dir=str(harness.parent),
            iteration=last_completed,
            started_at=started,
            last_completed_iteration=last_completed,
            cancelled=cancelled,
        ),
    )
    return run_dir


def test_find_picks_newest_non_terminal(harness_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    now = datetime.now(UTC)
    _write_run(
        harness_dir, "aaaaaaaa", "completed", last_completed=5, started=now - timedelta(hours=2)
    )
    _write_run(
        harness_dir, "bbbbbbbb", "iter_done", last_completed=2, started=now - timedelta(hours=1)
    )
    _write_run(harness_dir, "cccccccc", "iter_generating", last_completed=4, started=now)
    point = find_resumable_run(harness_dir)
    assert point is not None
    assert point.run_id == "cccccccc"
    assert point.last_completed_iteration == 4
    assert point.start_iteration == 5


def test_find_returns_none_when_all_terminal(harness_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    now = datetime.now(UTC)
    _write_run(harness_dir, "aaaaaaaa", "completed", last_completed=5, started=now)
    _write_run(harness_dir, "bbbbbbbb", "incomplete", last_completed=2, started=now)
    assert find_resumable_run(harness_dir) is None


def test_find_resumable_run_skips_cancelled_state(harness_dir: Path) -> None:
    """§C-Inv 1 — cancelled runs are never offered for resume.

    Design: client cancellation is terminal-forensic even while state is cancelling.
    Implementation: write a non-terminal state with cancelled=True and assert skip.
    Example: find_resumable_run returns None for cancelling/cancelled=True.
    """
    now = datetime.now(UTC)
    _write_run(
        harness_dir,
        "aaaaaaaa",
        "cancelling",
        last_completed=2,
        started=now,
        cancelled=True,
    )
    assert find_resumable_run(harness_dir) is None


def test_find_resumable_run_keeps_uncancelled_crash_recovery(harness_dir: Path) -> None:
    """§H2 — cancelled guard does not block normal crash recovery.

    Design: resume still recovers non-terminal anchored crashes when cancelled=False.
    Implementation: write iter_generating with a durable anchor and assert it resumes.
    Example: start_iteration is last_completed_iteration + 1.
    """
    now = datetime.now(UTC)
    _write_run(
        harness_dir,
        "bbbbbbbb",
        "iter_generating",
        last_completed=2,
        started=now,
        cancelled=False,
    )
    point = find_resumable_run(harness_dir)
    assert point is not None
    assert point.run_id == "bbbbbbbb"
    assert point.start_iteration == 3


def test_find_resumable_run_skips_non_run_id_dir(tmp_path: Path) -> None:
    """§H2 / finding 7 — resume ignores a state.json in a non-run-id dir.

    Design: the */state.json glob must shape-check parent.name so a stray dir
        containing a state.json is never offered as a resume candidate.
    Implementation: write a non-terminal state.json under a non-run-id dir and
        assert find_resumable_run returns None.
    Example: pytest asserts a 'scratchpd' dir is skipped.
    """
    harness = tmp_path / ".harness"
    bad = harness / "scratchpd"
    bad.mkdir(parents=True)
    now = datetime.now(UTC)
    write_state(
        bad / "state.json",
        RunState(
            state="iter_generating",
            run_id="abcd1234",
            iteration=2,
            target_dir=str(tmp_path),
            started_at=now,
            last_updated_at=now,
            last_completed_iteration=1,
        ),
    )
    assert find_resumable_run(harness) is None


def test_find_skips_run_with_no_durable_anchor(harness_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §H2 / H-Inv 2 — resume re-enters only from a fsync-durable iter_done
        boundary; a run crashed during planning (last_completed_iteration=0) has
        no anchor and must not be offered for resume.
    Implementation: write a non-terminal 'planning' run with last_completed=0 and
        assert find_resumable_run returns None.
    Example: pytest runs this test in the non-slow suite.
    """
    now = datetime.now(UTC)
    _write_run(harness_dir, "dddddddd", "planning", last_completed=0, started=now)
    assert find_resumable_run(harness_dir) is None


def test_find_prefers_anchored_run_over_no_anchor(harness_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §H2 / H-Inv 2 — a newer crash with no durable anchor must not shadow
        an older run that has a real iter_done boundary.
    Implementation: write a newer 'planning' run (last_completed=0) and an older
        'iter_generating' run (last_completed=3); assert the anchored one wins.
    Example: pytest runs this test in the non-slow suite.
    """
    now = datetime.now(UTC)
    _write_run(harness_dir, "eeeeeeee", "planning", last_completed=0, started=now)
    _write_run(
        harness_dir,
        "ffffffff",
        "iter_generating",
        last_completed=3,
        started=now - timedelta(hours=1),
    )
    point = find_resumable_run(harness_dir)
    assert point is not None
    assert point.run_id == "ffffffff"
    assert point.last_completed_iteration == 3
    assert point.start_iteration == 4


def test_find_returns_none_for_missing_dir(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert find_resumable_run(tmp_path / "nope") is None


def test_prepare_resume_archives_partial_iteration(harness_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = harness_dir / "cccccccc"
    (run_dir / "iteration-5").mkdir(parents=True)
    (run_dir / "iteration-5" / "marker.txt").write_text("partial")
    point = ResumePoint("cccccccc", run_dir, 4, 5)
    prepare_resume(run_dir, point)
    assert (run_dir / "iteration-5").is_dir()
    assert not (run_dir / "iteration-5" / "marker.txt").exists()
    archived = list(run_dir.glob("iteration-5.interrupted-*"))
    assert len(archived) == 1
    assert (archived[0] / "marker.txt").read_text() == "partial"
