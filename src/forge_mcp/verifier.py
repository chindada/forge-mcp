"""§H1 deterministic verification gate — subprocess-only, reads state only."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_OUTPUT_TAIL_MAX_CHARS = 16000  # §H1 — bound artifact and evaluator context.


@dataclass(frozen=True)
class VerificationOutcome:
    """One deterministic verification run against target_dir (§H1).

    Design: turns "did the build/tests pass" into a typed, on-disk fact so the
        terminal gate rests on a real exit code rather than an LLM claim.
    Implementation: frozen dataclass; `passed` is exit_code == 0; output_tail
        is bounded combined stdout+stderr persisted to iteration-N/verify.txt.
    Example: VerificationOutcome(command='pytest', exit_code=0, passed=True,
        timed_out=False, output_tail='ok').
    """

    command: str
    exit_code: int | None
    passed: bool
    timed_out: bool
    output_tail: str


def _tail(text: str) -> str:
    """Return the last bounded slice of verification output (§H1).

    Design: the evaluator only needs the tail of long build logs; bounding it
        protects fresh evaluator context from log bloat.
    Implementation: slice the trailing _OUTPUT_TAIL_MAX_CHARS characters.
    Example: len(_tail('a' * 20000)) == _OUTPUT_TAIL_MAX_CHARS.
    """
    return text[-_OUTPUT_TAIL_MAX_CHARS:]


def _text(value: str | bytes | None) -> str:
    """Normalize subprocess partial output to text (§H1).

    Design: TimeoutExpired may carry bytes even when text=True, but verify.txt
        is a text artifact and must stay renderable.
    Implementation: return strings unchanged, decode bytes with replacement,
        and map None to the empty string.
    Example: _text(b'ok') == 'ok'.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_verification(
    target_dir: Path, command: str, *, timeout_seconds: int
) -> VerificationOutcome:
    """Run the caller's verification command in target_dir under a timeout.

    Design: §H1 keeps the gate deterministic and orchestrator-owned; non-zero
        exit and timeout return an outcome instead of raising, preserving §6.3.
    Implementation: subprocess.run(shell=True, cwd=target_dir, capture_output,
        text, timeout); TimeoutExpired becomes timed_out=True with no exit code.
    Example: run_verification(Path('/repo'), 'npm test', timeout_seconds=600).
    """
    try:
        completed = subprocess.run(  # noqa: S602 - caller command is deliberate (§H1).
            command,
            shell=True,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
        return VerificationOutcome(
            command=command,
            exit_code=None,
            passed=False,
            timed_out=True,
            output_tail=_tail(stdout + stderr),
        )
    combined = (completed.stdout or "") + (completed.stderr or "")
    return VerificationOutcome(
        command=command,
        exit_code=completed.returncode,
        passed=completed.returncode == 0,
        timed_out=False,
        output_tail=_tail(combined),
    )


def render(outcome: VerificationOutcome) -> str:
    """Render a VerificationOutcome as iteration-N/verify.txt (§H1).

    Design: §H1.4 feeds verify.txt to the evaluator, so the header states the
        deterministic verdict and the body carries bounded output.
    Implementation: format command, exit_code, timed_out, passed, then tail.
    Example: render(VerificationOutcome('pytest', 0, True, False, 'ok')).
    """
    head = (
        f"command: {outcome.command}\n"
        f"exit_code: {outcome.exit_code}\n"
        f"timed_out: {outcome.timed_out}\n"
        f"passed: {outcome.passed}\n"
        "--- output tail ---\n"
    )
    return head + outcome.output_tail + "\n"
