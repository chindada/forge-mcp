from __future__ import annotations

from forge_mcp.models import EvalGap, GapTriage
from forge_mcp.triage import (
    effective_code_bug_titles,
    is_valid_citation,
    passes_citation_gate,
)

SPEC = "## §7.4 Merge\nThe orchestrator unions each completed plan's change-set into target_dir.\n"


def gap(title):
    """Create a minimal EvalGap fixture with the given title.

    Design: §5.3 gaps are identified by their whitespace-canonicalized title.
    Implementation: fills required fields with sentinel values so tests focus on title.
    Example: gap('real bug') returns EvalGap with title='real bug'.
    """
    return EvalGap(
        title=title,
        severity="high",
        design_doc_section="§7.4",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )


def test_citation_gate_requires_long_verbatim_substring():
    """Design: §5.3 cited text must be a >=20-char verbatim substring of spec.
    Implementation: a real long substring passes; a short/absent one fails.
    Example: is_valid_citation('The orchestrator unions each completed plan', SPEC).
    """
    assert is_valid_citation("The orchestrator unions each completed plan", SPEC)
    assert not is_valid_citation("§7.4", SPEC)  # too short
    assert not is_valid_citation("a 30 character non-substring!!", SPEC)  # not in spec


def test_design_fault_passes_only_with_valid_citations():
    """Design: §5.3 design_fault rows demote unless every citation is valid.
    Implementation: valid vs invalid citation.
    Example: passes_citation_gate True only when verbatim.
    """
    good = GapTriage(
        gap_title="g",
        design_fault=True,
        fault_kind="contradiction",
        cited_sections=["The orchestrator unions each completed plan"],
        explanation="x",
    )
    bad = GapTriage(
        gap_title="g",
        design_fault=True,
        fault_kind="contradiction",
        cited_sections=["nonexistent verbatim text here!!"],
        explanation="x",
    )
    assert passes_citation_gate(good, SPEC)
    assert not passes_citation_gate(bad, SPEC)


def test_effective_code_bugs_excludes_validated_design_faults():
    """Design: §6.5 count only gaps whose triage is NOT a validated design fault.
    Implementation: one validated fault (excluded), one untriaged gap (counted).
    Example: only the untriaged title remains.
    """
    gaps = [gap("real bug"), gap("spec is wrong")]
    triages = [
        GapTriage(
            gap_title="spec is wrong",
            design_fault=True,
            fault_kind="contradiction",
            cited_sections=["The orchestrator unions each completed plan"],
            explanation="x",
        )
    ]
    assert effective_code_bug_titles(gaps, triages, SPEC) == {"real bug"}
