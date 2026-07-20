"""Evaluator stage (§6): gap-finding eval pass and triage pass."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import EvalResult, TriageResult
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_evaluator(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    eval_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> EvalResult:
    """Run the Evaluator stage and return a validated EvalResult (§6).

    Design: §6 the Evaluator diffs the code under *cwd* against the frozen
        spec_text and emits a structured list of gaps; it is git-mutation-denied
        (PreToolUse git-deny hook) under bypassPermissions so it can read the
        repo but cannot commit. With the single-plan direct-edit harness the
        evaluated tree IS *cwd* (target_dir), so the now-unused sandbox parameter
        is gone — *cwd* alone names the directory.
    Implementation: build options with the evaluator_system prompt, the eval JSON
        schema as output_format, git-deny hooks, and the provided cwd; compose a
        prompt that names *cwd* and the frozen spec; call runner.run; validate the
        structured_output into an EvalResult.
    Example: ``await run_evaluator(runner, spec_text="# spec",
        eval_schema={...}, cwd=Path("/r"))`` returns an EvalResult.
    """
    options = build_options(
        system=load_prompt("evaluator_system"),
        output_format=envelope(eval_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    prompt = (
        f"## Frozen design spec\n\n{spec_text}\n\n"
        f"## Project path\n\n{cwd}\n\n"
        "Diff the code in the project above against the frozen design spec above and "
        "report all gaps."
    )
    result = await runner.run(prompt=prompt, options=options)
    return EvalResult(**(result.structured_output or {}))


async def run_triage(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    eval_result: EvalResult,
    triage_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> TriageResult:
    """Run the Triage stage and return a validated TriageResult (§6).

    Design: §6 the Triage stage classifies each gap from the eval pass as
        either a code bug or a design fault; a design fault requires verbatim
        citations from the frozen spec_text to pass the citation gate; the
        orchestrator (not this function) applies any amendments to spec.md.
    Implementation: build options with the evaluator_triage prompt, the triage
        JSON schema as output_format, git-deny hooks, and the provided cwd;
        compose a prompt that includes the frozen spec and the serialised gaps;
        call runner.run; validate the structured_output into a TriageResult.
    Example: ``await run_triage(runner, spec_text="# spec", eval_result=er,
        triage_schema={...}, cwd=Path("/r"))`` returns a TriageResult.
    """
    options = build_options(
        system=load_prompt("evaluator_triage"),
        output_format=envelope(triage_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    gaps_text = "\n".join(
        f"- {g.title} ({g.severity}): {g.current_state} → {g.expected_state}"
        for g in eval_result.gaps
    )
    prompt = (
        f"## Frozen design spec\n\n{spec_text}\n\n"
        f"## Gaps to triage\n\n{gaps_text or '(none)'}\n\n"
        "Classify each gap as a code bug or a design fault. "
        "For design faults, cite verbatim sections from the spec above."
    )
    result = await runner.run(prompt=prompt, options=options)
    return TriageResult(**(result.structured_output or {}))
