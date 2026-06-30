"""Planner stage (§6): convert a spec into a single structured Plan."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import Plan
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope
from forge_mcp.verifier import scope_verification_command


async def run_planner(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    plan_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> Plan:
    """Run the Planner stage and return a single validated Plan (§6).

    Design: §3 the Planner reads the frozen spec and returns exactly one Plan; it
        is git-mutation-denied (PreToolUse git-deny hook) and runs under
        bypassPermissions so it can read the repo to ground the plan but cannot
        commit. The multi-plan PlanSet/DAG is gone — one plan, one tree.
    Implementation: build options with the planner_system prompt,
        output_format=envelope(plan_schema), git-deny hooks, and the provided cwd
        — the caller passes plan_schema=Plan.model_json_schema(); call runner.run
        with spec_text as the prompt; validate the structured_output into a Plan,
        then scope any structurally-unsatisfiable clean-tree gate out of its
        verification_command (recording the drop in the run log).
    Example: ``await run_planner(runner, spec_text="# spec",
        plan_schema=Plan.model_json_schema(), cwd=Path("/r"))`` returns one Plan.
    """
    tee = run_log_tee(run_log_path) if run_log_path is not None else None
    options = build_options(
        system=load_prompt("planner_system"),
        output_format=envelope(plan_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=tee,
    )
    result = await runner.run(prompt=spec_text, options=options)
    plan = Plan(**(result.structured_output or {}))

    # Backstop the §6.5 contract: a clean-tree gate can never pass in a
    # never-committing direct-edit loop, so scope it out (keeping the correctness
    # conjuncts) and record the drop. The planner_system contract is the primary
    # control; this catches the inline case it misses without crashing the run.
    scoped, dropped = scope_verification_command(plan.verification_command)
    if dropped:
        plan = plan.model_copy(update={"verification_command": scoped})
        if tee is not None:
            tee(
                "forge: scoped verification_command — dropped non-completing "
                f"clean-tree gate(s) {dropped}; the direct-edit loop never commits, "
                "so a tree-cleanliness check can never pass in-loop (it belongs in "
                f"the project's post-commit CI). Kept: {scoped!r}"
            )
    return plan
