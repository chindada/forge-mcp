"""Pure oscillation / non-progress policy (no I/O), two scopes (§6.7)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Signal = Literal["none", "NUDGE", "EARLY_STOP"]


def fingerprint(items: Iterable[str]) -> frozenset[str]:
    """Reduce items to an order-independent fingerprint (§6.7).

    Design: §6.7 a stable, order-independent signature lets the detector compare
        successive gap/conflict/amendment sets while ignoring prose and ordering.
    Implementation: collect the stable strings into a frozenset.
    Example: fingerprint(['high|x', 'low|y']) == fingerprint(['low|y', 'high|x']).
    """
    return frozenset(items)


def detect_non_progress(history: list[frozenset[str]], window: int = 2) -> Signal:
    """Classify a fingerprint history as none / NUDGE / EARLY_STOP (§6.7).

    Design: §6.7 distinguishes an honest early stop (a stuck set repeated over a
        long window) from a one-off nudge, removing any incentive to burn the
        budget to the cap. `window` is a tunable policy constant.
    Implementation: too-short or empty-latest -> none; last 2*window all equal the
        latest -> EARLY_STOP; last window all equal -> NUDGE; else none.
    Example: detect_non_progress([fp]*4) returns 'EARLY_STOP'.
    """
    if len(history) < window:
        return "none"
    latest = history[-1]
    if not latest:
        return "none"
    if len(history) >= 2 * window and all(f == latest for f in history[-2 * window :]):
        return "EARLY_STOP"
    if all(f == latest for f in history[-window:]):
        return "NUDGE"
    return "none"
