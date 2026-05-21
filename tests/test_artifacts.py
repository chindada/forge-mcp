import json
import os

from forge_mcp.artifacts import (
    atomic_write_json,
    atomic_write_text,
    create_run_dir,
    escape_md,
    escape_md_inline,
    render_eval_md,
)
from forge_mcp.models import EvalGap, EvalResult


def test_atomic_write_text_mode_overwrite_no_tmp(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    path = tmp_path / "x.txt"
    atomic_write_text(path, "one")
    atomic_write_text(path, "two")
    assert path.read_text() == "two"
    if os.name == "posix":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not list(tmp_path.glob(".x.txt.*"))


def test_atomic_write_json_roundtrip(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": [1, 2]})
    assert json.loads(path.read_text()) == {"a": [1, 2]}


def test_create_run_dir_layout_and_modes(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = create_run_dir(tmp_path / ".harness", "abcd1234")
    assert (run_dir / "inputs").is_dir()
    assert (run_dir / "plan").is_dir()
    assert not (run_dir / "iteration-1").exists()
    if os.name == "posix":
        assert oct(run_dir.stat().st_mode & 0o777) == "0o700"
        assert oct((run_dir / "inputs").stat().st_mode & 0o777) == "0o700"


def test_escape_md_inline_order():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert escape_md_inline(r"a|b\c") == r"a\|b\\c"


def test_escape_md_heading_guard():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    escaped = escape_md("# title\nplain")
    assert not escaped.startswith("# title")
    assert escaped.startswith("\\# title")


def test_render_eval_md_no_gaps_no_removed_removed_tool_surface():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    md = render_eval_md(EvalResult(no_gaps=True, summary="done"), iteration_n=1)
    assert "removed browser surface" not in md
    assert "_no gaps reported_" in md


def test_render_eval_md_with_gaps():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    gap = EvalGap(
        title="gap|one",
        severity="high",
        design_doc_section="§1",
        current_state="# bad",
        expected_state="good",
        suggested_fix="fix",
    )
    md = render_eval_md(EvalResult(no_gaps=False, gaps=[gap], summary="summary"), iteration_n=2)
    assert "removed browser surface" not in md
    assert "gap\\|one" in md
    assert "| high |" in md


def test_prune_keeps_newest_n_and_current(harness_dir) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from datetime import UTC, datetime, timedelta

    from forge_mcp.artifacts import prune_old_runs
    from forge_mcp.state import RunState, write_state

    now = datetime.now(UTC)
    ids = ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd"]
    for i, rid in enumerate(ids):
        rd = harness_dir / rid
        rd.mkdir()
        write_state(
            rd / "state.json",
            RunState(
                state="completed",
                run_id=rid,
                target_dir=str(harness_dir.parent),
                iteration=1,
                started_at=now - timedelta(hours=len(ids) - i),
                last_completed_iteration=1,
            ),
        )
    prune_old_runs(harness_dir, keep_last=2, current_run_id="aaaaaaaa")
    remaining = {p.name for p in harness_dir.iterdir() if p.is_dir()}
    assert "dddddddd" in remaining and "cccccccc" in remaining
    assert "aaaaaaaa" in remaining
    assert "bbbbbbbb" not in remaining
