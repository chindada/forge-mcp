"""Remediation stage (§4): Claude writes a focused plan to close the open gaps."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import EvalGap, GapSummary, RemediationResult
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_remediation(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    plan_body: str,
    gaps: list[EvalGap],
    synthesized: list[GapSummary],
    nudge: bool,
    cwd: Path,
    run_log_path: Path | None = None,
) -> str:
    """Run the Remediation stage and return the next iteration's contract (§4).

    Design: §4 a non-completing iteration produces a remediation contract that
        instructs the next Generator turn to close the still-open gaps. Rather
        than re-emitting the whole plan body with a gaps footnote, Claude writes
        a focused, repo-grounded plan from the open gaps — so each iteration's
        contract is a genuine plan to fix the previous gap, not a near-identical
        restatement. It is git-mutation-denied and rooted at *cwd* (target_dir)
        so it can read the actual code but cannot commit. The synthesized verify
        blocker is surfaced too, and a NUDGE signal asks the agent to vary its
        approach.
    Implementation: build options with the remediation prompt, a RemediationResult
        output_format (so the plan returns as structured output, NOT freeform turn
        text — which would carry the agent's interstitial narration), git-deny
        hooks, and *cwd*; compose a prompt with the frozen spec, the project path,
        the open gaps, any synthesized blockers, the original plan as reference,
        and the nudge note when set; call runner.run and return the validated
        contract field.
    Example: ``await run_remediation(runner, spec_text="# spec", plan_body="# plan",
        gaps=[g], synthesized=[], nudge=False, cwd=Path("/r"))`` returns the
        remediation contract Markdown.
    """
    options = build_options(
        system=load_prompt("remediation"),
        output_format=envelope(RemediationResult.model_json_schema()),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    gaps_text = "\n".join(f"- {g.title} ({g.severity}): {g.suggested_fix}" for g in gaps)
    blockers_text = "\n".join(f"- {s.title} — resolve this blocker." for s in synthesized)
    sections = [
        f"## Frozen design spec\n\n{spec_text}",
        f"## Project path\n\n{cwd}",
        f"## Still-open gaps to close\n\n{gaps_text or '(none)'}",
    ]
    if blockers_text:
        sections.append(f"## Synthesized blockers\n\n{blockers_text}")
    sections.append(f"## Original plan (reference only — do not restate)\n\n{plan_body}")
    if nudge:
        sections.append(
            "## NUDGE\n\nPrior iterations made no progress on these gaps. "
            "Vary your approach and state plainly what to do differently."
        )
    sections.append(
        "Write a focused remediation plan that closes the still-open gaps above. "
        "Inspect the project to ground each gap, then return the plan in the contract field."
    )
    prompt = "\n\n".join(sections)
    result = await runner.run(prompt=prompt, options=options)
    return RemediationResult(**(result.structured_output or {})).contract
