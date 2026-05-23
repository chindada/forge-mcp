"""Canonical run-id matcher — the single source of truth for run-id shape.

This module is a stdlib-only leaf: it imports only `re` so the pinned
stdlib-only resources.py leaf (§R5.1 / §R9.1) can import it without violating
its isolation guarantee. See finding 7 in the ultrareview remediation brief.
"""

from __future__ import annotations

import re

RUN_ID_PATTERN = r"^[0-9a-f]{8}$"
RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def is_run_id(name: str) -> bool:
    """Return True iff name is an 8-char lowercase-hex run id.

    Design: finding 7 — prune_old_runs, the lineage/resume/cross_design globs,
        and the resource/server URI layers all share this one matcher instead
        of N drifting copies (the prior len==8 check deleted any 8-char dir).
    Implementation: full-anchored regex match against ^[0-9a-f]{8}$.
    Example: is_run_id('abcd1234') is True; is_run_id('ABCD1234') is False.
    """
    return RUN_ID_RE.match(name) is not None
