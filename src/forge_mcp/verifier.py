"""Deterministic verification gate, per-plan and run-level (§6.5)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VerifyOutcome:
    """The cached result of one verification-command run (§6.5)."""

    passed: bool
    exit_code: int | None
    timed_out: bool
    output: str


def run_verification(
    command: str | None,
    cwd: Path,
    *,
    timeout: float = 600.0,
    output_limit: int = 65536,
) -> VerifyOutcome:
    """Run a verification command in `cwd` and classify the outcome (§6.5).

    Design: §6.5/D1 a declared-absent command passes vacuously; a present
        command gates the plan's sandbox (or, run-level, the merged target_dir).
        The command originates from the Planner, so shell=True is acceptable.
    Implementation: command is None -> passed. Else subprocess.run(shell=True,
        cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False);
        passed = (exit_code == 0); a subprocess.TimeoutExpired -> passed=False,
        timed_out=True. Output (stdout+stderr) is bounded to output_limit.
    Example: run_verification('pytest -q', sandbox).passed.
    """
    if command is None:
        return VerifyOutcome(passed=True, exit_code=None, timed_out=False, output="")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:

        def to_str(data: bytes | str | None) -> str:
            """Convert bytes, str, or None to str.

            Design: handle bytes from TimeoutExpired which may be bytes or str.
            Implementation: check type, decode bytes, return str.
            Example: to_str(b"hello") -> "hello".
            """
            if data is None:
                return ""
            if isinstance(data, bytes):
                return data.decode(errors="replace")
            return data

        partial = to_str(exc.stdout) + to_str(exc.stderr)
        return VerifyOutcome(
            passed=False, exit_code=None, timed_out=True, output=partial[:output_limit]
        )
    output = (proc.stdout + proc.stderr)[:output_limit]
    return VerifyOutcome(
        passed=proc.returncode == 0, exit_code=proc.returncode, timed_out=False, output=output
    )
