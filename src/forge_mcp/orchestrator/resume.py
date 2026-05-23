"""§H2 crash-resume discovery and partial-iteration archival."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..ids import is_run_id
from ..state import read_state

_TERMINAL = frozenset({"completed", "incomplete", "failed"})


@dataclass(frozen=True)
class ResumePoint:
    """A located resumable run and its restart anchor (§H2).

    Design: bundles the existing run id/dir and fsync-durable completed
        iteration so engine can resume without re-planning.
    Implementation: start_iteration is last_completed_iteration + 1.
    Example: ResumePoint('abcd1234', Path('.harness/abcd1234'), 7, 8).
    """

    run_id: str
    run_dir: Path
    last_completed_iteration: int
    start_iteration: int


def find_resumable_run(harness_dir: Path) -> ResumePoint | None:
    """Find the newest non-terminal run under harness_dir (§H2).

    Design: resume is explicit; this only locates a candidate whose state.json
        shows a non-terminal phase from a prior crash.
    Implementation: scan */state.json, skip unreadable/terminal states, choose
        max started_at, and derive start_iteration from durable anchor.
    Example: point = find_resumable_run(Path('/repo/.harness')).
    """
    best: tuple[datetime, ResumePoint] | None = None
    if not harness_dir.exists():
        return None
    for state_path in harness_dir.glob("*/state.json"):
        if not is_run_id(state_path.parent.name):
            continue  # finding 7 — defensive shape check on the */state.json glob
        try:
            state = read_state(state_path)
        except Exception:
            continue
        if state.state in _TERMINAL:
            continue
        # §H2 / H-Inv 2 (durable resume anchor): resume re-enters ONLY from a
        # fsync-durable iter_done boundary. last_completed_iteration is set only
        # on the iter_done transition, so a value < 1 means no iteration ever
        # completed (e.g. a crash during planning, state='planning'). Such a run
        # has no durable anchor; offering it would skip run_plan_phase and crash
        # in _seed_start_contract reading a never-written plan/plan.md. Skip it so
        # prepare_run returns the clean "no resumable run for target" McpError.
        if state.last_completed_iteration < 1:
            continue
        point = ResumePoint(
            run_id=state.run_id,
            run_dir=state_path.parent,
            last_completed_iteration=state.last_completed_iteration,
            start_iteration=state.last_completed_iteration + 1,
        )
        if best is None or state.started_at > best[0]:
            best = (state.started_at, point)
    return best[1] if best else None


def prepare_resume(run_dir: Path, point: ResumePoint) -> None:
    """Archive the partial interrupted iteration before redoing it (§H2).

    Design: H-Inv 3 keeps completed iterations append-only; only the in-flight
        start_iteration dir may be renamed for forensics and recreated.
    Implementation: rename iteration-N to iteration-N.interrupted-<utc-ts> when
        present, then recreate iteration-N with 0700 permissions.
    Example: prepare_resume(run_dir, point) archives iteration-8 if present.
    """
    partial = run_dir / f"iteration-{point.start_iteration}"
    if partial.exists():
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        partial.rename(run_dir / f"iteration-{point.start_iteration}.interrupted-{ts}")
    partial.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(partial, 0o700)
