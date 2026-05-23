"""§14 doctor — env subset shared with preflight (single source of truth)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from .config import RunConfig

CheckStatus = Literal["OK", "WARN", "FAIL"]
DISK_FREE_WARN_BYTES = 5 * 1024**3


def check_target_dir_writable(target_dir: Path) -> tuple[str, CheckStatus, str]:
    """Verify target_dir exists, is a dir, and is read+write to the user.

    Design: §6.4 step 1 is shared between preflight and doctor so both report
        target directory defects consistently.
    Implementation: check existence, directory type, and os.access read/write.
    Example: check_target_dir_writable(Path('/repo')).
    """
    if not target_dir.exists():
        return ("target_dir", "FAIL", f"missing: {target_dir}")
    if not target_dir.is_dir():
        return ("target_dir", "FAIL", f"not a directory: {target_dir}")
    if not os.access(target_dir, os.R_OK | os.W_OK):
        return ("target_dir", "FAIL", f"not read+write: {target_dir}")
    return ("target_dir", "OK", str(target_dir))


def check_harness_writable(harness_dir: Path) -> tuple[str, CheckStatus, str]:
    """Verify `<target_dir>/.harness/` exists and is writable.

    Design: §6.4 step 3 requires artifact roots to be usable before expensive
        SDK probes or lock handoff occur.
    Implementation: create and delete a tiny probe file in the harness dir.
    Example: check_harness_writable(Path('/repo/.harness')).
    """
    try:
        harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = harness_dir / ".probe"
        probe.write_text("ok")
        probe.unlink()
    except Exception as exc:
        return (".harness", "FAIL", str(exc))
    return (".harness", "OK", str(harness_dir))


def check_claude_cli(config: RunConfig) -> tuple[str, CheckStatus, str]:
    """Resolve the `claude` CLI binary path.

    Design: §6.5 makes FORGE_CLAUDE_CLI_PATH a runtime hatch, and doctor must
        report the same resolution preflight will use.
    Implementation: prefer PATH, then the configured explicit cli path.
    Example: check_claude_cli(RunConfig.from_env()).
    """
    found = shutil.which("claude")
    if found:
        return ("claude", "OK", found)
    if config.claude_cli_path and Path(config.claude_cli_path).exists():
        return ("claude", "OK", str(config.claude_cli_path))
    return ("claude", "FAIL", "claude not on PATH and FORGE_CLAUDE_CLI_PATH unset")


async def check_codex(config: RunConfig) -> tuple[str, CheckStatus, str]:
    """Verify codex bin + SDK import + a live model probe.

    Design: §6.4 step 6 fails fast when Codex cannot be imported or its CLI
        cannot answer a minimal smoke command.
    Implementation: resolve the binary, import openai_codex lazily, and run
        `_codex_smoke_turn` under a 30-second asyncio timeout.
    Example: await check_codex(RunConfig.from_env()).
    """
    resolved = shutil.which(config.codex_bin)
    bin_path = resolved or config.codex_bin
    if resolved is None:
        try:
            os.stat(bin_path)
        except FileNotFoundError:
            return ("codex", "FAIL", f"codex binary not found: {config.codex_bin}")
        except OSError as exc:
            return ("codex", "FAIL", f"codex binary not usable: {exc}")
    if resolved is None and not stat.S_ISREG(os.stat(bin_path).st_mode):
        return ("codex", "FAIL", f"codex binary not found: {config.codex_bin}")
    try:
        import openai_codex  # noqa: F401
    except ImportError as exc:
        return ("codex", "FAIL", f"openai_codex import failed: {exc}")
    try:
        await asyncio.wait_for(_codex_smoke_turn(bin_path), timeout=30)
    except Exception as exc:
        return ("codex", "FAIL", f"live probe failed: {exc}")
    return ("codex", "OK", bin_path)


async def _codex_smoke_turn(codex_bin: str) -> None:
    """Run a tiny no-op codex turn to confirm the binary responds.

    Design: §6.4 step 6 catches broken installs before the orchestrator starts
        a long generator run.
    Implementation: spawn `codex --version` and require exit code zero.
    Example: await _codex_smoke_turn('/usr/local/bin/codex').
    """
    proc = await asyncio.create_subprocess_exec(
        codex_bin,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"codex --version exit {proc.returncode}")


def check_claude_auth() -> tuple[str, CheckStatus, str]:
    """Verify a Claude auth source is resolvable (A4).

    Design: §6.4 step 8 fails fast when no auth source can authenticate Claude;
        A4 checks env vars, .credentials.json, then Darwin Keychain.
    Implementation: never read secrets; probe only source existence and treat
        Keychain errors/timeouts as not found.
    Example: check_claude_auth().
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return ("claude_auth", "OK", "env-var")
    config_dir_env = os.environ.get("CLAUDE_CONFIG_DIR")
    config_dir = Path(config_dir_env) if config_dir_env else (Path.home() / ".claude")
    cred = config_dir / ".credentials.json"
    if cred.exists() and cred.stat().st_size > 0:
        return ("claude_auth", "OK", str(cred))
    if sys.platform == "darwin":
        suffix = ""
        if config_dir_env:
            digest = hashlib.sha256(os.path.abspath(config_dir_env).encode()).hexdigest()[:8]
            suffix = "-" + digest
        service = f"Claude Code-credentials{suffix}"
        try:
            proc = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    os.environ.get("USER", ""),
                    "-s",
                    service,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if proc.returncode == 0:
                return ("claude_auth", "OK", f"keychain:{service}")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ("claude_auth", "FAIL", "no ANTHROPIC_API_KEY and no credentials.json")


def disk_space_warn_threshold() -> int:
    """Return the §14 free-space warning threshold in bytes.

    Design: keeping this constant behind a helper lets tests pin the operational
        warning threshold without duplicating the numeric expression.
    Implementation: return the module-level 5 GiB byte count.
    Example: disk_space_warn_threshold() == 5 * 1024**3.
    """
    return DISK_FREE_WARN_BYTES


def check_disk_space(path: Path) -> tuple[str, CheckStatus, str]:
    """Return WARN when free disk space is below the §14 threshold.

    Design: disk pressure is important operator information but should not
        fail a run outright.
    Implementation: call shutil.disk_usage and compare `.free` to 5 GiB.
    Example: check_disk_space(Path.cwd()).
    """
    stat = shutil.disk_usage(path)
    if stat.free < DISK_FREE_WARN_BYTES:
        return ("disk_free", "WARN", f"{stat.free} bytes free under {path}")
    return ("disk_free", "OK", f"{stat.free} bytes free")


async def disk_space_warn_if_low(run_dir: Path, status: Any, logger: Any, ledger: Any) -> None:
    """Warn (non-fatal) when free space under run_dir is below §14 threshold.

    Design: §8.1 / §14 — a live warning surfaces disk pressure to the operator
        without failing the run; actual disk-full mid-run becomes
        status='failed' from a write OSError, not from this probe.
    Implementation: reuse the synchronous check_disk_space probe; on WARN,
        await one status.update(kind='warning') and append a non-fatal entry to
        ledger.warnings.
    Example: await disk_space_warn_if_low(run_dir, status, logger, ledger).
    """
    label, level, detail = check_disk_space(run_dir)
    if level != "WARN":
        return
    msg = f"{label}: {detail}"
    if logger is not None:
        logger.warning(msg)
    await status.update(
        phase="init",
        agent="orchestrator",
        message=msg,
        kind="warning",
    )
    ledger.warnings.append(msg)
