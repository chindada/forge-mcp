from __future__ import annotations

import json

from forge_mcp.artifacts import RunLayout
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.orchestrator.amend import apply_amendments

SPEC = "Alpha section. The widget must flush before close. Omega section."


def _triage(before, after, cited="The widget must flush before close"):
    """Build a design-fault GapTriage with a ProposedAmendment for test use.

    Design: §4 tests need a consistent triage fixture with a valid cited section
        for the in-loop single-row amendment signature.
    Implementation: hard-code gap_title, fault_kind; parameterise before/after so
        each test can supply the relevant amendment text, and cited_sections so
        each row can cite text appropriate to its position in the amendment sequence.
    Example: _triage('old text', 'new text') returns a valid design-fault GapTriage;
        _triage('old', 'new', cited='other text') cites 'other text' instead.
    """
    return GapTriage(
        gap_title="g",
        design_fault=True,
        fault_kind="contradiction",
        cited_sections=[cited],
        explanation="e",
        proposed_amendment=ProposedAmendment(
            cited_sections=[cited],
            before=before,
            after=after,
            rationale="r",
        ),
    )


def _seed(tmp_path):
    """Create a RunLayout with an empty spec_amendments.md log.

    Design: §4/§10 amend writes the run-level spec_amendments.md; tests seed it
        empty so each appended entry is observable.
    Implementation: build RunLayout.for_run(tmp_path), make its parent dir, write
        an empty amendments file, and return the layout.
    Example: lay = _seed(tmp_path); lay.spec_amendments.read_text() == ''.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    return lay


def test_valid_amendment_applies_bumps_fingerprint_appends_log(tmp_path):
    """Design: §4 a cited, present-before amendment applies, bumps fp, logs.
    Implementation: apply one valid amendment passed as a bare GapTriage row.
    Example: spec text changed; spec_amendments.md non-empty.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("flush before close", "flush and fsync before close")],
        now="t",
    )
    assert "fsync" in out.new_spec and out.new_fingerprint != "fp0"
    assert len(out.applied) == 1 and lay.spec_amendments.read_text() != ""


def test_amendment_log_entry_has_no_plan_id(tmp_path):
    """Design: §11 the spec_amendments.md entry drops plan_id (there is no plan id).
    Implementation: apply a valid amendment, parse the appended JSON line, assert the
        keys are exactly the surviving structured fields and 'plan_id' is absent.
    Example: json.loads(entry).keys() does not contain 'plan_id'.
    """
    lay = _seed(tmp_path)
    apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("flush before close", "flush and fsync before close")],
        now="t",
    )
    line = lay.spec_amendments.read_text().strip()
    entry = json.loads(line)
    assert "plan_id" not in entry
    assert set(entry) == {
        "now",
        "fault_kind",
        "cited_sections",
        "before",
        "after",
        "rationale",
    }
    assert entry["now"] == "t" and entry["fault_kind"] == "contradiction"


def test_amendment_with_absent_before_is_rejected(tmp_path):
    """Design: §4 a 'before' no longer verbatim is rejected (demote).
    Implementation: before text not in spec; passed as a bare GapTriage row.
    Example: rejected list has the proposal; spec unchanged.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("nonexistent target text", "x")],
        now="t",
    )
    assert out.applied == [] and len(out.rejected) == 1 and out.new_spec == SPEC


def test_rejected_amendment_still_counts_toward_churn(tmp_path):
    """Design: §4/§6.7 rejected proposals still feed amendment churn.
    Implementation: a rejected proposal yields a non-empty churn fingerprint.
    Example: churn_fingerprint non-empty.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("nonexistent target text", "x")],
        now="t",
    )
    assert len(out.churn_fingerprint) >= 1


def test_serial_amendments_apply_against_running_spec(tmp_path):
    """Design: §4 amendments apply serially so a later row sees the earlier edit.
    Implementation: row1 inserts 'fsync', row2 cites text only present after row1;
        both apply and the final spec carries both edits.
    Example: new_spec contains both 'fsync' and 'flushed-and-synced'.
    """
    lay = _seed(tmp_path)
    row1 = _triage("flush before close", "flush and fsync before close")
    row2 = _triage(
        "flush and fsync before close",
        "flushed-and-synced before close",
        cited="The widget must flush and fsync before close",
    )
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[row1, row2],
        now="t",
    )
    assert len(out.applied) == 2
    assert "flushed-and-synced" in out.new_spec and "flush before close" not in out.new_spec
