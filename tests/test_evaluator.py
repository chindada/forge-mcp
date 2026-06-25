# tests/test_evaluator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.evaluator import run_evaluator, run_triage
from forge_mcp.models import EvalResult
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_evaluator_parses_gaps(tmp_path: Path):
    """Design: §6 the Evaluator emits EvalResult diffing code in cwd vs the spec.
    Implementation: fake returns one gap; run_evaluator (no sandbox param) parses it.
    Example: no_gaps False, one EvalGap.
    """
    payload = {
        "no_gaps": False,
        "summary": "1 gap",
        "gaps": [
            {
                "title": "missing X",
                "severity": "high",
                "design_doc_section": "§7.4",
                "current_state": "absent",
                "expected_state": "present",
                "suggested_fix": "add X",
            }
        ],
    }
    runner = FakeClaudeRunner([structured(payload)])
    res = await run_evaluator(runner, spec_text="s", eval_schema={"type": "object"}, cwd=tmp_path)
    assert not res.no_gaps and res.gaps[0].title == "missing X"


@pytest.mark.driver
async def test_evaluator_has_no_sandbox_param():
    """Design: §6 run_evaluator drops the now-unused sandbox parameter; cwd alone
        names the directory the evaluator diffs against the spec.
    Implementation: introspect the signature and assert 'sandbox' is absent and
        'cwd' is present.
    Example: 'sandbox' not in signature(run_evaluator).parameters.
    """
    import inspect

    params = inspect.signature(run_evaluator).parameters
    assert "sandbox" not in params
    assert "cwd" in params


@pytest.mark.driver
async def test_triage_demotes_uncited_design_fault(tmp_path: Path):
    """Design: §6 a design_fault with an invalid citation demotes to a code-bug.
    Implementation: fake triage claims a design fault citing absent text.
    Example: the row no longer passes the citation gate.
    """
    spec = "real spec text that is long enough to cite verbatim here."
    ev = EvalResult(no_gaps=False, summary="x", gaps=[])
    payload = {
        "triages": [
            {
                "gap_title": "missing X",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": ["not in the spec at all here!!"],
                "explanation": "e",
            }
        ]
    }
    runner = FakeClaudeRunner([structured(payload)])
    tr = await run_triage(
        runner, spec_text=spec, eval_result=ev, triage_schema={"type": "object"}, cwd=tmp_path
    )
    from forge_mcp.triage import passes_citation_gate

    assert not passes_citation_gate(tr.triages[0], spec)
