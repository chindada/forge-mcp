"""§8.3 RunLedger mutable accumulator for orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..models import DesignFlawGap, EvalGap


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
