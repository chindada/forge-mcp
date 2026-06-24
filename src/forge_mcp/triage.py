from __future__ import annotations

from forge_mcp.models import EvalGap, GapTriage

CITATION_MIN_CHARS: int = 20


def is_valid_citation(text: str, spec: str, *, min_chars: int = CITATION_MIN_CHARS) -> bool:
    """Return True iff text is a verbatim substring of spec and meets the minimum length.

    Design: §5.3 cited sections must be long enough to be meaningful and must
        literally appear in the spec so they cannot be fabricated or paraphrased.
    Implementation: strip leading/trailing whitespace before length check; do
        not strip before the substring check so the caller controls what is
        actually cited.
    Example: is_valid_citation('The orchestrator unions each completed plan', spec)
        returns True when that exact phrase appears in spec.
    """
    return len(text.strip()) >= min_chars and text in spec


def passes_citation_gate(triage: GapTriage, spec: str) -> bool:
    """Return True iff the triage row is a design fault with all valid citations.

    Design: §5.3 a triage row can demote a gap from code-bug to design-fault
        only when the evidence is anchored to the spec via verbatim citations.
    Implementation: require design_fault True, fault_kind set (non-None), and
        every cited_sections entry to pass is_valid_citation; an empty
        cited_sections list vacuously fails because the model validator already
        enforces at least one entry when design_fault is True.
    Example: passes_citation_gate(triage_with_valid_citation, spec) returns True.
    """
    if not triage.design_fault or triage.fault_kind is None:
        return False
    return all(is_valid_citation(c, spec) for c in triage.cited_sections)


def effective_code_bug_titles(
    gaps: list[EvalGap],
    triages: list[GapTriage],
    spec: str,
) -> set[str]:
    """Return the set of gap titles that are non-demotable code bugs.

    Design: §5.3/§6.5 a gap is a code bug unless it has a triage row that
        passes the citation gate; gaps with no triage row remain code bugs.
    Implementation: build a lookup from whitespace-canonicalized triage title
        to triage row, then include each gap whose triage either is absent or
        does not pass the citation gate. Title canonicalization uses
        ' '.join(t.split()) to match EvalGap._canonicalize_title.
    Example: a gap titled 'spec is wrong' with a validated design-fault triage
        is excluded; 'real bug' with no triage row is included.
    """
    triage_by_title: dict[str, GapTriage] = {" ".join(t.gap_title.split()): t for t in triages}
    result: set[str] = set()
    for g in gaps:
        triage = triage_by_title.get(g.title)
        if triage is None or not passes_citation_gate(triage, spec):
            result.add(g.title)
    return result


def amendment_target_present(before: str, spec: str) -> bool:
    """Return True iff before is a verbatim substring of spec.

    Design: §5.3 step 2 the orchestrator must re-verify that the AMEND target
        text still exists in the current spec before applying an amendment.
    Implementation: simple substring check with no length restriction because
        the orchestrator is checking exact text it already has.
    Example: amendment_target_present('The orchestrator unions', spec) returns True.
    """
    return before in spec
