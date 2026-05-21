"""§H3 pure non-progress / oscillation policy (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models import EvalGap

NON_PROGRESS_WINDOW = 2  # §H3 — stuck-window size.


def fingerprint_gaps(gaps: list[EvalGap]) -> frozenset[str]:
    """Reduce a gap set to an order-independent identity (§H3).

    Design: non-progress is the same gaps recurring, so the identity ignores
        ordering and transient prose while retaining severity.
    Implementation: return a frozenset of 'title|severity' strings.
    Example: fingerprint_gaps([g1, g2]) == fingerprint_gaps([g2, g1]).
    """
    return frozenset(f"{gap.title}|{gap.severity}" for gap in gaps)


@dataclass(frozen=True)
class NonProgressSignal:
    """The convergence policy verdict for one iteration (§H3).

    Design: three-valued escalation distinguishes progress, a pivot nudge, and
        an honest early stop for repeated unchanged gaps.
    Implementation: frozen dataclass with kind and human-readable reason.
    Example: NonProgressSignal(kind='break', reason='gap-set unchanged').
    """

    kind: Literal["none", "pivot", "break"]
    reason: str


def detect_non_progress(
    history: list[frozenset[str]], *, window: int = NON_PROGRESS_WINDOW
) -> NonProgressSignal:
    """Classify progress from fingerprint history (§H3).

    Design: deterministic conservative detection uses equality over trailing
        windows instead of fuzzy similarity, making early stops auditable.
    Implementation: 2*window equal non-empty fingerprints break; one window
        pivots; empty/latest-changing histories return none.
    Example: detect_non_progress([{a}, {a}], window=2).kind == 'pivot'.
    """
    if len(history) < window:
        return NonProgressSignal("none", "insufficient history")
    latest = history[-1]
    if not latest:
        return NonProgressSignal("none", "no gaps in latest iteration")
    if len(history) >= 2 * window and all(fp == latest for fp in history[-2 * window :]):
        return NonProgressSignal("break", f"gap-set unchanged for {2 * window} iterations")
    if all(fp == latest for fp in history[-window:]):
        return NonProgressSignal("pivot", f"gap-set unchanged for {window} iterations")
    return NonProgressSignal("none", "gap-set changing")
