"""§H6 pure prose-handoff validation (sibling of triage; no I/O)."""

from __future__ import annotations

MIN_CONTRACT_CHARS = 80
MIN_PROSE_CHARS = 10
_ACTIONABLE_SIGNALS = (
    "accept",
    "verify",
    "test",
    "implement",
    "must",
    "should",
    "criteria",
    "step",
)


def _has_heading(text: str) -> bool:
    """Return True iff any line is a markdown heading (§H6).

    Design: well-formed handoffs have visible structure; no heading is a strong
        degeneracy signal for prose artifacts.
    Implementation: scan stripped lines for a leading '#'.
    Example: _has_heading('# Title') is True.
    """
    return any(line.lstrip().startswith("#") for line in text.splitlines())


def validate_contract(text: str) -> list[str]:
    """Return well-formedness problems for a contract (§H6, empty=ok).

    Design: catches silent propagation of degenerate contracts without imposing
        a machine schema on deliberately prose handoff artifacts.
    Implementation: flag empty, too short, missing heading, or lacking any
        actionable/acceptance keyword.
    Example: validate_contract('') == ['contract is empty'].
    """
    stripped = text.strip()
    if not stripped:
        return ["contract is empty"]
    problems: list[str] = []
    if len(stripped) < MIN_CONTRACT_CHARS:
        problems.append(f"contract is too short ({len(stripped)} < {MIN_CONTRACT_CHARS} chars)")
    if not _has_heading(text):
        problems.append("contract has no markdown heading")
    if not any(signal in stripped.lower() for signal in _ACTIONABLE_SIGNALS):
        problems.append("contract lacks any actionable/acceptance signal")
    return problems


def validate_plan(text: str) -> list[str]:
    """Return well-formedness problems for a plan (§H6, empty=ok).

    Design: plan validation is lighter because iteration one evaluation catches
        substantive weakness; only degeneracy is flagged.
    Implementation: flag empty/short text and missing markdown heading.
    Example: validate_plan('# Plan\nbuild it thoroughly') == [].
    """
    stripped = text.strip()
    problems: list[str] = []
    if len(stripped) < MIN_PROSE_CHARS:
        problems.append("plan is empty or too short")
    if not _has_heading(text):
        problems.append("plan has no markdown heading")
    return problems


def validate_summary(text: str) -> list[str]:
    """Return well-formedness problems for a summary (§H6, empty=ok).

    Design: summary validation is informational, so only clearly useless prose
        is flagged for forensics.
    Implementation: flag empty/short text and missing markdown heading.
    Example: validate_summary('# Summary\nall done') == [].
    """
    stripped = text.strip()
    problems: list[str] = []
    if len(stripped) < MIN_PROSE_CHARS:
        problems.append("summary is empty or too short")
    if not _has_heading(text):
        problems.append("summary has no markdown heading")
    return problems
