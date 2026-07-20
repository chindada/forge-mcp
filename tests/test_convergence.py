from __future__ import annotations

from forge_mcp.convergence import detect_non_progress, fingerprint


def fp(*xs):
    """Shorthand for fingerprint.

    Design: reduce test boilerplate.
    Implementation: call fingerprint(*xs).
    Example: fp('a', 'b') == fingerprint(['a', 'b']).
    """
    return fingerprint(xs)


def test_short_history_is_none():
    """Design: §6.7 history shorter than window -> none.
    Implementation: one entry, window 2.
    Example: detect_non_progress([fp('a')]) == 'none'.
    """
    assert detect_non_progress([fp("a")]) == "none"


def test_empty_latest_is_none():
    """Design: §6.7 an empty latest fingerprint never stops (no gaps = progress).
    Implementation: latest is empty frozenset.
    Example: returns 'none'.
    """
    assert detect_non_progress([fp("a"), fingerprint([])]) == "none"


def test_window_stable_is_nudge():
    """Design: §6.7 last `window` identical (but < 2*window) -> NUDGE.
    Implementation: 2 identical non-empty fingerprints, window 2.
    Example: returns 'NUDGE'.
    """
    assert detect_non_progress([fp("a"), fp("a")]) == "NUDGE"


def test_double_window_stable_is_early_stop():
    """Design: §6.7 last 2*window identical -> EARLY_STOP (honest stop).
    Implementation: 4 identical fingerprints, window 2.
    Example: returns 'EARLY_STOP'.
    """
    assert detect_non_progress([fp("a")] * 4) == "EARLY_STOP"


def test_changing_set_is_none():
    """Design: §6.7 a shrinking/changing gap-set is progress.
    Implementation: differing fingerprints -> none.
    Example: returns 'none'.
    """
    assert detect_non_progress([fp("a", "b"), fp("a")]) == "none"


def test_order_independent_fingerprint():
    """Design: §6.7 fingerprint ignores order.
    Implementation: same members different order compare equal.
    Example: fp('a','b') == fp('b','a').
    """
    assert fp("a", "b") == fp("b", "a")
