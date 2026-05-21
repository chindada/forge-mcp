"""§6.4 preflight — validate inputs, prepare artifacts, and acquire lock."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from .config import RunConfig
from .doctor import (
    CheckStatus,
    check_claude_auth,
    check_claude_cli,
    check_codex,
    check_harness_writable,
    check_target_dir_writable,
)
from .drivers._claude import ClaudeRunnerImpl
from .lockfile import LockBusy, TargetLock
from .models import RunForgeInput
from .skills import probe_required_skills

INVALID_PARAMS = -32602
SERVER_ERROR = -32000


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """§6.4 successful preflight handoff to the orchestrator.

    Design: lock ownership transfers to the orchestrator so §8.5 cancellation
        can release the lock before writing the final failed state.
    Implementation: frozen slots dataclass; run_id is read from TargetLock so
        every artifact and result shares the same identity.
    Example: PreparedRun(harness_dir=p, lock=l, run_id='abcd1234', config=c).
    """

    harness_dir: Path
    lock: TargetLock
    run_id: str
    config: RunConfig


def _canonicalize_target_dir(path: str) -> Path:
    """Return the §13 target_dir canonical form using abspath, not realpath.

    Design: §13 intentionally treats symlink aliases as distinct artifact roots
        by avoiding realpath resolution.
    Implementation: wrap `os.path.abspath` in Path and perform no existence
        checks in this helper.
    Example: _canonicalize_target_dir('./repo') returns an absolute Path.
    """
    return Path(os.path.abspath(path))


def _raise(code: int, message: str) -> None:
    """Raise an McpError with the given JSON-RPC intent code.

    Design: §6.3 uses numeric codes internally even though FastMCP turns them
        into `isError=true` text results on the wire.
    Implementation: construct ErrorData consistently for all preflight defects.
    Example: _raise(-32602, 'bad target') raises McpError.
    """
    raise McpError(ErrorData(code=code, message=message))


def _fail_if_not_ok(check: tuple[str, CheckStatus, str], *, code: int) -> None:
    """Raise when a shared doctor check returns FAIL.

    Design: §14 keeps doctor and preflight checks shared while preflight maps
        FAIL statuses onto MCP errors.
    Implementation: ignore OK/WARN and format FAIL as `label: detail`.
    Example: _fail_if_not_ok(('x', 'FAIL', 'bad'), code=-32000).
    """
    label, status, detail = check
    if status == "FAIL":
        _raise(code, f"{label}: {detail}")


def _load_design_doc(inputs: RunForgeInput) -> str:
    """Read or return the requested design document text.

    Design: §6.4 step 2 validates the document source after Pydantic's xor
        check and before any lock is acquired.
    Implementation: read design_doc_path when present, otherwise use inline
        content; reject missing or empty text.
    Example: _load_design_doc(RunForgeInput(..., design_doc_content='x')).
    """
    if inputs.design_doc_path is not None:
        path = Path(inputs.design_doc_path)
        if not path.exists() or not path.is_file():
            _raise(INVALID_PARAMS, f"design_doc_path missing: {path}")
        text = path.read_text()
    else:
        text = inputs.design_doc_content or ""
    if not text.strip():
        _raise(INVALID_PARAMS, "design document is empty")
    return text


def _ensure_harness(harness_dir: Path) -> None:
    """Create `.harness` and its private catch-all .gitignore.

        Design: §13 stores all run artifacts privately and hides them from git via
            a never-overwritten `*
    ` ignore file.
        Implementation: mkdir 0700, chmod on POSIX, and use O_EXCL for .gitignore.
        Example: _ensure_harness(Path('/repo/.harness')).
    """
    harness_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(harness_dir, 0o700)
    try:
        fd = os.open(harness_dir / ".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        os.write(fd, b"*\n")
    finally:
        os.close(fd)


async def prepare_run(inputs: RunForgeInput, config: RunConfig) -> PreparedRun:
    """Validate and prepare a run before orchestration starts (§6.4).

    Design: lock acquisition happens before slow SDK probes, and any later
        failure releases the lock before surfacing SERVER_ERROR.
    Implementation: validate target/design, create harness, acquire TargetLock,
        run shared env checks and skill probe, then return PreparedRun.
    Example: prepared = await prepare_run(inputs, RunConfig.from_env()).
    """
    os.umask(0o077)
    target_dir = _canonicalize_target_dir(inputs.target_dir)
    _fail_if_not_ok(check_target_dir_writable(target_dir), code=INVALID_PARAMS)
    _load_design_doc(inputs)
    harness_dir = target_dir / ".harness"
    _ensure_harness(harness_dir)
    _fail_if_not_ok(check_harness_writable(harness_dir), code=SERVER_ERROR)
    lock = TargetLock(harness_dir / "run.lock")
    try:
        lock.acquire()
    except LockBusy as exc:
        _raise(SERVER_ERROR, str(exc))
    try:
        _fail_if_not_ok(check_claude_cli(config), code=SERVER_ERROR)
        _fail_if_not_ok(await check_codex(config), code=SERVER_ERROR)
        await probe_required_skills(
            runner=ClaudeRunnerImpl(), claude_cli_path=config.claude_cli_path
        )
        _fail_if_not_ok(check_claude_auth(), code=SERVER_ERROR)
    except Exception:
        lock.release()
        raise
    return PreparedRun(harness_dir=harness_dir, lock=lock, run_id=lock.run_id, config=config)
