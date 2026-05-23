"""§X1 cross-design-doc pattern aggregation. Pure-policy, disk-read-only."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..ids import is_run_id
from ..state import read_state

_LOG = logging.getLogger("forge_mcp.cross_design")
_TERMINAL_STATES = frozenset({"completed", "incomplete", "failed"})
_RECENCY_WINDOW = timedelta(days=30)
_FREQUENCY_FLOOR = 3
_PER_PATTERN_BYTES_CAP = 1024
_TOTAL_DIGEST_BYTES_CAP = 8 * 1024

_DIGEST_HEADER = """# Cross-design patterns (advisory)

These patterns have been observed across **multiple distinct design
documents** in this harness over the last 30 days. They are
**statistical priors**, not requirements for the current design.
Treat each pattern as a *weak signal* — check whether your current
design's requirements actually suggest this pattern is relevant.
**If a pattern doesn't apply, IGNORE IT.** The sibling-run summary in
`prior_attempts.md` (when present) is a stronger signal than anything
here, and conflicts must resolve in favor of sibling-run guidance.
"""


@dataclass(frozen=True)
class CrossPattern:
    """One gated cross-design pattern surfaced to the planner (§X1).

    Design: the planner receives only weak priors aggregated across distinct
        design fingerprints, never raw prior run context from unrelated designs.
    Implementation: store the fault kind, distinct fingerprint count, newest
        observation time, and a bounded markdown summary body.
    Example: CrossPattern('missing_validation', 3, datetime.now(UTC), '...').
    """

    fault_kind: str
    distinct_fingerprints: int
    last_seen: datetime
    rendered_summary: str


@dataclass(frozen=True)
class _Hit:
    """One terminal-run occurrence used inside aggregation buckets (§X2).

    Design: private hit records keep bucketing independent of the rendered
        digest shape and preserve the distinct-fingerprint gate inputs.
    Implementation: immutable dataclass with fault kind, fingerprint, observed
        timestamp, and optional explanatory text.
    Example: _Hit('k', 'f' * 64, datetime.now(UTC), 'summary').
    """

    fault_kind: str
    fingerprint: str
    observed_at: datetime
    explanation: str


def _parse_datetime(value: object) -> datetime:
    """Parse a state timestamp into an aware UTC datetime (§X2).

    Design: malformed or missing timestamps make a run ambiguous and therefore
        ineligible under the conservative gate.
    Implementation: accept datetime values or ISO strings, normalizing trailing
        Z to +00:00 and naive values to UTC.
    Example: _parse_datetime('2026-05-22T00:00:00Z').tzinfo is not None.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("missing datetime")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _fault_kind_from(row: object) -> tuple[str, str] | None:
    """Extract post-classified fault_kind and prose from one sidecar row (§X1).

    Design: only the run-root design_flaws.json sidecar contributes; rows that
        lack a clear fault_kind are skipped rather than guessed.
    Implementation: support the existing {gaps:[...]} envelope and simple test
        rows while using explanation/current fields only as summary prose.
    Example: _fault_kind_from({'fault_kind': 'k'}) returns ('k', '').
    """
    if not isinstance(row, dict):
        return None
    kind = row.get("fault_kind")
    if not isinstance(kind, str) or not kind.strip():
        return None
    parts = []
    explanation = row.get("explanation")
    if isinstance(explanation, str) and explanation.strip():
        parts.append(explanation.strip())
    gap = row.get("gap")
    if isinstance(gap, dict):
        title = gap.get("title")
        if isinstance(title, str) and title.strip():
            parts.append(f"Example gap: {title.strip()}")
    return kind.strip(), " ".join(parts)


def _hits_for_run(state_path: Path) -> list[_Hit]:
    """Return eligible fault-kind hits for one run directory (§X2).

    Design: reuse the §L terminal and not-cancelled filter, then read the
        post-classified design_flaws.json sidecar only.
    Implementation: validate state.json through RunState, parse fingerprint
        from inputs/design.fingerprint, and parse the sidecar envelope.
    Example: hits = _hits_for_run(Path('.harness/abcd1234/state.json')).
    """
    run_dir = state_path.parent
    state = read_state(state_path)
    if state.state not in _TERMINAL_STATES or state.cancelled:
        return []
    fingerprint = (run_dir / "inputs" / "design.fingerprint").read_text(encoding="utf-8").strip()
    if not fingerprint:
        raise ValueError("missing fingerprint")
    observed_at = _parse_datetime(state.last_updated_at)
    data = json.loads((run_dir / "design_flaws.json").read_text(encoding="utf-8"))
    rows = data.get("gaps") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("bad design_flaws shape")
    hits: list[_Hit] = []
    for row in rows:
        extracted = _fault_kind_from(row)
        if extracted is None:
            continue
        kind, explanation = extracted
        hits.append(_Hit(kind, fingerprint, observed_at, explanation))
    return hits


def _summarize(fault_kind: str, hits: list[_Hit]) -> str:
    """Render a small summary body for one aggregated fault kind (§X3).

    Design: the digest should remind the planner of observed pattern classes
        without importing unrelated design details wholesale.
    Implementation: include the kind and up to three example explanations from
        recent hits, preserving deterministic insertion order.
    Example: _summarize('k', hits).startswith('Repeated fault kind').
    """
    examples = []
    for hit in hits:
        if hit.explanation and hit.explanation not in examples:
            examples.append(hit.explanation)
        if len(examples) == 3:
            break
    lines = [f"Repeated fault kind `{fault_kind}` appeared in prior terminal runs."]
    if examples:
        lines.append("")
        lines.extend(f"- {example}" for example in examples)
    return "\n".join(lines)


def find_cross_design_patterns(harness_dir: Path) -> list[CrossPattern]:
    """Aggregate eligible design-flaw sidecars across a harness (§X1/§X2).

    Design: cross-design learning is read-only and conservative: malformed
        runs are skipped with a warning, and only fault kinds seen across at
        least three distinct recent design fingerprints surface.
    Implementation: bucket _Hit records by fault_kind, apply the 30-day recency
        and distinct-fingerprint floor, then sort newest first.
    Example: patterns = find_cross_design_patterns(Path('.harness')).
    """
    now = datetime.now(UTC)
    buckets: dict[str, list[_Hit]] = defaultdict(list)
    for state_path in harness_dir.glob("*/state.json"):
        if not is_run_id(state_path.parent.name):
            continue  # finding 7 — defensive shape check on the */state.json glob
        try:
            for hit in _hits_for_run(state_path):
                buckets[hit.fault_kind].append(hit)
        except Exception:  # noqa: BLE001  # §X-Inv 1 conservative skip
            _LOG.warning("skipping malformed cross-design run %s", state_path, exc_info=True)
    patterns: list[CrossPattern] = []
    cutoff = now - _RECENCY_WINDOW
    for fault_kind, hits in buckets.items():
        recent = [hit for hit in hits if hit.observed_at >= cutoff]
        fingerprints = {hit.fingerprint for hit in recent}
        if len(fingerprints) < _FREQUENCY_FLOOR:
            continue
        patterns.append(
            CrossPattern(
                fault_kind=fault_kind,
                distinct_fingerprints=len(fingerprints),
                last_seen=max(hit.observed_at for hit in recent),
                rendered_summary=_summarize(fault_kind, recent),
            )
        )
    return sorted(patterns, key=lambda pattern: pattern.last_seen, reverse=True)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Truncate UTF-8 text to max_bytes with a marker (§X3).

    Design: context budgets are byte-oriented, and truncation must avoid
        splitting multibyte characters into invalid UTF-8.
    Implementation: slice encoded bytes, decode with ignore, and append marker
        when the original exceeded the cap.
    Example: len(_truncate_bytes('x' * 2000, 10).encode()) > 10 due marker.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n…[truncated]"


def render_digest(
    patterns: list[CrossPattern],
    *,
    max_total_bytes: int = _TOTAL_DIGEST_BYTES_CAP,
    max_per_pattern_bytes: int = _PER_PATTERN_BYTES_CAP,
) -> str:
    """Render the planner-facing cross-design markdown digest (§X3).

    Design: the verbatim anti-anchoring header appears even when no patterns
        qualify so the planner input shape is stable and advisory framing stays
        redundant with the system prompt.
    Implementation: append newest patterns first, cap each body, and stop before
        the total UTF-8 byte cap would be exceeded.
    Example: render_digest([]).startswith('# Cross-design patterns').
    """
    output = [_DIGEST_HEADER]
    total = len(_DIGEST_HEADER.encode("utf-8"))
    for pattern in sorted(patterns, key=lambda item: item.last_seen, reverse=True):
        summary = _truncate_bytes(pattern.rendered_summary, max_per_pattern_bytes)
        block = (
            f"\n## Pattern: {pattern.fault_kind}\n\n"
            f"ADVISORY ONLY — observed in {pattern.distinct_fingerprints} distinct designs "
            f"in the last 30 days; last seen {pattern.last_seen.date().isoformat()}.\n\n"
            f"{summary}\n"
        )
        block_bytes = len(block.encode("utf-8"))
        if total + block_bytes > max_total_bytes:
            break
        output.append(block)
        total += block_bytes
    return "".join(output)


def render_cross_design_digest(harness_dir: Path, logger: logging.Logger) -> str:
    """Build a digest for planner cold-start, failing soft (§X7).

    Design: §X-Inv 6 says inputs/cross_design_patterns.md is always written;
        aggregation failures are advisory-side-channel failures and produce a
        header-only digest instead of failing the run.
    Implementation: catch all exceptions around find_cross_design_patterns,
        log a warning, and render an empty pattern list.
    Example: digest = render_cross_design_digest(Path('.harness'), logger).
    """
    try:
        patterns = find_cross_design_patterns(harness_dir)
    except Exception:  # noqa: BLE001  # §X-Decision 9 fail-soft
        logger.warning("cross-design aggregation failed; writing empty digest", exc_info=True)
        patterns = []
    return render_digest(patterns)
