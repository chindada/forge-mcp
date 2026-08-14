"""Preflight check set for ``forge check`` and ``forge serve`` (§4.4/§10.3).

All SDK imports are lazy (inside functions) so that
``import forge_mcp.check`` succeeds without the Claude SDK installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Disk-space WARN threshold: 500 MiB free.
_DISK_WARN_BYTES: int = 500 * 1024 * 1024

# Short timeout (seconds) for the ``codex --version`` smoke probe.
_CODEX_VERSION_TIMEOUT: float = 5.0

# Claude Code 2.1.153 fixed strict-MCP handling for custom-agent servers.
_MIN_CLAUDE_VERSION: tuple[int, int, int] = (2, 1, 153)
_CLAUDE_VERSION_TIMEOUT: float = 5.0


@dataclass(frozen=True)
class Check:
    """A single preflight check row (§4.4).

    Design: §4.4 callers iterate a flat list of Check rows and display them
        in a table; the status Literal ensures typos are caught by type-checkers.
    Implementation: frozen dataclass so rows are safely hashable and immutable
        once produced by run_checks.
    Example: ``Check("git available", "OK", "/usr/bin/git")``.
    """

    label: str
    status: Literal["OK", "WARN", "FAIL"]
    detail: str


def any_fail(checks: list[Check]) -> bool:
    """Return True iff any row in *checks* has status ``"FAIL"`` (§4.4).

    Design: §4.4 forge serve maps any FAIL to a tagged error before starting;
        a single predicate prevents callers from repeating the status comparison.
    Implementation: short-circuit iteration with ``any`` over a generator that
        tests each row's status field.
    Example: ``any_fail([Check("x", "FAIL", "")]) == True``.
    """
    return any(c.status == "FAIL" for c in checks)


def _check_target_writable(target_dir: Path | None) -> Check:
    """Return a Check row for whether *target_dir* is a writable directory (§4.4).

    Design: §4.4 the target dir is the workspace for all run artifacts; if it
        is not writable the entire run will fail immediately, so this is the
        first probe in the list.
    Implementation: if *target_dir* is None emit a WARN; otherwise verify
        the path is an existing directory and is writable via os.access.
    Example: a freshly-created tmp_path returns status "OK".
    """
    import os

    label = "target writable"
    if target_dir is None:
        return Check(label, "WARN", "no target_dir provided")
    if not target_dir.exists():
        return Check(label, "FAIL", f"path does not exist: {target_dir}")
    if not target_dir.is_dir():
        return Check(label, "FAIL", f"path is not a directory: {target_dir}")
    if not os.access(target_dir, os.W_OK):
        return Check(label, "FAIL", f"directory not writable: {target_dir}")
    return Check(label, "OK", str(target_dir))


def _check_git_available() -> Check:
    """Return a Check row for whether ``git`` is on PATH (§4.4).

    Design: §4.4 forge relies on git for several operations; the git probe
        surfaces missing git installations early rather than at operation time.
    Implementation: use shutil.which("git") — non-None means git is available.
    Example: on a typical developer machine returns status "OK".
    """
    label = "git available"
    path = shutil.which("git")
    if path:
        return Check(label, "OK", path)
    return Check(label, "FAIL", "git not found on PATH")


def _check_claude_cli() -> Check:
    """Return a Check row for Claude CLI presence and MCP-safe version (§4.4).

    Design: §4.4 Claude CLI availability is required for claude-engine runs,
        and versions before 2.1.153 cannot enforce strict MCP configuration for
        custom-agent servers, so both conditions must pass before a live probe.
    Implementation: run the exact claude_bin() value's bounded ``--version``
        command, parse the leading three-part version, and fail closed on a
        missing, old, unrecognized, timed-out, or unsuccessful binary.
    Example: a missing override or version 2.1.152 FAILs; 2.1.153 returns OK.
    """
    from forge_mcp.config import claude_bin  # lazy import avoids SDK at module load

    label = "claude CLI"
    bin_path = claude_bin()
    try:
        result = subprocess.run(
            [str(bin_path), "--version"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_VERSION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Check(label, "FAIL", f"claude --version timed out after {_CLAUDE_VERSION_TIMEOUT}s")
    except OSError as exc:
        return Check(label, "FAIL", f"claude --version OSError: {exc}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return Check(label, "FAIL", f"exit code {result.returncode}: {detail}")

    raw = (result.stdout or result.stderr).strip()
    version_line = raw.splitlines()[0] if raw else ""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\s|$)", version_line)
    if match is None:
        return Check(label, "FAIL", f"unrecognized claude --version output: {version_line!r}")

    version = (int(match[1]), int(match[2]), int(match[3]))
    if version < _MIN_CLAUDE_VERSION:
        minimum = ".".join(str(part) for part in _MIN_CLAUDE_VERSION)
        return Check(label, "FAIL", f"Claude Code {version_line} is below required {minimum}")
    return Check(label, "OK", f"{bin_path} ({version_line})")


def _check_codex_binary() -> Check:
    """Return a Check row for whether the Codex binary is present (§4.4).

    Design: §4.4 codex binary presence is a prerequisite for codex-engine runs;
        reporting it as a named check helps operators diagnose missing installs.
    Implementation: call codex_bin() from forge_mcp.config; check path.exists()
        or shutil.which("codex") fallback.
    Example: with no codex installed returns FAIL with a descriptive detail.
    """
    from forge_mcp.config import codex_bin  # lazy import

    label = "codex binary"
    bin_path = codex_bin()
    if bin_path.exists():
        return Check(label, "OK", str(bin_path))
    on_path = shutil.which("codex")
    if on_path:
        return Check(label, "OK", on_path)
    return Check(label, "FAIL", f"codex binary not found: {bin_path}")


def _check_codex_importable() -> Check:
    """Return a Check row for whether ``openai_codex`` can be imported (§4.4).

    Design: §4.4 the openai_codex package is required for the Codex driver;
        surfacing an import failure here avoids a confusing error deep inside
        the driver at runtime.
    Implementation: attempt ``import openai_codex`` inside a try/except
        ImportError; return OK on success, FAIL on failure.
    Example: without the package installed returns FAIL with the error message.
    """
    label = "openai_codex importable"
    try:
        import openai_codex  # noqa: F401

        return Check(label, "OK", "")
    except ImportError as exc:
        return Check(label, "FAIL", str(exc))


def _check_codex_version_smoke() -> Check:
    """Return a Check row from running ``codex --version`` as a smoke test (§4.4).

    Design: §4.4 a live smoke call catches PATH/permission/corrupt-binary issues
        that binary presence alone cannot detect; a short timeout prevents hangs.
    Implementation: run ``codex --version`` via subprocess with a fixed short
        timeout; capture stdout; WARN if binary not found (FAIL is reserved for
        execution errors so that absence is distinguished from breakage).
    Example: with codex installed returns OK with the version string.
    """
    label = "codex --version smoke"
    codex = shutil.which("codex")
    if not codex:
        return Check(label, "WARN", "codex not on PATH; skipping version smoke")
    try:
        result = subprocess.run(
            [codex, "--version"],
            capture_output=True,
            text=True,
            timeout=_CODEX_VERSION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Check(label, "FAIL", f"codex --version timed out after {_CODEX_VERSION_TIMEOUT}s")
    except OSError as exc:
        return Check(label, "FAIL", f"codex --version OSError: {exc}")
    if result.returncode == 0:
        raw = (result.stdout or result.stderr).strip()
        version = raw.splitlines()[0] if raw else ""
        return Check(label, "OK", version)
    return Check(label, "FAIL", f"exit code {result.returncode}: {result.stderr.strip()}")


def _check_sdk_contract() -> Check:
    """Return a Check row for whether the Claude SDK supports MCP isolation (§8.4).

    Design: §8.4 the seam depends on both its public symbols and the SDK options
        that keep settings-backed skills separate from strict MCP isolation.
    Implementation: require the SDK, inspect ``ClaudeAgentOptions`` for all
        load-bearing fields, and import the seam symbols; absence or
        incompatibility returns FAIL without importing at module load.
    Example: no SDK or an SDK without ``strict_mcp_config`` returns FAIL.
    """
    import inspect

    label = "SDK contract"
    try:
        import claude_agent_sdk
    except ModuleNotFoundError as exc:
        if exc.name == "claude_agent_sdk":
            return Check(label, "FAIL", f"Claude SDK required for Claude stages: {exc}")
        return Check(label, "FAIL", f"Claude SDK import failed: {exc}")
    except ImportError as exc:
        return Check(label, "FAIL", f"Claude SDK import failed: {exc}")

    required_options = {"mcp_servers", "strict_mcp_config", "setting_sources", "skills"}
    try:
        option_parameters = inspect.signature(claude_agent_sdk.ClaudeAgentOptions).parameters
    except (AttributeError, TypeError, ValueError) as exc:
        return Check(label, "FAIL", f"ClaudeAgentOptions unavailable: {exc}")
    missing_options = sorted(required_options - set(option_parameters))
    if missing_options:
        return Check(label, "FAIL", f"ClaudeAgentOptions missing: {', '.join(missing_options)}")

    try:
        from forge_mcp.drivers._claude import (  # noqa: F401
            ClaudeRunner,
            StructuredResult,
            build_options,
        )
    except ImportError as exc:
        return Check(label, "FAIL", f"Claude seam import failed: {exc}")
    return Check(label, "OK", "Claude seam and MCP-isolation options present")


def _check_disk_space(target_dir: Path | None) -> Check:
    """Return a Check row for available disk space at *target_dir* (§4.4).

    Design: §4.4 insufficient disk space causes opaque I/O errors mid-run; a
        preflight WARN at a generous threshold gives operators a chance to act.
    Implementation: use shutil.disk_usage on target_dir (or Path.home() as
        fallback); emit WARN if free bytes are below _DISK_WARN_BYTES.
    Example: on a typical machine with >500 MiB free returns OK.
    """
    label = "disk space"
    check_path = target_dir if target_dir is not None else Path.home()
    try:
        usage = shutil.disk_usage(check_path)
    except OSError as exc:
        return Check(label, "WARN", f"disk_usage failed: {exc}")
    free_mib = usage.free // (1024 * 1024)
    if usage.free < _DISK_WARN_BYTES:
        return Check(label, "WARN", f"only {free_mib} MiB free at {check_path}")
    return Check(label, "OK", f"{free_mib} MiB free at {check_path}")


def _check_codex_skills() -> list[Check]:
    """Return Check rows for required Codex skill directories (§10.3).

    Design: §10.3 per-engine skill discovery is delegated to forge_mcp.skills;
        the check module maps SkillProbe rows to Check rows so the output list
        is uniform.
    Implementation: import probe_codex_skills lazily; resolve codex_home from
        environment ($CODEX_HOME) or ~/.codex default; convert each SkillProbe
        to a Check with label prefixed "codex-skill:".
    Example: with no skills installed returns two FAIL rows for the required ids.
    """
    import os

    from forge_mcp.skills import probe_codex_skills

    codex_home_env = os.environ.get("CODEX_HOME")
    codex_home = Path(codex_home_env) if codex_home_env else Path.home() / ".codex"
    probes = probe_codex_skills(codex_home=codex_home)
    return [Check(f"codex-skill:{p.label}", p.status, p.detail) for p in probes]


def _check_claude_skills(*, probe_live: bool = True) -> list[Check]:
    """Return Check rows for required Claude skill availability (§10.3).

    Design: §10.3 the Claude-side probe verifies that required skills are
        advertised at session init; it is skipped (WARN) when compatibility
        preflight disables live probing or its required runtime is unavailable.
    Implementation: attempt lazy imports of ClaudeDriver and probe_claude_skills;
        if the SDK or CLI is unavailable emit a single WARN row; otherwise run
        the async probe via asyncio.run with a short deadline and map each
        SkillProbe to a Check with label prefixed "claude-skill:".
        When *probe_live* is False, skip the live session entirely and return a
        single WARN row so tests and CI can stay offline and fast.
    Example: with live probing disabled returns a single WARN row
        "claude-skill:probe: skipped (live probe disabled)".
    """
    import asyncio

    label_prefix = "claude-skill"
    skip_row = Check(
        f"{label_prefix}: skipped (claude CLI / SDK not available)",
        "WARN",
        "SDK not installed or claude CLI absent",
    )

    # When live probing is disabled (e.g. in tests), return immediately.
    if not probe_live:
        return [Check(f"{label_prefix}:probe", "WARN", "skipped (live probe disabled)")]

    # Check CLI availability first (no SDK needed for this check).
    if not shutil.which("claude"):
        from forge_mcp.config import claude_bin  # lazy import

        if not claude_bin().exists():
            return [skip_row]

    try:
        from forge_mcp.drivers._claude import ClaudeDriver  # noqa: PLC0415
        from forge_mcp.skills import probe_claude_skills  # noqa: PLC0415
    except ImportError:
        return [skip_row]

    _REQUIRED_CLAUDE_SKILLS: tuple[str, ...] = ("writing-plans", "code-review")
    _CLAUDE_SKILL_DEADLINE: float = 20.0

    try:
        runner = ClaudeDriver()
        probes = asyncio.run(
            probe_claude_skills(
                runner=runner,
                required=_REQUIRED_CLAUDE_SKILLS,
                deadline=_CLAUDE_SKILL_DEADLINE,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return [Check(f"{label_prefix}: probe error", "WARN", str(exc))]

    return [Check(f"{label_prefix}:{p.label}", p.status, p.detail) for p in probes]


def run_checks(target_dir: Path | None, *, probe_claude_live: bool = True) -> list[Check]:
    """Run all preflight checks and return a list of Check rows (§4.4/§10.3).

    Design: §4.4 callers (``forge check`` CLI and ``forge serve`` preflight)
        need a single entry point that runs every probe and never raises;
        each probe is isolated in its own try/except so a crash in one probe
        does not prevent the others from running.
    Implementation: invoke each probe function inside a try/except Exception
        that converts unexpected errors into FAIL rows; concatenate all results
        into a flat list and return it.  When *probe_claude_live* is False the
        live Claude-skill session is skipped (WARN) so tests and CI stay
        offline and fast; the default True keeps the real doctor probing live.
    Example: ``run_checks(Path("/tmp/target"))`` returns at least a
        "target writable" row and a "git available" row.
    """
    rows: list[Check] = []

    def _safe(fn, label: str) -> list[Check]:
        """Run *fn* and return its Check rows, converting exceptions to a FAIL row.

        Design: §4.4 each probe must be isolated; an unexpected exception in one
            probe must not abort the remaining probes or propagate out of
            run_checks.
        Implementation: call fn(); on any Exception return a single FAIL Check
            whose label and detail describe the crash.
        Example: a probe that raises RuntimeError returns a single FAIL row.
        """
        try:
            result = fn()
            return result if isinstance(result, list) else [result]
        except Exception as exc:  # noqa: BLE001
            return [Check(label, "FAIL", f"probe crashed: {exc}")]

    rows += _safe(lambda: _check_target_writable(target_dir), "target writable")
    rows += _safe(_check_git_available, "git available")
    claude_cli_rows = _safe(_check_claude_cli, "claude CLI")
    rows += claude_cli_rows
    rows += _safe(_check_codex_binary, "codex binary")
    rows += _safe(_check_codex_importable, "openai_codex importable")
    rows += _safe(_check_codex_version_smoke, "codex --version smoke")
    sdk_contract_rows = _safe(_check_sdk_contract, "SDK contract")
    rows += sdk_contract_rows
    rows += _safe(lambda: _check_disk_space(target_dir), "disk space")
    rows += _safe(_check_codex_skills, "codex-skill probes")
    claude_probe_live = probe_claude_live and not any_fail(claude_cli_rows + sdk_contract_rows)
    rows += _safe(
        lambda: _check_claude_skills(probe_live=claude_probe_live),
        "claude-skill probes",
    )

    return rows
