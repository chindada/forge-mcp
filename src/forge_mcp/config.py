"""Environment-derived run configuration (§6.5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .resources import compute_harness_token


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
    harness_roots: tuple[Path, ...] = ()  # §R3.3
    harness_root_tokens: dict[str, Path] = field(default_factory=dict)  # §R3.3 token -> root
    lineage_top_k: int = 4  # §L8.6; K=0 disables cross-run learning.

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
        roots_raw = os.environ.get("FORGE_HARNESS_ROOTS", "").strip()
        roots: tuple[Path, ...] = ()
        tokens: dict[str, Path] = {}
        if roots_raw:
            parsed: list[Path] = []
            for entry in roots_raw.split(","):
                raw_path = entry.strip()
                if not raw_path:
                    continue
                root = Path(raw_path)
                if not root.is_absolute():
                    raise ValueError(
                        f"FORGE_HARNESS_ROOTS entry must be an absolute path: {raw_path!r}"
                    )
                if not root.exists():
                    raise ValueError(f"FORGE_HARNESS_ROOTS entry does not exist: {root}")
                if not root.is_dir():
                    raise ValueError(f"FORGE_HARNESS_ROOTS entry is not a directory: {root}")
                if not os.access(root, os.R_OK):
                    raise ValueError(f"FORGE_HARNESS_ROOTS entry is not readable: {root}")
                parsed.append(root)
                tokens[compute_harness_token(root)] = root
            roots = tuple(parsed)
        raw_top_k = os.environ.get("FORGE_LINEAGE_TOP_K", "4")
        try:
            lineage_top_k = int(raw_top_k)
        except ValueError as exc:
            raise ValueError(
                f"FORGE_LINEAGE_TOP_K must be an integer in [0, 10], got {raw_top_k!r}"
            ) from exc
        if not 0 <= lineage_top_k <= 10:
            raise ValueError(f"FORGE_LINEAGE_TOP_K must be in [0, 10], got {lineage_top_k}")
        return cls(
            codex_bin=os.environ.get("FORGE_CODEX_BIN", "codex"),
            claude_config_dir=Path(claude_config_dir) if claude_config_dir else None,
            claude_cli_path=Path(claude_cli_path) if claude_cli_path else None,
            keep_runs=int(os.environ.get("FORGE_KEEP_RUNS", "10")),
            harness_roots=roots,
            harness_root_tokens=tokens,
            lineage_top_k=lineage_top_k,
        )
