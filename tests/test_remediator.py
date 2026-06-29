# tests/test_remediator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers._claude import StructuredResult
from forge_mcp.drivers.remediator import run_remediation
from forge_mcp.models import EvalGap, GapSummary


class _CapturingRunner:
    """ClaudeRunner double that records the prompt and returns a structured contract.

    Design: §4 the Remediation stage returns structured output (a RemediationResult
        `contract` field); the fake returns that contract while ALSO emitting
        narration in .text, so tests can prove run_remediation reads the contract
        field and strips the agent's interstitial narration.
    Implementation: capture prompt/options on run(); return a StructuredResult whose
        structured_output is {"contract": body} and whose text is the narration;
        interrupt/aclose are no-ops.
    Example: ``_CapturingRunner("# plan")`` returns contract "# plan".
    """

    def __init__(self, body: str, narration: str = "") -> None:
        """Store the contract body, the narration to emit, and init capture slots.

        Design: §4 tests inspect the composed remediation prompt and need to prove
            narration carried in .text never reaches the returned contract.
        Implementation: store body and narration; init prompt to '' and
            options/last_session_id to None.
        Example: ``_CapturingRunner("# plan").body == "# plan"``.
        """
        self.body = body
        self.narration = narration
        self.prompt: str = ""
        self.options: object = None
        self.last_session_id: str | None = None

    async def run(self, *, prompt: str, options: object) -> StructuredResult:
        """Record the prompt/options and return the structured contract result.

        Design: §4 run_remediation reads structured_output["contract"]; the fake
            returns that contract plus separate narration in .text so the test can
            assert the narration is stripped from the returned contract.
        Implementation: store prompt/options; return StructuredResult with
            structured_output={"contract": self.body} and text=self.narration.
        Example: ``await _CapturingRunner("x").run(prompt="p", options=o)`` carries
            structured_output {"contract": "x"}.
        """
        self.prompt = prompt
        self.options = options
        return StructuredResult(
            structured_output={"contract": self.body},
            text=self.narration,
            session_id="fake",
            init_skills=None,
        )

    async def interrupt(self) -> None:
        """No-op interrupt (Protocol surface)."""

    async def aclose(self) -> None:
        """No-op close (Protocol surface)."""


def _gap(title: str = "missing X") -> EvalGap:
    """Build one EvalGap fixture for a remediation turn.

    Design: §4 the remediation prompt is composed from the open gaps; tests
        supply a concrete gap to assert it reaches the prompt.
    Implementation: return an EvalGap with all required fields populated.
    Example: ``_gap().title == "missing X"``.
    """
    return EvalGap(
        title=title,
        severity="high",
        design_doc_section="§7.4",
        current_state="absent",
        expected_state="present",
        suggested_fix="add X",
    )


@pytest.mark.driver
async def test_remediation_returns_structured_contract_stripping_narration(tmp_path: Path):
    """Design: §4 run_remediation returns the structured `contract` field, NOT the
        agent's freeform turn text — so interstitial narration never leaks into the
        contract handed to the Generator. It also grounds the prompt in the gaps + path.
    Implementation: the fake returns contract="# Remediation plan…" plus separate
        narration in .text; assert the return equals the contract, the narration is
        absent, and the gap title + project path appear in the prompt.
    Example: out == the clean plan; the narration string is not in out.
    """
    narration = "I'll inspect the repo, then consult the plan-writing skill."
    runner = _CapturingRunner("# Remediation plan\nclose the gap", narration=narration)
    out = await run_remediation(
        runner,
        spec_text="the frozen spec",
        plan_body="# original plan",
        gaps=[_gap()],
        synthesized=[],
        nudge=False,
        cwd=tmp_path,
    )
    assert out == "# Remediation plan\nclose the gap"
    # The agent's interstitial narration (carried in .text) is stripped — the bug this fixes.
    assert narration not in out
    assert "missing X" in runner.prompt
    assert str(tmp_path) in runner.prompt
    # Reference-only plan body is present but flagged not to be restated.
    assert "do not restate" in runner.prompt.lower()


@pytest.mark.driver
async def test_remediation_surfaces_nudge_and_synthesized_blockers(tmp_path: Path):
    """Design: §4 a NUDGE signal and synthesized verify blockers are surfaced to
        the remediation turn so it varies its approach and resolves the blocker.
    Implementation: pass nudge=True and one synthesized GapSummary; assert both
        appear in the composed prompt.
    Example: 'NUDGE' and the blocker title appear in the prompt.
    """
    runner = _CapturingRunner("plan")
    await run_remediation(
        runner,
        spec_text="s",
        plan_body="p",
        gaps=[_gap()],
        synthesized=[
            GapSummary(
                title="verification command failed",
                severity="high",
                design_doc_section="§6.5",
            )
        ],
        nudge=True,
        cwd=tmp_path,
    )
    assert "NUDGE" in runner.prompt
    assert "verification command failed" in runner.prompt
