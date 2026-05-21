"""Environment-derived run configuration (§6.5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Configuration resolved once before preparing a run.

    Design: §6.5 centralizes environment overrides for Codex and Claude so
        preflight and runtime use the same values.
    Implementation: `from_env` reads three variables and converts path-valued
        settings to Path while leaving absent overrides as None.
    Example: cfg = RunConfig.from_env(); cfg.codex_bin == 'codex'.
    """

    codex_bin: str = "codex"
    claude_config_dir: Path | None = None
    claude_cli_path: Path | None = None
    keep_runs: int = 10

    @classmethod
    def from_env(cls) -> RunConfig:
        """Build configuration from documented environment variables.

        Design: §6.5 names the environment contract used by both doctor and
            preflight, including the Claude CLI runtime hatch.
        Implementation: missing values use defaults; present path variables
            are wrapped in Path without resolving symlinks.
        Example: RunConfig.from_env().claude_cli_path may be Path('/bin/claude').
        """
        claude_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        claude_cli_path = os.environ.get("FORGE_CLAUDE_CLI_PATH")
        return cls(
            codex_bin=os.environ.get("FORGE_CODEX_BIN", "codex"),
            claude_config_dir=Path(claude_config_dir) if claude_config_dir else None,
            claude_cli_path=Path(claude_cli_path) if claude_cli_path else None,
            keep_runs=int(os.environ.get("FORGE_KEEP_RUNS", "10")),
        )
