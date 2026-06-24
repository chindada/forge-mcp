from __future__ import annotations

from forge_mcp.artifacts import RunLayout
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.orchestrator.amend import apply_amendments

SPEC = "Alpha section. The widget must flush before close. Omega section."


def _triage(before, after):
    """Build a design-fault GapTriage with a ProposedAmendment for test use.

    Design: §5.3 tests need a consistent triage fixture with a valid cited section.
    Implementation: hard-code gap_title, fault_kind, cited_sections; parameterise
        before/after so each test can supply the relevant amendment text.
    Example: _triage('old text', 'new text') returns a valid design-fault GapTriage.
    """
    return GapTriage(
        gap_title="g",
        design_fault=True,
        fault_kind="contradiction",
        cited_sections=["The widget must flush before close"],
        explanation="e",
        proposed_amendment=ProposedAmendment(
            cited_sections=["The widget must flush before close"],
            before=before,
            after=after,
            rationale="r",
        ),
    )


def test_valid_amendment_applies_bumps_fingerprint_appends_log(tmp_path):
    """Design: §5.3 a cited, present-before amendment applies, bumps fp, logs.
    Implementation: apply one valid amendment.
    Example: spec text changed; spec_amendments.md non-empty.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[("p1", _triage("flush before close", "flush and fsync before close"))],
        now="t",
    )
    assert "fsync" in out.new_spec and out.new_fingerprint != "fp0"
    assert len(out.applied) == 1 and lay.spec_amendments.read_text() != ""


def test_amendment_with_absent_before_is_rejected(tmp_path):
    """Design: §5.3 step 2 a 'before' no longer verbatim is rejected (demote).
    Implementation: before text not in spec.
    Example: rejected list has the proposal; spec unchanged.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[("p1", _triage("nonexistent target text", "x"))],
        now="t",
    )
    assert out.applied == [] and len(out.rejected) == 1 and out.new_spec == SPEC


def test_rejected_amendment_still_counts_toward_churn(tmp_path):
    """Design: §6.7 rejected proposals still feed amendment churn.
    Implementation: a rejected proposal yields a non-empty churn fingerprint.
    Example: churn_fingerprint non-empty.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[("p1", _triage("nonexistent target text", "x"))],
        now="t",
    )
    assert len(out.churn_fingerprint) >= 1
