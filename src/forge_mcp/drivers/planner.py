"""Planner stage (§5.1): convert a spec into a structured PlanSet."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import PlanSet
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_planner(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    plan_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> PlanSet:
    """Run the Planner stage and return a validated PlanSet (§5.1).

    Design: §5.1 the Planner receives the full design spec and returns a
        structured set of plans with dependency edges; it uses the
        plan-writing skill and is constrained by git-deny hooks so it
        cannot mutate the repository.
    Implementation: build options with the planner_system prompt, the
        plan JSON schema as output_format, git-deny hooks, and the
        provided cwd; call runner.run with spec_text as the prompt;
        validate the structured_output into a PlanSet.
    Example: ``await run_planner(runner, spec_text="# spec", plan_schema={...}, cwd=Path("/r"))``
        returns a PlanSet with one or more plans.
    """
    options = build_options(
        system=load_prompt("planner_system"),
        output_format=envelope(plan_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    result = await runner.run(prompt=spec_text, options=options)
    return PlanSet(**(result.structured_output or {}))
