"""§8.3 RunLedger mutable accumulator for orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models import DesignFlawGap, EvalGap
from ..verifier import VerificationOutcome


@dataclass
class RunLedger:
    """Mutable accumulator replacing scattered orchestrator state.

    Design: §8.3 keeps terminal result inputs explicit without letting drivers
        mutate orchestrator internals.
    Implementation: plain dataclass with list default factories and scalar
        fields populated by lifecycle and phase helpers.
    Example: ledger = RunLedger(); ledger.warnings.append('note').
    """

    warnings: list[str] = field(default_factory=list)
    completed_phases: list[str] = field(default_factory=list)
    unresolved_gaps: list[EvalGap] = field(default_factory=list)
    design_flaw_gaps: list[DesignFlawGap] = field(default_factory=list)
    decided_at: datetime | None = None
    unresolved_overflow_path: str | None = None
    design_flaw_overflow_path: str | None = None
    git_uncommitted_path: str | None = None
    lock_released: bool = False
    failed_phase: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    traceback_truncated: str | None = None
    last_verification: VerificationOutcome | None = None
    gap_fingerprints: list[frozenset[str]] = field(default_factory=list)
    # §H6.3: high-severity gaps synthesized during iteration n (e.g. a degenerate
    # remediation contract detected after that iteration's fingerprint/remediation
    # already ran) are parked here and drained into iteration n+1's eval_for_loop,
    # so the condition reaches the next iteration's evaluation/seed.
    carried_gaps: list[EvalGap] = field(default_factory=list)
    stop_reason: str | None = None
    resumed_from_iteration: int | None = None
    # §L8.5 — forensic fields for cross-run learning.
    linked_prior_runs: list[str] = field(default_factory=list)
    lineage_overflow_path: Path | None = None
