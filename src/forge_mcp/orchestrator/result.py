"""§11.5 RunResult construction from state, ledger, and artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..models import (
    ArtifactIndex,
    IterationArtifacts,
    RunForgeInput,
    RunResult,
    VerificationSummary,
)
from .ledger import RunLedger
from .statemachine import RunStateMachine


def _iteration_dirs(run_dir: Path) -> list[Path]:
    """Return iteration directories sorted by numeric suffix.

    Design: §11.5 artifact order is numeric so iteration-10 follows iteration-9
        rather than lexicographic iteration-1.
    Implementation: parse `iteration-N` names and sort by the integer N.
    Example: _iteration_dirs(run_dir)[-1].name == 'iteration-10'.
    """
    found: list[tuple[int, Path]] = []
    for path in run_dir.glob("iteration-*"):
        try:
            found.append((int(path.name.split("-", 1)[1]), path))
        except (IndexError, ValueError):
            continue
    return [path for _, path in sorted(found)]


def _artifact_index(run_dir: Path, ledger: RunLedger) -> ArtifactIndex:
    """Build the public artifact index while omitting run.log.

    Design: §13 exposes useful handoff paths but never crosses run.log over the
        MCP tool boundary because it may contain sensitive details.
    Implementation: inspect known files under plan/ and iteration-N dirs and
        include optional paths only when files exist.
    Example: artifacts = _artifact_index(Path('.harness/abcd1234'), ledger).
    """
    iterations: list[IterationArtifacts] = []
    for iteration_dir in _iteration_dirs(run_dir):
        n = int(iteration_dir.name.split("-", 1)[1])
        iterations.append(
            IterationArtifacts(
                n=n,
                contract_path=str(iteration_dir / "contract.md"),
                summary_path=str(iteration_dir / "summary.md")
                if (iteration_dir / "summary.md").exists()
                else None,
                eval_json_path=str(iteration_dir / "eval.json")
                if (iteration_dir / "eval.json").exists()
                else None,
                eval_md_path=str(iteration_dir / "eval.md")
                if (iteration_dir / "eval.md").exists()
                else None,
                triage_json_path=str(iteration_dir / "triage.json")
                if (iteration_dir / "triage.json").exists()
                else None,
                git_violation_path=str(iteration_dir / "git-violation.txt")
                if (iteration_dir / "git-violation.txt").exists()
                else None,
                verify_path=str(iteration_dir / "verify.txt")
                if (iteration_dir / "verify.txt").exists()
                else None,
            )
        )
    return ArtifactIndex(
        plan_path=str(run_dir / "plan" / "plan.md"),
        iterations=iterations,
        status_log_path=str(run_dir / "status.log"),
        state_json_path=str(run_dir / "state.json"),
        git_state_path=str(run_dir / "inputs" / "git-state.txt")
        if (run_dir / "inputs" / "git-state.txt").exists()
        else None,
        git_uncommitted_path=ledger.git_uncommitted_path,
        unresolved_gaps_overflow_path=ledger.unresolved_overflow_path,
        design_flaw_gaps_overflow_path=ledger.design_flaw_overflow_path,
    )


def build_result(
    *,
    run_id: str,
    run_dir: Path,
    status: str,
    inputs: RunForgeInput,
    sm: RunStateMachine,
    ledger: RunLedger,
    started_at: datetime,
) -> RunResult:
    """Build the terminal RunResult for completed/incomplete/failed runs.

    Design: §6.3 terminal states are normal returns; failed-only fields are set
        only for failed status and traceback is already truncated by lifecycle.
    Implementation: compute runtime, discover artifacts numerically, and pass
        capped ledger lists into the Pydantic RunResult model.
    Example: result = build_result(status='completed', ...).
    """
    decided = ledger.decided_at or datetime.now(UTC)
    runtime_seconds = max(0, int((decided - started_at).total_seconds()))
    message = {
        "completed": "forge-mcp run completed",
        "incomplete": "forge-mcp run reached iteration or runtime cap",
        "failed": "forge-mcp run failed",
    }[status]
    if ledger.stop_reason:
        message += f" (stopped early: {ledger.stop_reason})"
    verification = None
    if ledger.last_verification is not None:
        v = ledger.last_verification
        verification = VerificationSummary(
            command=v.command,
            exit_code=v.exit_code,
            passed=v.passed,
            timed_out=v.timed_out,
        )
    failed = status == "failed"
    return RunResult(
        status=status,  # type: ignore[arg-type]
        run_id=run_id,
        run_dir=str(run_dir),
        iterations_used=sm.iteration,
        runtime_seconds=runtime_seconds,
        completed_phases=ledger.completed_phases,
        unresolved_gaps=ledger.unresolved_gaps,
        design_flaw_gaps=ledger.design_flaw_gaps,
        artifacts=_artifact_index(run_dir, ledger),
        warnings=ledger.warnings,
        message=message,
        failed_phase=ledger.failed_phase if failed else None,
        error_class=ledger.error_class if failed else None,
        error_message=ledger.error_message if failed else None,
        traceback_truncated=ledger.traceback_truncated if failed else None,
        verification=verification,
        resumed_from_iteration=ledger.resumed_from_iteration,
    )
