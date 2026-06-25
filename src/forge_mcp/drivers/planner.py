"""Planner stage (§5.1 superseded by §3): convert a spec into a single structured Plan."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import Plan
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_planner(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    plan_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> Plan:
    """Run the Planner stage and return a single validated Plan (§5.1 superseded).

    Design: §3 the Planner reads the frozen spec and returns exactly one Plan; it
        is git-mutation-denied (PreToolUse git-deny hook) and runs under
        bypassPermissions so it can read the repo to ground the plan but cannot
        commit. The multi-plan PlanSet/DAG is gone — one plan, one tree.
    Implementation: build options with the planner_system prompt,
        output_format=envelope(plan_schema), git-deny hooks, and the provided cwd
        — the caller passes plan_schema=Plan.model_json_schema(); call runner.run
        with spec_text as the prompt; validate the structured_output into a Plan.
    Example: ``await run_planner(runner, spec_text="# spec",
        plan_schema=Plan.model_json_schema(), cwd=Path("/r"))`` returns one Plan.
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
    return Plan(**(result.structured_output or {}))
