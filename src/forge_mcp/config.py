"""Environment-derived run configuration (§6.5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .resources import compute_harness_token


def _parse_bounded_int_env(name: str, *, default: int, low: int, high: int) -> int:
    """Parse an integer environment variable with an inclusive range (§E).

    Design: environment validation must be symmetric for every bounded integer
        knob so bad operator input fails fast instead of drifting silently.
    Implementation: read os.environ, int() with a wrapped ValueError, then
        apply one inclusive range check with the same user-facing message.
    Example: _parse_bounded_int_env('FORGE_KEEP_RUNS', default=10, low=0, high=1000).
    """
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer in [{low}, {high}], got {raw!r}") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}], got {raw!r}")
    return value


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Configuration resolved once before preparing a run.

    Design: §6.5 centralizes environment overrides for Codex and Claude so
        preflight and runtime use the same values.
    Implementation: `from_env` reads the documented environment variables and converts path-valued
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
        keep_runs = _parse_bounded_int_env("FORGE_KEEP_RUNS", default=10, low=0, high=1000)
        lineage_top_k = _parse_bounded_int_env("FORGE_LINEAGE_TOP_K", default=4, low=0, high=10)
        return cls(
            codex_bin=os.environ.get("FORGE_CODEX_BIN", "codex"),
            claude_config_dir=Path(claude_config_dir) if claude_config_dir else None,
            claude_cli_path=Path(claude_cli_path) if claude_cli_path else None,
            keep_runs=keep_runs,
            harness_roots=roots,
            harness_root_tokens=tokens,
            lineage_top_k=lineage_top_k,
        )
