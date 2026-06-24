"""Environment resolution, binary paths, and run-dir creation (§10.4/§12)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from forge_mcp.ids import format_run_id

CONCURRENCY_CAP: int = 4


def _env_path(name: str) -> Path | None:
    """Return the env var as a Path, or None if not set.

    Design: §10.4 env vars must take precedence over PATH lookups and
        hard-coded defaults; a single helper centralises the lookup so each
        public function stays concise.
    Implementation: check os.environ for the key and return Path(value) when
        present, None otherwise.
    Example: with FORGE_CLAUDE_BIN=/usr/bin/myclaude, _env_path returns
        Path('/usr/bin/myclaude').
    """
    val = os.environ.get(name)
    return Path(val) if val is not None else None


def claude_config_dir() -> Path:
    """Return the Claude config directory (§10.4).

    Design: §10.4 operators override the config root via env var so that
        CI and dev environments can point at separate directories without
        changing user home state.
    Implementation: return $CLAUDE_CONFIG_DIR as a Path when set, else
        ~/.claude.
    Example: with CLAUDE_CONFIG_DIR=/tmp/cc, returns Path('/tmp/cc').
    """
    return _env_path("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"


def claude_bin() -> Path:
    """Return the path to the claude binary (§10.4).

    Design: §10.4 the binary location varies by install method; env var
        overrides allow test harnesses and alternate installs to be used
        without modifying PATH.
    Implementation: check $FORGE_CLAUDE_BIN first, then shutil.which('claude'),
        then fall back to ~/.local/bin/claude.
    Example: with FORGE_CLAUDE_BIN=/opt/claude, returns Path('/opt/claude').
    """
    return (
        _env_path("FORGE_CLAUDE_BIN")
        or (Path(w) if (w := shutil.which("claude")) else None)
        or Path.home() / ".local" / "bin" / "claude"
    )


def codex_bin() -> Path:
    """Return the path to the codex binary (§10.4).

    Design: §10.4 codex may be installed via npm-global rather than a
        system path, so a dedicated fallback covers that common layout.
    Implementation: check $FORGE_CODEX_BIN first, then shutil.which('codex'),
        then fall back to ~/.npm-global/bin/codex.
    Example: with FORGE_CODEX_BIN=/tmp/codex, returns Path('/tmp/codex').
    """
    return (
        _env_path("FORGE_CODEX_BIN")
        or (Path(w) if (w := shutil.which("codex")) else None)
        or Path.home() / ".npm-global" / "bin" / "codex"
    )


def _ensure_harness_gitignore(harness: Path) -> None:
    """Create harness dir and write a self-ignoring .gitignore if absent (§7.2).

    Design: §7.2 the .harness directory must never appear in version control;
        using O_EXCL ensures the file is written exactly once and subsequent
        runs leave any existing content intact.
    Implementation: mkdir harness with exist_ok; attempt os.open with
        O_CREAT|O_EXCL|O_WRONLY; on success write b'*' and close; on
        FileExistsError leave the file untouched.
    Example: calling twice leaves .gitignore containing exactly '*'.
    """
    harness.mkdir(exist_ok=True)
    try:
        fd = os.open(harness / ".gitignore", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    try:
        os.write(fd, b"*")
    finally:
        os.close(fd)


def create_run_dir(target_dir: Path, when: time.struct_time) -> Path:
    """Create and return a timestamped run directory under target_dir/.harness (§12).

    Design: §12 each run needs a unique, sortable directory name; same-second
        re-runs receive a two-digit uniquifier suffix so multiple concurrent
        or rapid sequential runs never collide.
    Implementation: ensure .harness and its .gitignore; loop uniquifier from
        None then 1, 2, … calling format_run_id; attempt os.makedirs at mode
        0700 with exist_ok=False; return on success, bump uniquifier on
        FileExistsError.
    Example: two calls with the same struct_time yield dirs '20260623183102'
        and '20260623183102-01'.
    """
    harness = target_dir / ".harness"
    _ensure_harness_gitignore(harness)
    u: int | None = None
    while True:
        run_id = format_run_id(when, uniquifier=u)
        run_dir = harness / run_id
        try:
            os.makedirs(run_dir, mode=0o700, exist_ok=False)
            return run_dir
        except FileExistsError:
            u = 1 if u is None else u + 1
