"""Run-id / timestamp shape — the single source of the run-dir naming format (§12)."""

from __future__ import annotations

import re
import time

RUN_ID_RE = re.compile(r"^\d{14}(-\d{2})?$")


def is_run_id(name: str) -> bool:
    """Return True if `name` is a valid second-precision run-id (§12).

    Design: §12 every run-id consumer (matcher, run_dir field, lock payload,
        prune filter) must share one shape; a mismatched filter silently never
        prunes. This regex is that shape.
    Implementation: full-match `^\\d{14}(-\\d{2})?$`.
    Example: is_run_id('20260623183102') returns True.
    """
    return bool(RUN_ID_RE.match(name))


def format_run_id(when: time.struct_time, *, uniquifier: int | None = None) -> str:
    """Format a time value as `YYYYMMDDHHMMSS[-NN]` (§12).

    Design: §12 second precision reduces collisions vs the example's minute
        precision; the source time is injected (not read here) so the module
        is pure and the matcher/formatter cannot drift.
    Implementation: strftime the 14-digit stamp; append a zero-padded 2-digit
        uniquifier on a same-second re-run.
    Example: format_run_id(struct_time(...2,18,31,2...)) -> '20260623183102'.
    """
    base = time.strftime("%Y%m%d%H%M%S", when)
    return f"{base}-{uniquifier:02d}" if uniquifier is not None else base
