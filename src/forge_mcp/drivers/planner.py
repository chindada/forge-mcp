"""§10.3 PlannerDriver — Claude-authored implementation plan."""

from __future__ import annotations

from importlib.resources import files

from ..artifacts import atomic_write_text
from ..runcontext import RunContext
from ._claude import (
    CLAUDE_SETTING_SOURCES,
    ClaudeRunner,
    build_options,
    collect_writes_to_basename,
    git_deny_hooks,
    truncate_for_warning,
)


class PlannerDriver:
    """Claude-backed planner phase driver.

    Design: §9.1 starts a fresh Claude session to produce plan/plan.md from
        run inputs without mutating target_dir.
    Implementation: invoke the ClaudeRunner seam with cwd=run_dir/plan and
        recover off-cwd Write tool content when needed.
    Example: await PlannerDriver(runner).write_plan(ctx).
    """

    def __init__(self, runner: ClaudeRunner) -> None:
        """Store the Claude runner seam.

        Design: dependency injection keeps driver tests independent of SDKs.
        Implementation: assign the protocol object to an instance attribute.
        Example: PlannerDriver(fake_runner).
        """
        self._runner = runner

    @property
    def last_session_id(self) -> str | None:
        """Expose the planner runner's last_session_id for artifacts (§C2.2).

        Design: orchestrator code writes sessions.json without branching by
            concrete SDK driver type.
        Implementation: passthrough read with a None default for fakes and
            fail-soft SDK capture paths.
        Example: sid = driver.last_session_id after write_plan returns.
        """
        return getattr(self._runner, "last_session_id", None)

    async def write_plan(self, ctx: RunContext) -> str | None:
        """§10.3 write run_dir/plan/plan.md via Claude.

        Design: §9.1 uses a fresh Claude session; off-cwd-write recovery returns
            a descriptor surfaced as a ledger warning.
        Implementation: load planner_system.md, run Claude in plan cwd, and
            recover the last Write tool_use targeting plan.md if missing.
        Example: await driver.write_plan(ctx).
        """
        plan_dir = ctx.run_dir / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        system = (files("forge_mcp.prompts") / "planner_system.md").read_text()
        prompt = "Read inputs/design.md and author plan.md in the current working directory."
        options = build_options(
            setting_sources=CLAUDE_SETTING_SOURCES,
            add_dirs=[ctx.run_dir / "inputs"],
            disallowed_tools=("Edit",),
            cwd=plan_dir,
            cli_path=ctx.claude_cli_path,
            hooks=git_deny_hooks(),
        )
        turn = await self._runner.run_with_messages(prompt=prompt, options=options, system=system)
        plan_path = plan_dir / "plan.md"
        if plan_path.exists():
            return None
        recovered = collect_writes_to_basename(turn.messages, "plan.md")
        if recovered is None:
            return None
        atomic_write_text(plan_path, recovered)
        return truncate_for_warning("recovered planner Write tool content for plan.md")
