# tests/test_evaluator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.evaluator import run_evaluator, run_triage
from forge_mcp.models import EvalResult
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_evaluator_parses_gaps(tmp_path: Path):
    """Design: §5.3 the Evaluator emits EvalResult diffing code vs spec.
    Implementation: fake returns one gap; run_evaluator parses it.
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
    res = await run_evaluator(
        runner, spec_text="s", sandbox=tmp_path, eval_schema={"type": "object"}, cwd=tmp_path
    )
    assert not res.no_gaps and res.gaps[0].title == "missing X"


@pytest.mark.driver
async def test_triage_demotes_uncited_design_fault(tmp_path: Path):
    """Design: §5.3 a design_fault with an invalid citation demotes to a code-bug.
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
