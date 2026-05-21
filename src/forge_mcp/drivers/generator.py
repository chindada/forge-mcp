"""§10.2 GeneratorDriver — Codex implementation turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib.resources import files
from typing import Any

from ..artifacts import atomic_write_text
from ..runcontext import RunContext
from ._codex import CodexRunner, build_app_server_config, never_approval_mode, sandbox_policy_for

_STATUS_EVENT_KINDS = {"command_execution", "function_call", "file_change", "web_search"}


class GeneratorDriver:
    """Codex-backed generator phase driver.

    Design: §9.2 delegates implementation to Codex with no removed MCP
        server attachments after §15 excision.
    Implementation: read iteration contract, build Codex config/sandbox through
        the SDK seam, stream selected events to status, and backstop summary.md.
    Example: await GeneratorDriver(runner).implement(ctx, codex_bin='codex', status_cb=cb).
    """

    def __init__(self, runner: CodexRunner) -> None:
        """Store the Codex runner seam.

        Design: dependency injection keeps generator tests from importing or
            spawning the real Codex SDK.
        Implementation: assign the protocol object for later implement calls.
        Example: GeneratorDriver(fake_codex_runner).
        """
        self._runner = runner

    async def implement(
        self,
        ctx: RunContext,
        *,
        codex_bin: str,
        status_cb: Callable[..., Awaitable[None]],
        env: dict | None = None,
    ) -> None:
        """Run one Codex implementation turn for the current iteration.

        Design: §9.2 generator reads iteration-N/contract.md and writes only to
            target_dir plus that iteration directory; no MCP servers are passed.
        Implementation: construct seam configs, stream notable events to status,
            and write a backstop summary if the agent omitted one.
        Example: await driver.implement(ctx, codex_bin='codex', status_cb=cb).
        """
        if ctx.iteration_n is None:
            raise RuntimeError("GeneratorDriver requires ctx.iteration_n")
        if ctx.target_dir is None:
            raise RuntimeError("GeneratorDriver requires ctx.target_dir")
        iteration_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
        contract = (iteration_dir / "contract.md").read_text()
        system = (files("forge_mcp.prompts") / "generator_system.md").read_text()
        instructions = f"{system}\n\n# Contract\n\n{contract}"
        session = await self._runner.turn(
            instructions=instructions,
            server_config=build_app_server_config(codex_bin=codex_bin, cwd=ctx.target_dir, env=env),
            sandbox_policy=sandbox_policy_for(
                target_dir=ctx.target_dir, iteration_dir=iteration_dir
            ),
            approval_mode=never_approval_mode(),
            env=env,
        )
        await status_cb(kind="stream", agent="generator", message="turn started")
        async for event in session:
            kind = getattr(event, "kind", None) or getattr(event, "type", "")
            payload: Any = getattr(event, "payload", {})
            item_kind = payload.get("kind") if isinstance(payload, dict) else None
            if kind in {"turn/started", "turn/completed"}:
                await status_cb(kind="stream", agent="generator", message=kind)
            elif kind == "item/started" and item_kind in _STATUS_EVENT_KINDS:
                await status_cb(
                    kind="stream", agent="generator", message=f"item started: {item_kind}"
                )
        if not (iteration_dir / "summary.md").exists():
            atomic_write_text(
                iteration_dir / "summary.md",
                "# Summary\n\nGenerator turn completed (no agent-authored summary).\n",
            )
