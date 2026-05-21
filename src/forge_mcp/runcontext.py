"""Immutable per-driver run context (§8.4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunContext:
    """Fresh context object passed into each driver call.

    Design: §8.4 avoids threading mutable orchestrator state into SDK-facing
        drivers and carries the Claude CLI runtime hatch from §6.5.
    Implementation: frozen slots dataclass with load-bearing field order used
        by tests and keyword construction in phase code.
    Example: RunContext(run_dir=Path('/r'), target_dir=None, iteration_n=None).
    """

    run_dir: Path
    target_dir: Path | None = None
    claude_config_dir: Path | None = None
    claude_cli_path: Path | None = None
    iteration_n: int | None = None
