"""Deterministic verification gate, per-plan and run-level (§6.5)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# A verification conjunct that asserts a clean/unchanged git working tree. The
# direct-edit loop never commits (spec 0003 §1), so such a gate measures "not yet
# committed" against the 0-commit deliverables and can never pass in-loop — it is a
# post-commit CI check, not a completion gate. Two shapes are matched (best-effort,
# inline only — a gate hidden inside `make verify` is invisible here; the planner
# contract in planner_system.md is the primary control):
#   - an emptiness test over git status: `test -z "$(git status --porcelain)"`,
#     `[ -z "$(git status -s)" ]`, `[[ -z ... ]]`
#   - a diff tree-state gate: `git diff --exit-code` / `git diff --quiet`
# The patterns are matched with fullmatch against a whole `&&`-conjunct and use
# `[^;|&]` rather than `.` so a gate is recognised ONLY when it IS the entire
# conjunct — a gate merely *buried* in a multi-statement segment (chained by
# `;`/`|`/`&`, or mis-split out of a quoted subshell) is left in place rather than
# taking the surrounding correctness work down with it. `&` is excluded too so a
# gate before a `& <work>` tail can't swallow that work (a `2>&1`-style redirect
# inside a gate is the price — it simply isn't recognised, which fails safe).
_CLEAN_TREE_GATES = (
    re.compile(
        r"(?:test|\[\[?)\s+-z\b[^;|&]*\bgit\s+status\b[^;|&]*(?:--porcelain|--short|-s)\b[^;|&]*"
    ),
    re.compile(r"\bgit\s+diff\b[^;|&]*(?:--exit-code|--quiet)[^;|&]*"),
)


def _is_clean_tree_gate(conjunct: str) -> bool:
    """Report whether one stripped `&&`-conjunct, in full, asserts a clean git tree.

    Design: §1 a clean-tree assertion is the only conjunct class that is
        unsatisfiable in the never-committing loop, so it is the unit the scoper
        drops; everything else (build/test/lint) is kept. A gate must BE the whole
        conjunct, not merely appear inside it, so dropping it never removes adjacent
        real work.
    Implementation: True iff any _CLEAN_TREE_GATES pattern fullmatches the (already
        stripped) conjunct — anchored, `[^;|&]`-bounded, see the pattern comment.
    Example: _is_clean_tree_gate('test -z "$(git status --porcelain)"') is True;
        _is_clean_tree_gate('make build ; git diff --quiet') is False.
    """
    return any(pattern.fullmatch(conjunct) for pattern in _CLEAN_TREE_GATES)


def scope_verification_command(command: str | None) -> tuple[str | None, list[str]]:
    """Drop non-completing clean-tree gates from a verification command (spec 0003 §1).

    Design: §1 the harness is a non-committing direct-edit loop, so a
        verification_command conjunct that gates on a clean git tree (e.g.
        ``test -z "$(git status --porcelain)"``) is structurally unsatisfiable —
        the deliverables are uncommitted by design, so the tree is never clean and
        the gate burns the whole iteration cap without ever signalling correctness.
        Such checks are post-commit CI acceptance gates, not in-loop completion
        gates. This scopes the command down to its implementation-correctness
        conjuncts, dropping only the clean-tree ones (decompose, never drop the
        whole command), and reports what was removed so the caller can record it.
    Implementation: split on ``&&`` (best-effort, not quote/subshell aware) and
        partition in one pass — a segment that, IN FULL, is a clean-tree gate
        (_is_clean_tree_gate) is dropped, every other segment kept. With nothing to
        drop, return the command byte-identical (no normalisation) — so a mis-split
        fragment or a gate buried after a `;`/`|`/`&` is a safe no-op, never a
        corruption or a vacuous all-dropped ``None``. Otherwise rejoin the survivors
        with `` && `` (or None when every conjunct was a gate). The planner contract
        (planner_system.md) is the primary control.
    Example: scope_verification_command('make build && test -z "$(git status
        --porcelain)"') == ('make build', ['test -z "$(git status --porcelain)"']).
    """
    if command is None:
        return None, []
    kept: list[str] = []
    dropped: list[str] = []
    for segment in command.split("&&"):
        conjunct = segment.strip()
        (dropped if _is_clean_tree_gate(conjunct) else kept).append(conjunct)
    if not dropped:
        return command, []
    scoped = " && ".join(kept) if kept else None
    return scoped, dropped


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
