"""§11.2 pure caps and overflow helpers."""

from __future__ import annotations

GAP_LIST_CAP = 50
WARNINGS_CAP = 25


def split_warnings(warnings: list[str]) -> tuple[list[str], list[str]]:
    """Return kept and dropped warning slices.

    Design: §11.2 caps warnings in RunResult while preserving overflow as a
        separate warning about truncation.
    Implementation: slice at WARNINGS_CAP and return both lists without IO.
    Example: split_warnings(['a'] * 30)[0] has length 25.
    """
    return warnings[:WARNINGS_CAP], warnings[WARNINGS_CAP:]


def build_gap_overflow(gaps: list, *, kind: str, total: int) -> str | None:
    """Return overflow file content (tail only) or None when within cap.

    Design: §11.2 places the kept head in RunResult and writes only dropped
        tail entries under a truncation header.
    Implementation: if total exceeds GAP_LIST_CAP, format titles for
        gaps[GAP_LIST_CAP:] into markdown content.
    Example: build_gap_overflow(gaps, kind='unresolved', total=51).
    """
    if total <= GAP_LIST_CAP:
        return None
    lines = [f"# {kind} gaps — truncated from {total} total", ""]
    for gap in gaps[GAP_LIST_CAP:]:
        nested = getattr(gap, "gap", None)
        title = getattr(gap, "title", None) or getattr(nested, "title", "")
        lines.append(f"- {title}")
    lines.append("")
    return "\n".join(lines)
