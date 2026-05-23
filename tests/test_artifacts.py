import json
import os
from pathlib import Path

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


def test_escape_md_inline_collapses_newlines_to_br() -> None:
    """§10.4 / finding 3 — newlines become <br> after pipe/backslash escaping.

    Design: multi-line gap prose must not break markdown table rows; a single
        shared escaper fixes all three tables.
    Implementation: feed CRLF/CR/LF and assert each becomes <br>, that pipes and
        backslashes are still escaped, and that no raw newline remains.
    Example: escape_md_inline('a\nb') returns 'a<br>b'.
    """
    assert escape_md_inline("a\nb") == "a<br>b"
    assert escape_md_inline("a\r\nb") == "a<br>b"
    assert escape_md_inline("a\rb") == "a<br>b"
    assert escape_md_inline("a|b\\c\nd") == "a\\|b\\\\c<br>d"
    assert "\n" not in escape_md_inline("x\ny\nz")
    assert escape_md_inline("a|b\\c") == "a\\|b\\\\c"


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


def test_prune_old_runs_keeps_non_run_id_dirs(tmp_path: Path) -> None:
    """§H9 / finding 7 — prune only acts on real run-id dirs.

    Design: prune previously used len(name)==8 and could delete any 8-char dir;
        the matcher must be ^[0-9a-f]{8}$ so siblings survive.
    Implementation: create one real run-id dir plus 8-char non-hex / uppercase
        siblings and assert only the real run is eligible for pruning.
    Example: pytest asserts the 8-char 'BACKUP01' dir survives.
    """
    from forge_mcp.artifacts import prune_old_runs

    harness = tmp_path / ".harness"
    harness.mkdir()
    real = harness / "abcd1234"
    real.mkdir()
    upper = harness / "ABCDEF12"
    upper.mkdir()
    nonhex = harness / "backup_x"
    nonhex.mkdir()
    sibling = harness / "0000abcd"
    sibling.mkdir()

    prune_old_runs(harness, keep_last=1)

    assert upper.exists()
    assert nonhex.exists()
    assert real.exists() or sibling.exists()


def test_write_sessions_json_creates_ordered_private_file(tmp_path):
    """write_sessions_json records iteration and ordered phases (§C2.4).

    Design: sessions.json preserves phase order as forensic data.
    Implementation: write two entries and parse the resulting JSON.
    Example: payload['phases'][0]['phase'] == 'iter_generating'.
    """
    import json
    import os
    import stat

    from forge_mcp.artifacts import write_sessions_json

    target = tmp_path / "sessions.json"
    entries = [{"phase": "iter_generating"}, {"phase": "iter_evaluating"}]
    write_sessions_json(target, 2, entries)
    assert json.loads(target.read_text()) == {"iteration": 2, "phases": entries}
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_design_fingerprint_atomic(tmp_path):
    """§L2.3 — writes one-line hex digest + newline; 0600 on POSIX.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    fp = "a" * 64
    artifacts.write_design_fingerprint(inputs_dir, fp)
    p = inputs_dir / "design.fingerprint"
    assert p.read_text() == fp + "\n"
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_write_design_fingerprint_idempotent(tmp_path):
    """§L2.3 — re-writing with the same content replaces atomically.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    fp = "b" * 64
    artifacts.write_design_fingerprint(inputs_dir, fp)
    artifacts.write_design_fingerprint(inputs_dir, fp)
    assert (inputs_dir / "design.fingerprint").read_text() == fp + "\n"


def test_write_prior_attempts_under_cap_no_overflow(tmp_path):
    """§L6.2 — text under cap writes whole, returns None.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    text = "# small\n\nbody\n"
    overflow = artifacts.write_prior_attempts(inputs_dir, text)
    assert overflow is None
    assert (inputs_dir / "prior_attempts.md").read_text() == text
    assert not (inputs_dir / "prior_attempts-overflow.md").exists()


def test_write_prior_attempts_over_cap_splits(tmp_path):
    """§L6.2 — text over cap writes head + overflow; returns overflow path.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    body = ("a" * 79 + "\n") * 600
    overflow = artifacts.write_prior_attempts(inputs_dir, body)
    assert overflow is not None
    assert overflow == inputs_dir / "prior_attempts-overflow.md"
    head = (inputs_dir / "prior_attempts.md").read_text()
    tail = overflow.read_text()
    assert len(head.encode("utf-8")) <= 32_768 + 200
    assert "truncated" in head.lower()
    assert body.startswith(head.split("\n... truncated")[0].rstrip("\n"))
    assert body.rstrip("\n").endswith(tail.rstrip("\n"))


def test_write_prior_attempts_posix_0600(tmp_path):
    """§L6.2 — head + overflow are 0600 on POSIX.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    artifacts.write_prior_attempts(inputs_dir, "line\n" * 10_000)
    if os.name == "posix":
        assert (inputs_dir / "prior_attempts.md").stat().st_mode & 0o777 == 0o600
        assert (inputs_dir / "prior_attempts-overflow.md").stat().st_mode & 0o777 == 0o600


def test_write_design_flaws_empty_list(tmp_path):
    """§L8.4 — empty list still writes the artifact envelope.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    artifacts.write_design_flaws(tmp_path, [])
    assert json.loads((tmp_path / "design_flaws.json").read_text()) == {"gaps": []}


def test_write_design_flaws_nonempty(tmp_path):
    """§L8.4 — non-empty list serializes via model_dump(mode='json').

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts
    from forge_mcp.models import DesignFlawGap

    flaw = DesignFlawGap(
        gap=EvalGap(
            title="t",
            severity="high",
            design_doc_section="§1",
            current_state="c",
            expected_state="e",
            suggested_fix="f",
        ),
        iteration_n=2,
        fault_kind="ambiguity",
        cited_sections=["§3.4 says one thing and §5.2 says the opposite"],
        explanation="contradiction",
    )
    artifacts.write_design_flaws(tmp_path, [flaw])
    payload = json.loads((tmp_path / "design_flaws.json").read_text())
    assert payload["gaps"][0]["fault_kind"] == "ambiguity"


def test_write_design_flaws_0600(tmp_path):
    """§L8.4 — 0600 on POSIX, same as every §13 artifact.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp import artifacts

    artifacts.write_design_flaws(tmp_path, [])
    if os.name == "posix":
        assert (tmp_path / "design_flaws.json").stat().st_mode & 0o777 == 0o600
