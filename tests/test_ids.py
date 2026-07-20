# tests/test_ids.py
from __future__ import annotations

import time

from forge_mcp.ids import format_run_id, is_run_id


def test_is_run_id_accepts_second_precision_and_uniquifier():
    """Design: §12 run-id regex ^\\d{14}(-\\d{2})?$.
    Implementation: 14 digits, optional -NN.
    Example: is_run_id('20260623183102') is True.
    """
    assert is_run_id("20260623183102")
    assert is_run_id("20260623183102-01")
    assert not is_run_id("202606231831")  # minute precision rejected
    assert not is_run_id("deadbeef")  # old 8-hex shape rejected
    assert not is_run_id("20260623183102-1")  # uniquifier must be 2 digits


def test_format_run_id_round_trips():
    """Design: §12 the formatter and matcher agree.
    Implementation: format a known struct_time and re-match it.
    Example: format_run_id(struct) -> '20260623183102'.
    """
    st = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    assert format_run_id(st) == "20260623183102"
    assert format_run_id(st, uniquifier=1) == "20260623183102-01"
    assert is_run_id(format_run_id(st))
