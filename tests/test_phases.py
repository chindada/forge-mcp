# tests/test_phases.py
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from forge_mcp.artifacts import init_run_layout
from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.models import Plan
from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured

# A spec whose body contains a verbatim, citable, amendable target so the
# in-loop amendment path can re-validate the citation and rewrite spec.md.
SPEC = (
    "Alpha section. The widget must flush before close. "
    "It must flush before close in all cases. Omega section."
)


def _plan(**kw):
    """Build a single-plan Plan fixture with defaults, overridable via kwargs.

    Design: §3/§4 the collapsed Plan carries only surface, verification_command,
        and body — there is no id/depends_on/file_scope — so the loop tests build
        from exactly those fields.
    Implementation: start from a default backend plan dict, apply overrides, and
        construct a Plan (extra='forbid' rejects any stray legacy field).
    Example: _plan(verification_command='false') yields a plan with that command.
    """
    base = dict(surface="backend", verification_command=None, body="do X")
    base.update(kw)
    return Plan(**base)  # type: ignore[arg-type]


def _eval(no_gaps: bool, gaps: list[dict] | None = None) -> dict:
    """Build an EvalResult payload dict for a scripted FakeClaudeRunner result.

    Design: §4 the evaluator output drives the completion gate; tests script a
        clean (no_gaps) or gapful evaluator turn without the real SDK.
    Implementation: return the minimal EvalResult dict with no_gaps, a gaps list,
        and a summary string.
    Example: _eval(True) returns {'no_gaps': True, 'gaps': [], 'summary': 'ok'}.
    """
    return {"no_gaps": no_gaps, "gaps": gaps or [], "summary": "ok"}


def _gap(title: str = "broken thing") -> dict:
    """Build one EvalGap payload dict for a scripted evaluator turn.

    Design: §4 a gapful evaluator turn forces triage and (when undemoted) blocks
        completion; tests supply a concrete code-bug gap.
    Implementation: return an EvalGap dict with all required fields populated.
    Example: _gap('broken thing')['title'] == 'broken thing'.
    """
    return {
        "title": title,
        "severity": "high",
        "design_doc_section": "§1",
        "current_state": "broken",
        "expected_state": "fixed",
        "suggested_fix": "fix it",
    }


def _amend_triage() -> dict:
    """Build a TriageResult payload that demotes the gap to a design fault + amendment.

    Design: §4 step 8 a cited design-fault triage carrying a proposed_amendment is
        applied to spec.md in-loop; the citation and 'before' text both appear in
        SPEC so the row passes the citation gate and the amendment applies.
    Implementation: return a TriageResult dict with one design_fault triage whose
        cited_sections and proposed_amendment.before are verbatim spec substrings.
    Example: _amend_triage()['triages'][0]['design_fault'] is True.
    """
    cite = "The widget must flush before close"
    return {
        "triages": [
            {
                "gap_title": "broken thing",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": [cite],
                "explanation": "spec is wrong",
                "proposed_amendment": {
                    "cited_sections": [cite],
                    "before": "must flush before close",
                    "after": "must flush and fsync before close",
                    "rationale": "fsync is required",
                },
            }
        ]
    }


@pytest.mark.driver
async def test_clean_iteration_completes_direct_edit(tmp_path: Path):
    """Design: §4 no gaps + no verify cmd -> done; the loop edits target_dir directly.

    Implementation: seed the layout, point target_dir at a writable dir, script a
        clean evaluator turn; assert done and that the generator ran rooted at
        target_dir (no sandbox copy).
    Example: terminal_state 'done', iterations 1.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    written: list[str] = []
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: written.append(cwd),
    )
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3,
    )
    assert res.terminal_state == "done"
    assert res.iterations == 1
    # The generator ran rooted at target_dir (direct edit, no sandbox copy).
    assert written == [str(target)]


def test_plan_loop_result_has_no_change_set_or_amendment_fields():
    """Design: §4 PlanLoopResult drops change_set and proposed_amendment.

    Implementation: inspect the dataclass fields; assert the removed names are
        absent and the surviving fields are exactly the new set.
    Example: 'change_set' not in field names.
    """
    names = {f.name for f in dataclasses.fields(PlanLoopResult)}
    assert "change_set" not in names
    assert "proposed_amendment" not in names
    assert names == {
        "terminal_state",
        "iterations",
        "last_gaps",
        "synthesized",
        "stop_reason",
    }


@pytest.mark.driver
async def test_verify_failure_synthesizes_only_verify_gap(tmp_path: Path):
    """Design: §6.5 a failing verification synthesizes a non-demotable §6.5 gap, blocks done.

    Implementation: verification_command 'false' with a clean evaluator -> not
        done; the single synthesized gap is the §6.5 verify gap (no §9 git gap).
    Example: terminal_state 'incomplete', synthesized has exactly the §6.5 gap.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(verification_command="false"),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
    )
    assert res.terminal_state == "incomplete"
    assert [g.design_doc_section for g in res.synthesized] == ["§6.5"]
    assert layout.verify_txt(1).exists()


@pytest.mark.driver
async def test_verify_pass_with_no_gaps_completes(tmp_path: Path):
    """Design: §4 verify passing + no gaps -> done (two-conjunct gate satisfied).

    Implementation: verification_command 'true' (exit 0) with a clean evaluator.
    Example: terminal_state 'done', no synthesized gaps.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(verification_command="true"),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
    )
    assert res.terminal_state == "done"
    assert res.synthesized == []


@pytest.mark.driver
async def test_in_loop_amendment_rewrites_spec_and_continues(tmp_path: Path):
    """Design: §4 step 8 a validated design-fault triage rewrites spec.md in-loop and continues.

    Implementation: iteration 1 evaluator finds a gap, triage demotes it to a
        cited design fault with an amendment; iteration 2 evaluator is clean so
        the loop completes. Assert spec.md gained the amended text, an amendments
        log entry was written with no plan_id field, and done at iteration 2.
    Example: spec.md contains 'fsync'; terminal_state 'done', iterations 2.
    """
    import json

    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_eval(False, [_gap()])),
            structured(_amend_triage()),
            structured({"contract": "# Remediation plan for iteration 2"}),
            structured(_eval(True)),
        ]
    )
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 2)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3,
    )
    assert res.terminal_state == "done"
    assert res.iterations == 2
    # The Claude-authored remediation contract for iteration 2 was written to disk.
    assert layout.contract(2).read_text() == "# Remediation plan for iteration 2"
    # The amendment durably rewrote spec.md against the current spec_text.
    assert "fsync" in layout.spec_md.read_text()
    # The amendments log entry carries no plan_id field (single-plan).
    log = layout.spec_amendments.read_text().strip().splitlines()
    assert log, "expected one amendment log entry"
    entry = json.loads(log[-1])
    assert "plan_id" not in entry
    assert entry["after"] == "must flush and fsync before close"


@pytest.mark.driver
async def test_unresolved_code_bug_hits_iteration_cap(tmp_path: Path):
    """Design: §4 a persistent undemoted code bug never completes -> incomplete at the cap.

    Implementation: every iteration the evaluator reports the same gap and triage
        does NOT demote it (empty triages list); max_iterations=2 forces the cap.
    Example: terminal_state 'incomplete', stop_reason 'iteration cap'.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_eval(False, [_gap()])),
            structured({"triages": []}),
            structured({"contract": "# Remediation plan for iteration 2"}),
            structured(_eval(False, [_gap()])),
            structured({"triages": []}),
        ]
    )
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 2)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=2,
    )
    assert res.terminal_state == "incomplete"
    assert res.stop_reason == "iteration cap"
    assert res.iterations == 2


@pytest.mark.driver
async def test_generator_failure_isolated_as_failed(tmp_path: Path):
    """Design: §4 an unhandled error in the loop returns a 'failed' report, not a raise.

    Implementation: a codex runner whose on_generate raises forces the loop's
        failure-isolation path.
    Example: terminal_state 'failed', stop_reason names the exception.
    """

    def _boom(_cwd: str) -> None:
        """Raise to simulate a generator failure inside the loop.

        Design: §4 exercises the failure-isolation branch.
        Implementation: unconditionally raise RuntimeError.
        Example: _boom('/x') raises RuntimeError.
        """
        raise RuntimeError("codex exploded")

    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})], on_generate=_boom)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=2,
    )
    assert res.terminal_state == "failed"
    assert "codex exploded" in (res.stop_reason or "")


@pytest.mark.driver
async def test_remediation_gaps_are_triage_filtered(tmp_path: Path):
    """Design: §4 the remediation contract targets only effective code bugs — a
        validly-demoted design fault (the spec's problem) is excluded from what the
        Generator is told to fix, while last_gaps stays RAW for the engine report.
    Implementation: iter1 reports a code bug + a design-fault gap; triage demotes
        the design fault with a valid spec citation and no amendment. Capture
        iter2's remediation prompt and assert it lists the code bug but not the
        demoted gap; assert res.last_gaps still carries both (raw).
    Example: 'real bug' is in the remediation prompt; 'spec issue' is not.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    # A design-fault triage with a VALID verbatim citation but NO amendment, so the
    # gap is demoted (excluded from code bugs) yet nothing rewrites the spec.
    demote = {
        "triages": [
            {
                "gap_title": "spec issue",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": ["The widget must flush before close"],
                "explanation": "spec contradicts itself",
            }
        ]
    }
    two_gaps = _eval(False, [_gap("real bug"), _gap("spec issue")])
    claude = FakeClaudeRunner(
        [
            structured(two_gaps),  # iter1 eval
            structured(demote),  # iter1 triage
            structured({"contract": "# remediation"}),  # iter2 remediation (prompt captured)
            structured(two_gaps),  # iter2 eval
            structured(demote),  # iter2 triage
        ]
    )
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 2)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=2,
    )
    # The remediation prompt (the only one carrying the gaps-to-close section) lists
    # the effective code bug, not the validly-demoted design fault.
    rem_prompts = [p for p in claude.prompts if "Still-open gaps to close" in p]
    assert len(rem_prompts) == 1
    assert "real bug" in rem_prompts[0]
    assert "spec issue" not in rem_prompts[0]
    # last_gaps (the engine's honest report) stays RAW — both gaps survive.
    assert {g.title for g in res.last_gaps} == {"real bug", "spec issue"}
