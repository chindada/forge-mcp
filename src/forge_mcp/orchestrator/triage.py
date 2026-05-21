"""§11.1 pure triage classification and citation validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import DesignFlawGap, EvalGap, EvalResult, TriageResult

_WS = re.compile(r"\s+")
_MIN_CITATION_CHARS = 20
_MAX_CITATION_SCAN_CHARS = 1024 * 1024


@dataclass(frozen=True)
class TriageOutcome:
    """Result of classify_gaps (§11.1).

    Design: the iteration loop needs promoted design flaws, remaining code-bug
        gaps, and warnings from one pure policy decision.
    Implementation: frozen dataclass with list fields supplied by the builder.
    Example: TriageOutcome(design_flaws=[], code_bug_gaps=[], warnings=[]).
    """

    design_flaws: list[DesignFlawGap] = field(default_factory=list)
    code_bug_gaps: list[EvalGap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def canonicalize_for_citation(text: str) -> str:
    """Collapse whitespace for verbatim citation matching.

    Design: §11.1 allows whitespace-canonicalized substring checks, not fuzzy
        semantic matches.
    Implementation: replace runs of whitespace with a single space and strip.
    Example: canonicalize_for_citation('a\n b') == 'a b'.
    """
    return _WS.sub(" ", text).strip()


def validate_citations(cited: list[str], canon: str) -> bool:
    """Return True iff every citation is long and verbatim.

    Design: §11.1 accepts design flaws only with every cited_sections entry at
        least 20 characters and present in inputs/design.md.
    Implementation: canonicalize each citation and check length/substr.
    Example: validate_citations(['long exact text'], canon).
    """
    if not cited:
        return False
    for entry in cited:
        normalized = canonicalize_for_citation(entry)
        if len(normalized) < _MIN_CITATION_CHARS or normalized not in canon:
            return False
    return True


def classify_gaps(
    eval_result: EvalResult,
    triage: TriageResult,
    canonical_design_doc: str,
    iteration_n: int,
) -> TriageOutcome:
    """Pair triage rows to gaps; promote strict design flaws; demote else.

    Design: §11.1 is conservative: title collisions, unmatched rows, and weak
        citations all remain code bugs; this is the sole owner of coercion logs.
    Implementation: index gaps by title, validate one row per unique title, and
        construct DesignFlawGap only after citation acceptance.
    Example: outcome = classify_gaps(er, tr, design_text, 1).
    """
    canon = canonicalize_for_citation(canonical_design_doc)
    warnings: list[str] = []
    if len(canon) > _MAX_CITATION_SCAN_CHARS:
        warnings.append(
            f"design doc citation scan over {_MAX_CITATION_SCAN_CHARS} chars ({len(canon)}); "
            "checks still run"
        )
    by_title: dict[str, list[EvalGap]] = {}
    for gap in eval_result.gaps:
        by_title.setdefault(gap.title, []).append(gap)
    triage_by_title = {row.gap_title: row for row in triage.triages}
    design_flaws: list[DesignFlawGap] = []
    code_bug_gaps: list[EvalGap] = []
    for row in triage.triages:
        for entry in row._coercion_log:
            warnings.append(f"triage drift on '{row.gap_title}': {entry}")
    for title, gaps in by_title.items():
        if len(gaps) > 1:
            warnings.append(f"title collision on '{title}' (x{len(gaps)}) — demoting all")
            code_bug_gaps.extend(gaps)
            continue
        gap = gaps[0]
        row = triage_by_title.get(title)
        if row is None or not row.design_fault:
            code_bug_gaps.append(gap)
            continue
        if not validate_citations(row.cited_sections, canon):
            warnings.append(f"weak citation on '{title}' — demoting to code-bug")
            code_bug_gaps.append(gap)
            continue
        if row.fault_kind is None:
            warnings.append(f"missing fault kind on '{title}' — demoting to code-bug")
            code_bug_gaps.append(gap)
            continue
        design_flaws.append(
            DesignFlawGap(
                gap=gap,
                iteration_n=iteration_n,
                fault_kind=row.fault_kind,
                cited_sections=row.cited_sections,
                explanation=row.explanation,
            )
        )
    return TriageOutcome(design_flaws=design_flaws, code_bug_gaps=code_bug_gaps, warnings=warnings)
