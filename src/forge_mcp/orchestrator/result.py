"""§11.5 RunResult construction from state, ledger, and artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..errors import PREFIX_START, FailureKind, tag
from ..models import (
    ArtifactIndex,
    IterationArtifacts,
    RunForgeInput,
    RunResult,
    VerificationSummary,
)
from .ledger import RunLedger
from .statemachine import RunStateMachine


def _failure_kind_for(status: str) -> FailureKind | None:
    """Map a terminal RunResult status to its §W categorical kind.

    Design: §W3 says completed has no failure kind, failed maps to
        infra_failure, and incomplete maps to timeout for returned results.
    Implementation: use a flat mapping and return None for unknown values so
        the model validation still owns illegal status detection.
    Example: _failure_kind_for("incomplete") returns "timeout".
    """
    mapping: dict[str, FailureKind] = {"failed": "infra_failure", "incomplete": "timeout"}
    return mapping.get(status)


def _tag_once(kind: FailureKind, body: str | None) -> str | None:
    """Prefix a message body unless it is already tagged (§W3).

    Design: §W-Decision 5 prepends the stable prefix while preserving the body;
        retry paths must not accidentally double-prefix diagnostics.
    Implementation: None passes through; strings beginning with the literal
        prefix family pass through; all other strings route through errors.tag.
    Example: _tag_once("timeout", "cap") starts with the timeout prefix.
    """
    if body is None or body.startswith(PREFIX_START):
        return body
    return tag(kind, body)


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


def _maybe_uri(harness_token: str | None, run_id: str, subpath: str) -> str | None:
    """Return encode_uri(...) when harness_token is set, else None (§R4.2/§R4.3).

    Design: §R4.2 — URI companions are populated only when the orchestrator
        was given a harness_token, preserving legacy direct-call results.
    Implementation: ternary on harness_token; encode_uri is pure and
        stdlib-only, and the local import avoids widening module load order.
    Example: _maybe_uri('aBcDeFgHiJkL', '12345678', 'plan/plan.md') starts
        with 'forge://'.
    """
    from ..resources import encode_uri

    return encode_uri(harness_token, run_id, subpath) if harness_token else None


def _artifact_index(
    run_dir: Path,
    ledger: RunLedger,
    *,
    harness_token: str | None = None,
    run_id: str | None = None,
) -> ArtifactIndex:
    """Build the public artifact index while omitting run.log.

    Design: §13 exposes useful handoff paths but never crosses run.log over the
        MCP tool boundary because it may contain sensitive details. §R4.2 /
        §R4.3 add optional forge:// URI companions next to each existing path.
    Implementation: inspect known files under plan/, inputs/, and iteration-N
        directories; include optional paths only when files exist and mirror
        each with a URI populated by _maybe_uri when tokenized.
    Example: artifacts = _artifact_index(Path('.harness/abcd1234'), ledger,
        harness_token='aBcDeFgHiJkL', run_id='abcd1234').
    """
    iterations: list[IterationArtifacts] = []
    for iteration_dir in _iteration_dirs(run_dir):
        n = int(iteration_dir.name.split("-", 1)[1])
        sub_prefix = iteration_dir.name
        summary_exists = (iteration_dir / "summary.md").exists()
        eval_json_exists = (iteration_dir / "eval.json").exists()
        eval_md_exists = (iteration_dir / "eval.md").exists()
        triage_json_exists = (iteration_dir / "triage.json").exists()
        git_violation_exists = (iteration_dir / "git-violation.txt").exists()
        verify_exists = (iteration_dir / "verify.txt").exists()
        sessions_exists = (iteration_dir / "sessions.json").exists()
        iterations.append(
            IterationArtifacts(
                n=n,
                contract_path=str(iteration_dir / "contract.md"),
                contract_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/contract.md")
                if run_id
                else None,
                summary_path=str(iteration_dir / "summary.md") if summary_exists else None,
                summary_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/summary.md")
                if (summary_exists and run_id)
                else None,
                eval_json_path=str(iteration_dir / "eval.json") if eval_json_exists else None,
                eval_json_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/eval.json")
                if (eval_json_exists and run_id)
                else None,
                eval_md_path=str(iteration_dir / "eval.md") if eval_md_exists else None,
                eval_md_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/eval.md")
                if (eval_md_exists and run_id)
                else None,
                triage_json_path=str(iteration_dir / "triage.json") if triage_json_exists else None,
                triage_json_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/triage.json")
                if (triage_json_exists and run_id)
                else None,
                git_violation_path=str(iteration_dir / "git-violation.txt")
                if git_violation_exists
                else None,
                git_violation_uri=_maybe_uri(
                    harness_token, run_id, f"{sub_prefix}/git-violation.txt"
                )
                if (git_violation_exists and run_id)
                else None,
                verify_path=str(iteration_dir / "verify.txt") if verify_exists else None,
                verify_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/verify.txt")
                if (verify_exists and run_id)
                else None,
                sessions_path=str(iteration_dir / "sessions.json") if sessions_exists else None,
                sessions_uri=_maybe_uri(harness_token, run_id, f"{sub_prefix}/sessions.json")
                if (sessions_exists and run_id)
                else None,
            )
        )
    plan_sessions_exists = (run_dir / "plan" / "sessions.json").exists()
    git_state_exists = (run_dir / "inputs" / "git-state.txt").exists()
    prior_attempts_exists = (run_dir / "inputs" / "prior_attempts.md").exists()  # §L8.3
    design_flaws_exists = (run_dir / "design_flaws.json").exists()
    return ArtifactIndex(
        plan_path=str(run_dir / "plan" / "plan.md"),
        plan_uri=_maybe_uri(harness_token, run_id, "plan/plan.md") if run_id else None,
        plan_sessions_path=str(run_dir / "plan" / "sessions.json")
        if plan_sessions_exists
        else None,
        plan_sessions_uri=_maybe_uri(harness_token, run_id, "plan/sessions.json")
        if (plan_sessions_exists and run_id)
        else None,
        iterations=iterations,
        status_log_path=str(run_dir / "status.log"),
        status_log_uri=_maybe_uri(harness_token, run_id, "status.log") if run_id else None,
        state_json_path=str(run_dir / "state.json"),
        state_json_uri=_maybe_uri(harness_token, run_id, "state.json") if run_id else None,
        git_state_path=str(run_dir / "inputs" / "git-state.txt") if git_state_exists else None,
        git_state_uri=_maybe_uri(harness_token, run_id, "inputs/git-state.txt")
        if (git_state_exists and run_id)
        else None,
        git_uncommitted_path=ledger.git_uncommitted_path,
        git_uncommitted_uri=_maybe_uri(harness_token, run_id, "inputs/git-uncommitted.txt")
        if (ledger.git_uncommitted_path and run_id)
        else None,
        unresolved_gaps_overflow_path=ledger.unresolved_overflow_path,
        unresolved_gaps_overflow_uri=_maybe_uri(
            harness_token, run_id, "unresolved-gaps-overflow.md"
        )
        if (ledger.unresolved_overflow_path and run_id)
        else None,
        design_flaw_gaps_overflow_path=ledger.design_flaw_overflow_path,
        design_flaw_gaps_overflow_uri=_maybe_uri(
            harness_token, run_id, "design-flaw-gaps-overflow.md"
        )
        if (ledger.design_flaw_overflow_path and run_id)
        else None,
        prior_attempts_path=str(run_dir / "inputs" / "prior_attempts.md")
        if prior_attempts_exists
        else None,
        prior_attempts_uri=_maybe_uri(harness_token, run_id, "inputs/prior_attempts.md")
        if (prior_attempts_exists and run_id)
        else None,
        design_flaws_path=str(run_dir / "design_flaws.json") if design_flaws_exists else None,
        design_flaws_uri=_maybe_uri(harness_token, run_id, "design_flaws.json")
        if (design_flaws_exists and run_id)
        else None,
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
    task_id: str | None = None,
    harness_token: str | None = None,
) -> RunResult:
    """Build the terminal RunResult for completed/incomplete/failed runs.

    Design: §6.3 terminal states are normal returns; failed-only fields are set
        only for failed status and traceback is already truncated by lifecycle.
        §C3 task_id is best-effort wire correlation, not ledger state. §R4.2 /
        §R4.3 URI companions populate only when harness_token is supplied.
    Implementation: compute runtime, discover artifacts numerically, and pass
        capped ledger lists plus optional harness_token into the result model.
    Example: result = build_result(status='completed', harness_token='tok', ...).
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
    failure_kind = _failure_kind_for(status)
    if failure_kind is not None:
        message = _tag_once(failure_kind, message) or message
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
        task_id=task_id,
        run_dir=str(run_dir),
        iterations_used=sm.iteration,
        runtime_seconds=runtime_seconds,
        completed_phases=ledger.completed_phases,
        unresolved_gaps=ledger.unresolved_gaps,
        design_flaw_gaps=ledger.design_flaw_gaps,
        artifacts=_artifact_index(run_dir, ledger, harness_token=harness_token, run_id=run_id),
        warnings=ledger.warnings,
        message=message,
        failed_phase=ledger.failed_phase if failed else None,
        error_class=ledger.error_class if failed else None,
        error_message=_tag_once(failure_kind, ledger.error_message)
        if failed and failure_kind is not None
        else None,
        traceback_truncated=ledger.traceback_truncated if failed else None,
        verification=verification,
        resumed_from_iteration=ledger.resumed_from_iteration,
        linked_prior_runs=list(ledger.linked_prior_runs),
        failure_kind=failure_kind,
    )
