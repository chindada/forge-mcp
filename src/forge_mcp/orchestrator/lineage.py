"""§L cross-run learning — fingerprint, discovery, summary, digest.

This module is pure orchestration policy with small local I/O helpers. It does
NOT import drivers, engine, or SDK seams (§L9.1). Public functions carry the
Rule 21 three-section docstring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..artifacts import escape_md, escape_md_inline
from ..ids import is_run_id
from ..models import DesignFlawGap, EvalGap, EvalResult
from ..state import read_state

_TERMINAL_STATES = frozenset({"completed", "incomplete", "failed"})
_GAP_TITLE_MAX = 200
_PROSE_MAX = 500
_VERIFY_TAIL_MAX = 2000
_TOP_K_GAPS = 10
_TOP_K_FLAWS = 5
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_UNKNOWN_RANK = 3
_MAX_DIGEST_BYTES = 32_768
_MAX_PER_SUMMARY_BYTES = 8_192

_ANTI_ANCHORING_HEADER = """\
# Prior Attempts at This Design

This file is a digest of prior `run_forge` invocations on this exact
design doc (same SHA-256 fingerprint). Each entry below reached a
terminal state without converging sufficiently.

Treat the contents as evidence about what does NOT work, not as a
suggestion to refine. For each prior unresolved gap, propose a
DIFFERENT implementation strategy. Incremental refinement of a
multiply-failed approach is forbidden.
"""


@dataclass(frozen=True)
class LineageCandidate:
    """One eligible prior run located by the sibling scan (§L4).

    Design: §L3 eligibility rules are checked as the scan reads each prior
        run dir; LineageCandidate is the record the selector ranks by
        last_updated_at. Frozen so it is hashable and immutable.
    Implementation: dataclass with run_id, run_dir, last_updated_at; the scan
        returns list[LineageCandidate] pre-filtered, then sorts and truncates.
    Example: LineageCandidate(run_id="abcd1234", run_dir=Path("/r"),
        last_updated_at=datetime.now()).
    """

    run_id: str
    run_dir: Path
    last_updated_at: datetime


@dataclass(frozen=True)
class PriorRunSummary:
    """One prior run distilled into planner-facing fields (§L5).

    Design: §L5 selects high-signal fields from a prior terminal run while
        excluding full contracts and summaries. The renderer applies a per-run
        cap so one run cannot consume the digest budget.
    Implementation: frozen dataclass; summarize_prior_run pre-truncates gap
        fields and render_prior_attempts interpolates escaped markdown.
    Example: PriorRunSummary("abcd1234", "incomplete", None, 1, 60, [], [],
        None, None).
    """

    run_id: str
    status: Literal["completed", "incomplete", "failed"]
    reason: str | None
    iterations_used: int
    runtime_seconds: int
    unresolved_gaps: list[EvalGap]
    design_flaw_gaps: list[DesignFlawGap]
    verify_tail: str | None
    eval_summary: str | None


def fingerprint_design(text: str) -> str:
    """Return the canonical SHA-256 hex digest of a design doc (§L2).

    Design: §L2.1 canonicalization is minimal so meaningful edits invalidate
        lineage while cross-platform whitespace noise does not.
    Implementation: normalize CRLF/CR to LF, strip trailing whitespace per
        line, and hash the UTF-8 canonical string with SHA-256.
    Example: fingerprint_design("# h\n") returns a 64-character hex digest.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    canonical = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_lineage_runs(
    harness_dir: Path,
    fingerprint: str,
    *,
    current_run_id: str,
    top_k: int,
) -> list[LineageCandidate]:
    """Scan harness_dir for eligible prior runs, return top-K by recency (§L4).

    Design: §L3 eligibility (different run_id, fingerprint match, terminal
        state, not cancelled, not a symlink) is enforced here as the single
        authoritative selection gate. Cold-start fallback applies per candidate.
    Implementation: iterate run-id-shaped sibling dirs, reject symlinks before
        is_dir(), compare inputs/design.fingerprint, read state.json, filter
        terminal/non-cancelled, sort by last_updated_at descending, slice top_k.
    Example: find_lineage_runs(harness, fp, current_run_id="abcd1234", top_k=4).
    """
    if top_k <= 0:
        return []
    try:
        entries = list(harness_dir.iterdir())
    except OSError:
        return []
    candidates: list[LineageCandidate] = []
    for path in entries:
        if not is_run_id(path.name):
            continue
        try:
            if path.is_symlink() or not path.is_dir():
                continue
        except OSError:
            continue
        if path.name == current_run_id:
            continue
        try:
            prior_fp = (path / "inputs" / "design.fingerprint").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if prior_fp != fingerprint:
            continue
        try:
            state = read_state(path / "state.json")
        except Exception:
            continue
        if state.state not in _TERMINAL_STATES or state.cancelled:
            continue
        candidates.append(
            LineageCandidate(
                run_id=path.name,
                run_dir=path,
                last_updated_at=state.last_updated_at,
            )
        )
    candidates.sort(key=lambda candidate: candidate.last_updated_at, reverse=True)
    return candidates[:top_k]


def _truncate(value: str, limit: int) -> str:
    """Truncate value to limit chars, appending ellipsis on overflow (§L5.2).

    Design: §L5.2 bounds planner-facing prose so prior-run digests remain
        context-economical and predictable.
    Implementation: return the value unchanged within limit, otherwise reserve
        one character for the ellipsis so the returned string length is limit.
    Example: _truncate("abcd", 3) returns "ab…".
    """
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _select_top_gaps(gaps: list[EvalGap], k: int) -> list[EvalGap]:
    """Sort by severity rank then title and return top-k (§L5.2).

    Design: high severity evaluator gaps are most useful to the next planner;
        alphabetical tie-breaking keeps the digest deterministic.
    Implementation: sorted() with a small severity-rank map and fallback rank
        for unknown severities, then slice to k.
    Example: _select_top_gaps(gaps, 10) returns at most ten gaps.
    """
    return sorted(
        gaps,
        key=lambda gap: (_SEVERITY_RANK.get(gap.severity.lower(), _UNKNOWN_RANK), gap.title),
    )[:k]


def _truncate_gap(gap: EvalGap) -> EvalGap:
    """Return a copy of gap with planner-facing fields truncated (§L5.2).

    Design: each retained gap must stay bounded while preserving severity and
        section metadata for planner prioritization.
    Implementation: construct a fresh EvalGap with bounded title/current/
        expected/suggested_fix fields and copied metadata.
    Example: bounded = _truncate_gap(gap).
    """
    return EvalGap(
        title=_truncate(gap.title, _GAP_TITLE_MAX),
        severity=gap.severity,
        design_doc_section=gap.design_doc_section,
        current_state=_truncate(gap.current_state, _PROSE_MAX),
        expected_state=_truncate(gap.expected_state, _PROSE_MAX),
        suggested_fix=_truncate(gap.suggested_fix, _PROSE_MAX),
    )


def _select_top_design_flaws(flaws: list[DesignFlawGap], k: int) -> list[DesignFlawGap]:
    """Sort design flaws by severity then iteration and return top-k (§L5.2).

    Design: high-severity design flaws should be surfaced first because they can
        invalidate an otherwise reasonable implementation plan.
    Implementation: sort by nested gap severity rank and iteration_n ascending,
        then return the requested prefix.
    Example: _select_top_design_flaws(flaws, 5) returns at most five flaws.
    """
    return sorted(
        flaws,
        key=lambda flaw: (
            _SEVERITY_RANK.get(flaw.gap.severity.lower(), _UNKNOWN_RANK),
            flaw.iteration_n,
        ),
    )[:k]


def _highest_iteration_with_eval(run_dir: Path) -> int | None:
    """Find the highest-N iteration-N directory that has eval.json (§L5.2).

    Design: summaries should reflect the freshest evaluator result, matching the
        terminal unresolved-gaps behavior elsewhere in the orchestrator.
    Implementation: enumerate children, parse numeric iteration suffixes, keep
        those with eval.json, and return max or None.
    Example: _highest_iteration_with_eval(run_dir) returns 10 for iteration-10.
    """
    found: list[int] = []
    try:
        children = list(run_dir.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir() or not child.name.startswith("iteration-"):
            continue
        try:
            iteration_n = int(child.name.removeprefix("iteration-"))
        except ValueError:
            continue
        if (child / "eval.json").exists():
            found.append(iteration_n)
    return max(found) if found else None


def _read_design_flaws_sidecar(run_dir: Path) -> list[DesignFlawGap]:
    """Read design_flaws.json if present; return [] on any failure (§L8.4).

    Design: pre-§L runs lack the sidecar and malformed sidecars must not break
        cold-start fallback for otherwise useful prior runs.
    Implementation: parse the envelope shape {"gaps": [...]}; validate entries
        independently and skip malformed rows.
    Example: flaws = _read_design_flaws_sidecar(run_dir).
    """
    try:
        payload = json.loads((run_dir / "design_flaws.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_gaps = payload.get("gaps") if isinstance(payload, dict) else None
    if not isinstance(raw_gaps, list):
        return []
    out: list[DesignFlawGap] = []
    for raw in raw_gaps:
        try:
            out.append(DesignFlawGap.model_validate(raw))
        except Exception:
            continue
    return out


def summarize_prior_run(run_dir: Path) -> PriorRunSummary | None:
    """Read a prior run's terminal artifacts into a summary (§L5).

    Design: §L5 extracts only the compact planner-useful slice from a prior run;
        per-artifact failures degrade individual fields while unreadable state
        returns None for TOCTOU safety.
    Implementation: read state.json, compute runtime, parse latest eval.json,
        select/truncate gaps, read verify tail, read design_flaws.json sidecar,
        and return a frozen PriorRunSummary.
    Example: summary = summarize_prior_run(Path(".harness/abcd1234")).
    """
    try:
        state = read_state(run_dir / "state.json")
    except Exception:
        return None

    runtime_seconds = max(0, int((state.last_updated_at - state.started_at).total_seconds()))
    unresolved_gaps: list[EvalGap] = []
    eval_summary: str | None = None
    verify_tail: str | None = None
    latest_n = _highest_iteration_with_eval(run_dir)
    if latest_n is not None:
        iteration_dir = run_dir / f"iteration-{latest_n}"
        try:
            eval_result = EvalResult.model_validate_json(
                (iteration_dir / "eval.json").read_text(encoding="utf-8")
            )
            eval_summary = eval_result.summary
            unresolved_gaps = [
                _truncate_gap(gap) for gap in _select_top_gaps(eval_result.gaps, _TOP_K_GAPS)
            ]
        except (OSError, ValueError):
            pass
        try:
            text = (iteration_dir / "verify.txt").read_text(encoding="utf-8", errors="replace")
            verify_tail = text[-_VERIFY_TAIL_MAX:] if text else None
        except OSError:
            verify_tail = None

    design_flaws = _select_top_design_flaws(_read_design_flaws_sidecar(run_dir), _TOP_K_FLAWS)
    status: Literal["completed", "incomplete", "failed"] = state.state  # type: ignore[assignment]
    return PriorRunSummary(
        run_id=state.run_id,
        status=status,
        reason=state.reason,
        iterations_used=state.last_completed_iteration,
        runtime_seconds=runtime_seconds,
        unresolved_gaps=unresolved_gaps,
        design_flaw_gaps=design_flaws,
        verify_tail=verify_tail,
        eval_summary=eval_summary,
    )


def _render_one_summary(summary: PriorRunSummary) -> str:
    """Render one PriorRunSummary as a markdown section (§L6.1).

    Design: §L6.1 uses one section per prior run with optional subsections for
        evaluator summary, unresolved gaps, design flaws, and verify tail.
    Implementation: interpolate escaped markdown/table fields and omit empty
        subsections; verify tails are fenced with backtick-triplet escaping.
    Example: text = _render_one_summary(summary).
    """
    lines: list[str] = [f"## Run {escape_md_inline(summary.run_id)}", ""]
    lines.append(
        f"**status:** `{summary.status}` · **iterations:** {summary.iterations_used} · "
        f"**runtime:** {summary.runtime_seconds}s"
    )
    if summary.reason:
        lines.extend(["", f"**reason:** {escape_md_inline(summary.reason)}"])
    lines.append("")
    if summary.eval_summary:
        lines.extend(["### Final evaluator summary", "", escape_md(summary.eval_summary), ""])
    if summary.unresolved_gaps:
        lines.extend(
            [
                "### Unresolved gaps",
                "",
                "| Title | Severity | Section | Current | Expected | Suggested fix |",
                "|---|---|---|---|---|---|",
            ]
        )
        for gap in summary.unresolved_gaps:
            lines.append(
                "| "
                + " | ".join(
                    [
                        escape_md_inline(gap.title),
                        escape_md_inline(gap.severity),
                        escape_md_inline(gap.design_doc_section),
                        escape_md_inline(escape_md(gap.current_state)),
                        escape_md_inline(escape_md(gap.expected_state)),
                        escape_md_inline(escape_md(gap.suggested_fix)),
                    ]
                )
                + " |"
            )
        lines.append("")
    if summary.design_flaw_gaps:
        lines.extend(
            [
                "### Design-flaw gaps",
                "",
                "| Title | Fault kind | Iteration | Citation | Explanation |",
                "|---|---|---|---|---|",
            ]
        )
        for flaw in summary.design_flaw_gaps:
            lines.append(
                "| "
                + " | ".join(
                    [
                        escape_md_inline(flaw.gap.title),
                        escape_md_inline(flaw.fault_kind),
                        str(flaw.iteration_n),
                        escape_md_inline(escape_md("; ".join(flaw.cited_sections))),
                        escape_md_inline(escape_md(flaw.explanation)),
                    ]
                )
                + " |"
            )
        lines.append("")
    if summary.verify_tail:
        safe_tail = summary.verify_tail.replace("```", "``\u200b`")
        lines.extend(["### Verify command tail (last 2 KiB)", "", "```", safe_tail, "```", ""])
    return "\n".join(lines)


def _replace_summary(summary: PriorRunSummary, **changes: object) -> PriorRunSummary:
    """Return a copy of summary with named fields replaced (§L6.2).

    Design: PriorRunSummary is frozen, so cap enforcement needs immutable copy
        updates instead of in-place mutation.
    Implementation: delegate to dataclasses.replace with caller-supplied field
        overrides.
    Example: smaller = _replace_summary(summary, verify_tail=None).
    """
    return replace(summary, **changes)


def _shrink_summary(summary: PriorRunSummary) -> PriorRunSummary:
    """Apply the per-summary 8 KiB cap to one summary (§L-Decision 5).

    Design: no single prior run may consume more than a quarter of the digest
        budget, so pathological summaries are progressively shrunk.
    Implementation: measure rendered UTF-8 bytes, shorten verify tail, shorten
        gap prose, then drop trailing gaps until the cap is satisfied.
    Example: capped = _shrink_summary(summary).
    """

    def _measure(candidate: PriorRunSummary) -> int:
        """Measure rendered bytes for cap enforcement (§L6.2).

        Design: digest limits are byte limits because downstream context costs
            follow encoded size more closely than Python character counts.
        Implementation: render the candidate and count UTF-8 encoded bytes.
        Example: _measure(summary) <= _MAX_PER_SUMMARY_BYTES.
        """
        return len(_render_one_summary(candidate).encode("utf-8"))

    current = summary
    if _measure(current) > _MAX_PER_SUMMARY_BYTES and current.verify_tail:
        current = _replace_summary(current, verify_tail=current.verify_tail[-1000:])
    if _measure(current) > _MAX_PER_SUMMARY_BYTES and current.verify_tail:
        current = _replace_summary(current, verify_tail=current.verify_tail[-500:])
    if _measure(current) > _MAX_PER_SUMMARY_BYTES:
        current = _replace_summary(
            current,
            unresolved_gaps=[
                EvalGap(
                    title=gap.title,
                    severity=gap.severity,
                    design_doc_section=gap.design_doc_section,
                    current_state=_truncate(gap.current_state, 300),
                    expected_state=_truncate(gap.expected_state, 300),
                    suggested_fix=_truncate(gap.suggested_fix, 300),
                )
                for gap in current.unresolved_gaps
            ],
        )
    while _measure(current) > _MAX_PER_SUMMARY_BYTES and current.unresolved_gaps:
        current = _replace_summary(current, unresolved_gaps=current.unresolved_gaps[:-1])
    if _measure(current) > _MAX_PER_SUMMARY_BYTES and current.eval_summary:
        current = _replace_summary(current, eval_summary=_truncate(current.eval_summary, 500))
    if _measure(current) > _MAX_PER_SUMMARY_BYTES and current.reason:
        current = _replace_summary(current, reason=_truncate(current.reason, 100))
    return current


def render_prior_attempts(summaries: list[PriorRunSummary]) -> str:
    """Render prior-run summaries as one markdown digest (§L6).

    Design: the anti-anchoring header must appear first so the planner treats
        prior attempts as evidence of what not to repeat, not as an anchor.
    Implementation: empty input returns ""; otherwise concatenate the header
        and each shrunk/escaped summary with stable input ordering.
    Example: markdown = render_prior_attempts([summary1, summary2]).
    """
    if not summaries:
        return ""
    parts = [_ANTI_ANCHORING_HEADER]
    parts.extend(_render_one_summary(_shrink_summary(summary)) for summary in summaries)
    return "\n".join(parts)
