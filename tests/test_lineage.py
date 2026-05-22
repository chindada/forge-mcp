"""Tests for orchestrator.lineage (§L)."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge_mcp.models import DesignFlawGap, EvalGap
from forge_mcp.orchestrator import lineage


def test_fingerprint_identical_bytes_same_digest() -> None:
    """§L2.1 — identical bytes must produce identical digests.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.fingerprint_design("# heading\nbody\n") == lineage.fingerprint_design(
        "# heading\nbody\n"
    )


def test_fingerprint_crlf_normalized() -> None:
    """§L2.1 — CRLF/CR normalize to LF before hashing.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.fingerprint_design("a\r\nb\n") == lineage.fingerprint_design("a\nb\n")
    assert lineage.fingerprint_design("a\rb\n") == lineage.fingerprint_design("a\nb\n")


def test_fingerprint_trailing_whitespace_stripped() -> None:
    """§L2.1 — trailing whitespace per line must not invalidate.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.fingerprint_design("a  \nb\t\n") == lineage.fingerprint_design("a\nb\n")


def test_fingerprint_heading_rename_invalidates() -> None:
    """§L2.1 — meaningful edits MUST change the digest.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.fingerprint_design("# A\n") != lineage.fingerprint_design("# B\n")


def test_fingerprint_empty_and_whitespace_only_distinct() -> None:
    """§L2.1 — empty and pure-whitespace inputs have stable distinct digests.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.fingerprint_design("") != lineage.fingerprint_design("\n")
    assert lineage.fingerprint_design("") == lineage.fingerprint_design("")
    assert lineage.fingerprint_design("   \n  \n") == lineage.fingerprint_design("\n\n")


def test_fingerprint_utf8_multibyte_round_trips() -> None:
    """§L2.1 — multi-byte UTF-8 hashes deterministically.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    text = "héllo — ✨\n"
    assert lineage.fingerprint_design(text) == lineage.fingerprint_design(text)
    assert lineage.fingerprint_design(text) != lineage.fingerprint_design("hello\n")


def test_fingerprint_shape() -> None:
    """§L2.2 — hex digest is 64 lowercase hex chars.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    fp = lineage.fingerprint_design("body\n")
    assert re.fullmatch(r"[0-9a-f]{64}", fp), fp


def test_lineage_candidate_is_frozen_dataclass() -> None:
    """§L4.1 — LineageCandidate is hashable and immutable.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    cand = lineage.LineageCandidate(
        run_id="abcd1234",
        run_dir=Path("/r/.harness/abcd1234"),
        last_updated_at=datetime(2026, 5, 20, 14, 30, tzinfo=UTC),
    )
    assert cand.run_id == "abcd1234"
    with pytest.raises(dataclasses.FrozenInstanceError):
        cand.run_id = "different"  # type: ignore[misc]
    assert hash(cand) == hash(cand)


def _make_run(
    harness_dir: Path,
    run_id: str,
    *,
    fingerprint: str | None,
    state: str = "completed",
    cancelled: bool = False,
    last_updated_at: datetime | None = None,
    started_at: datetime | None = None,
) -> Path:
    """Synthesize a sibling run dir for find_lineage_runs tests.

    Design: tests/test_lineage uses a small factory so each scenario only
        names the dimensions that matter for §L3 eligibility.
    Implementation: writes inputs/design.fingerprint + state.json with the
        forge-mcp RunState shape; fingerprint=None omits the fingerprint file.
    Example: _make_run(harness, "aaaa0001", fingerprint="f" * 64).
    """
    run_dir = harness_dir / run_id
    (run_dir / "inputs").mkdir(parents=True, exist_ok=True)
    if fingerprint is not None:
        (run_dir / "inputs" / "design.fingerprint").write_text(fingerprint + "\n")
    started = started_at or datetime(2026, 1, 1, tzinfo=UTC)
    updated = last_updated_at or (started + timedelta(seconds=60))
    state_payload = {
        "state": state,
        "run_id": run_id,
        "target_dir": "/repo",
        "iteration": 0,
        "started_at": started.isoformat(),
        "last_updated_at": updated.isoformat(),
        "cancelled": cancelled,
        "last_completed_iteration": 0,
    }
    (run_dir / "state.json").write_text(json.dumps(state_payload))
    return run_dir


def test_find_lineage_runs_empty_harness(tmp_path: Path) -> None:
    """§L3 — empty harness returns [].

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    assert (
        lineage.find_lineage_runs(harness, "deadbeef" * 8, current_run_id="aaaa0000", top_k=4) == []
    )


def test_find_lineage_runs_one_eligible(tmp_path: Path) -> None:
    """§L3 — one eligible prior run returns one candidate.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "a" * 64
    _make_run(harness, "aaaa0001", fingerprint=fp, state="completed")
    out = lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4)
    assert [c.run_id for c in out] == ["aaaa0001"]


def test_find_lineage_runs_sorted_and_truncated(tmp_path: Path) -> None:
    """§L3.2 — top-K is most-recent first and truncates after sorting.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "b" * 64
    base = datetime(2026, 5, 1, tzinfo=UTC)
    for i, run_id in enumerate(["aaaa0001", "aaaa0002", "aaaa0003", "aaaa0004", "aaaa0005"]):
        _make_run(harness, run_id, fingerprint=fp, last_updated_at=base + timedelta(hours=i))
    out = lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=2)
    assert [c.run_id for c in out] == ["aaaa0005", "aaaa0004"]


def test_find_lineage_runs_fingerprint_mismatch_skipped(tmp_path: Path) -> None:
    """§L3 rule 2 — fingerprint mismatch is skipped.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    _make_run(harness, "aaaa0001", fingerprint="d" * 64)
    assert lineage.find_lineage_runs(harness, "e" * 64, current_run_id="aaaa9999", top_k=4) == []


def test_find_lineage_runs_excludes_current_run(tmp_path: Path) -> None:
    """§L3.3 — the current run_id is excluded even if eligible.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "f" * 64
    _make_run(harness, "aaaa0001", fingerprint=fp)
    assert lineage.find_lineage_runs(harness, fp, current_run_id="aaaa0001", top_k=4) == []


def test_find_lineage_runs_top_k_zero_returns_empty(tmp_path: Path) -> None:
    """§L3.2 — top_k=0 is the kill switch.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "1" * 64
    _make_run(harness, "aaaa0001", fingerprint=fp)
    assert lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=0) == []


_NON_TERMINAL = [
    "init",
    "canonicalizing",
    "planning",
    "planned",
    "iter_generating",
    "iter_verifying",
    "iter_evaluating",
    "iter_triaging",
    "iter_done",
    "iter_remediating",
    "finalizing",
    "cancelling",
]


@pytest.mark.parametrize("state", _NON_TERMINAL)
def test_find_lineage_runs_non_terminal_state_skipped(tmp_path: Path, state: str) -> None:
    """§L3.1 rule 3 — non-terminal states must skip.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "2" * 64
    _make_run(harness, "aaaa0001", fingerprint=fp, state=state)
    out = lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4)
    assert out == [], f"non-terminal state {state!r} must be skipped"


def test_find_lineage_runs_cancelled_skipped(tmp_path: Path) -> None:
    """§L3.1 rule 4 — cancelled=True is the load-bearing filter for §8.5 cancels.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "3" * 64
    _make_run(harness, "aaaa0001", fingerprint=fp, state="failed", cancelled=True)
    assert lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4) == []


def test_find_lineage_runs_missing_fingerprint_silently_skipped(tmp_path: Path) -> None:
    """§L3.3 — missing design.fingerprint is silently skipped.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    _make_run(harness, "aaaa0001", fingerprint=None)
    assert lineage.find_lineage_runs(harness, "4" * 64, current_run_id="aaaa9999", top_k=4) == []


def test_find_lineage_runs_malformed_state_silently_skipped(tmp_path: Path) -> None:
    """§L4.2 — malformed state.json is silently skipped, scan continues.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "5" * 64
    bad = harness / "aaaa0001"
    (bad / "inputs").mkdir(parents=True)
    (bad / "inputs" / "design.fingerprint").write_text(fp + "\n")
    (bad / "state.json").write_text("not json")
    _make_run(harness, "aaaa0002", fingerprint=fp, state="completed")
    out = lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4)
    assert [c.run_id for c in out] == ["aaaa0002"]


def test_find_lineage_runs_non_run_id_names_ignored(tmp_path: Path) -> None:
    """§L4.1 — only ^[0-9a-f]{8}$ names are considered.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "6" * 64
    (harness / "run.lock").write_text("x")
    (harness / ".gitignore").write_text("*")
    (harness / "ABCD1234").mkdir()
    (harness / "abc").mkdir()
    _make_run(harness, "aaaa0001", fingerprint=fp)
    out = lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4)
    assert [c.run_id for c in out] == ["aaaa0001"]


def test_find_lineage_runs_symlink_rejected(tmp_path: Path) -> None:
    """§L-Inv 5 — symlinked candidates MUST be rejected outright.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    fp = "7" * 64
    attacker = tmp_path / "attacker"
    (attacker / "inputs").mkdir(parents=True)
    (attacker / "inputs" / "design.fingerprint").write_text(fp + "\n")
    state_payload = {
        "state": "completed",
        "run_id": "aaaa0001",
        "target_dir": "/repo",
        "iteration": 0,
        "started_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "last_updated_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
        "cancelled": False,
        "last_completed_iteration": 0,
    }
    (attacker / "state.json").write_text(json.dumps(state_payload))
    (harness / "aaaa0001").symlink_to(attacker, target_is_directory=True)
    assert lineage.find_lineage_runs(harness, fp, current_run_id="aaaa9999", top_k=4) == []


def test_prior_run_summary_fields() -> None:
    """§L5.1 — PriorRunSummary holds the planner-facing fields.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    s = lineage.PriorRunSummary(
        run_id="abcd1234",
        status="incomplete",
        reason="non-progress: gap-set unchanged for 4 iters",
        iterations_used=10,
        runtime_seconds=2820,
        unresolved_gaps=[],
        design_flaw_gaps=[],
        verify_tail="14 failed",
        eval_summary="missing /healthz",
    )
    assert s.run_id == "abcd1234"
    assert s.status == "incomplete"
    assert s.iterations_used == 10
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.run_id = "x"  # type: ignore[misc]


def _write_eval_json(run_dir: Path, iteration_n: int, gaps: list[dict], summary: str = "s") -> None:
    """Write one synthetic eval.json file for summarize_prior_run tests.

    Design: §L5 summarization selects from iteration artifacts, so tests need
        compact fixtures that create realistic eval.json payloads.
    Implementation: mkdir iteration-N and write the minimal EvalResult shape.
    Example: _write_eval_json(run_dir, 1, [_gap("missing")]).
    """
    iter_dir = run_dir / f"iteration-{iteration_n}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "eval.json").write_text(
        json.dumps({"no_gaps": False, "gaps": gaps, "summary": summary})
    )


def _gap(title: str, severity: str = "high") -> dict:
    """Return a minimal EvalGap-shaped dict for lineage tests.

    Design: lineage tests should vary only title and severity unless a scenario
        needs prose fields.
    Implementation: fill stable dummy fields accepted by EvalGap validation.
    Example: payload = _gap("missing /healthz", severity="medium").
    """
    return {
        "title": title,
        "severity": severity,
        "design_doc_section": "§1",
        "current_state": "missing",
        "expected_state": "present",
        "suggested_fix": "add it",
    }


def test_summarize_happy_path(tmp_path: Path) -> None:
    """§L5.2 — happy path populates from state, eval, verify.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(
        tmp_path,
        "abcd1234",
        fingerprint="a" * 64,
        state="incomplete",
        started_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        last_updated_at=datetime(2026, 5, 1, 12, 47, tzinfo=UTC),
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text())
    payload["reason"] = "non-progress: gap-set unchanged for 4 iters"
    payload["last_completed_iteration"] = 8
    state_path.write_text(json.dumps(payload))
    _write_eval_json(run_dir, 8, [_gap("missing /healthz")], summary="missing /healthz")
    (run_dir / "iteration-8" / "verify.txt").write_text("PASS PASS FAIL FAIL")
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None
    assert s.run_id == "abcd1234"
    assert s.status == "incomplete"
    assert s.reason == "non-progress: gap-set unchanged for 4 iters"
    assert s.iterations_used == 8
    assert s.runtime_seconds == 47 * 60
    assert s.unresolved_gaps[0].title == "missing /healthz"
    assert s.verify_tail == "PASS PASS FAIL FAIL"
    assert s.eval_summary == "missing /healthz"
    assert s.design_flaw_gaps == []


def test_summarize_picks_highest_iteration_numerically(tmp_path: Path) -> None:
    """§L5.2 — iteration-10 selected over iteration-2.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(tmp_path, "abcd2222", fingerprint="b" * 64, state="incomplete")
    _write_eval_json(run_dir, 2, [_gap("old")], summary="old")
    _write_eval_json(run_dir, 10, [_gap("new")], summary="new")
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None
    assert s.eval_summary == "new"
    assert s.unresolved_gaps[0].title == "new"


def test_summarize_missing_verify_and_no_iterations(tmp_path: Path) -> None:
    """§L5.2 — missing optional artifacts degrade to None or [].

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    with_eval = _make_run(tmp_path, "abcd3333", fingerprint="c" * 64, state="completed")
    _write_eval_json(with_eval, 1, [], summary="ok")
    s = lineage.summarize_prior_run(with_eval)
    assert s is not None and s.verify_tail is None and s.eval_summary == "ok"
    no_eval = _make_run(tmp_path, "abcd4444", fingerprint="d" * 64, state="failed")
    s2 = lineage.summarize_prior_run(no_eval)
    assert s2 is not None
    assert s2.unresolved_gaps == [] and s2.eval_summary is None and s2.verify_tail is None


def test_summarize_truncates_gap_fields(tmp_path: Path) -> None:
    """§L5.2 — title and prose fields are bounded.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(tmp_path, "abcd6666", fingerprint="f" * 64, state="incomplete")
    big = {
        "title": "x" * 500,
        "severity": "high",
        "design_doc_section": "§1",
        "current_state": "c" * 1000,
        "expected_state": "e" * 1000,
        "suggested_fix": "s" * 1000,
    }
    _write_eval_json(run_dir, 1, [big], summary="s")
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None
    g = s.unresolved_gaps[0]
    assert len(g.title) == 200 and g.title.endswith("…")
    assert len(g.current_state) == 500 and g.current_state.endswith("…")
    assert len(g.expected_state) == 500 and g.expected_state.endswith("…")
    assert len(g.suggested_fix) == 500 and g.suggested_fix.endswith("…")


def test_summarize_top_10_gaps_by_severity_then_alphabetic(tmp_path: Path) -> None:
    """§L5.2 — top-10 by severity, ties alphabetic.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(tmp_path, "abcd7777", fingerprint="1" * 64, state="incomplete")
    gaps = (
        [_gap(f"high-{i:02d}", severity="high") for i in range(5)]
        + [_gap(f"medium-{i:02d}", severity="medium") for i in range(5)]
        + [_gap(f"low-{i:02d}", severity="low") for i in range(5)]
    )
    _write_eval_json(run_dir, 1, gaps, summary="s")
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None
    assert [g.title for g in s.unresolved_gaps] == [
        *(f"high-{i:02d}" for i in range(5)),
        *(f"medium-{i:02d}" for i in range(5)),
    ]


def test_summarize_verify_tail_last_2000_chars(tmp_path: Path) -> None:
    """§L5.2 — verify.txt tail is last 2000 chars.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(tmp_path, "abcd8888", fingerprint="2" * 64, state="incomplete")
    _write_eval_json(run_dir, 1, [], summary="s")
    (run_dir / "iteration-1" / "verify.txt").write_text("HEAD" + "x" * 5000 + "TAIL")
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None and s.verify_tail is not None
    assert len(s.verify_tail) == 2000
    assert s.verify_tail.endswith("TAIL")


def test_summarize_reads_design_flaws_sidecar(tmp_path: Path) -> None:
    """§L8.4 — design_flaws.json sidecar feeds summary design_flaw_gaps.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = _make_run(tmp_path, "abcd9999", fingerprint="3" * 64, state="incomplete")
    _write_eval_json(run_dir, 1, [], summary="s")
    flaw = DesignFlawGap(
        gap=EvalGap(**_gap("ambiguity in §3.4")),
        iteration_n=2,
        fault_kind="ambiguity",
        cited_sections=["§3.4 says one thing and §5.2 says the opposite"],
        explanation="two sections contradict",
    )
    (run_dir / "design_flaws.json").write_text(json.dumps({"gaps": [flaw.model_dump(mode="json")]}))
    s = lineage.summarize_prior_run(run_dir)
    assert s is not None
    assert len(s.design_flaw_gaps) == 1
    assert s.design_flaw_gaps[0].fault_kind == "ambiguity"
    assert s.design_flaw_gaps[0].iteration_n == 2


def test_summarize_unreadable_state_returns_none(tmp_path: Path) -> None:
    """§L5.2 — TOCTOU race: state.json unreadable returns None.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = tmp_path / "abcd0000"
    (run_dir / "inputs").mkdir(parents=True)
    assert lineage.summarize_prior_run(run_dir) is None


def test_render_empty_list_empty_string() -> None:
    """§L6.1 — empty list → empty string.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    assert lineage.render_prior_attempts([]) == ""


def test_render_one_summary_starts_with_anti_anchoring_header() -> None:
    """§L6.1 — first heading is the prior-attempts boilerplate.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    s = lineage.PriorRunSummary(
        run_id="abcd1234",
        status="completed",
        reason=None,
        iterations_used=3,
        runtime_seconds=120,
        unresolved_gaps=[],
        design_flaw_gaps=[],
        verify_tail=None,
        eval_summary=None,
    )
    out = lineage.render_prior_attempts([s])
    assert out.startswith("# Prior Attempts at This Design")
    assert "## Run abcd1234" in out
    assert "completed" in out


def test_render_preserves_summary_order() -> None:
    """§L6.1 — multiple summaries appear in input order.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    s1 = lineage.PriorRunSummary("aaaa0001", "incomplete", None, 10, 0, [], [], None, None)
    s2 = lineage.PriorRunSummary("aaaa0002", "failed", None, 2, 0, [], [], None, None)
    out = lineage.render_prior_attempts([s1, s2])
    assert out.index("## Run aaaa0001") < out.index("## Run aaaa0002")


def test_render_escapes_markdown_in_gap_title() -> None:
    """§L6.3 — a gap title with '#' is escaped, not rendered as heading.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    g = EvalGap(
        title="# evil heading",
        severity="high",
        design_doc_section="§1",
        current_state="x",
        expected_state="y",
        suggested_fix="z",
    )
    s = lineage.PriorRunSummary("abcd1234", "incomplete", None, 1, 0, [g], [], None, None)
    out = lineage.render_prior_attempts([s])
    assert all(line != "# evil heading" for line in out.splitlines())


def test_render_omits_verify_section_when_none() -> None:
    """§L6.1 — verify_tail=None means no verify subsection.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    s = lineage.PriorRunSummary("abcd1234", "completed", None, 1, 0, [], [], None, None)
    out = lineage.render_prior_attempts([s])
    assert "Verify" not in out


def test_per_summary_cap_enforced() -> None:
    """§L-Decision 5 — single rendered summary is ≤ per-summary cap.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    gaps = [
        EvalGap(
            title="x" * 200,
            severity="high",
            design_doc_section="§1",
            current_state="c" * 500,
            expected_state="e" * 500,
            suggested_fix="f" * 500,
        )
        for _ in range(10)
    ]
    s = lineage.PriorRunSummary(
        run_id="abcd1234",
        status="incomplete",
        reason="r" * 200,
        iterations_used=10,
        runtime_seconds=0,
        unresolved_gaps=gaps,
        design_flaw_gaps=[],
        verify_tail="v" * 2000,
        eval_summary="s" * 2000,
    )
    rendered = lineage._render_one_summary(lineage._shrink_summary(s))
    assert len(rendered.encode("utf-8")) <= lineage._MAX_PER_SUMMARY_BYTES
