"""Generator stage (§5): run one autonomous Codex turn that edits target_dir directly."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge_mcp.drivers._codex import CodexEvent, CodexRunner

# Surface → capability preface (no "Skill tool" wording per §10.1)
_SURFACE_PREFACE: dict[str, str] = {
    "backend": "Apply plan-execution capabilities to implement the contract below.",
    "frontend": "Apply frontend-design capabilities to implement the contract below.",
}
_DEFAULT_PREFACE = "Implement the contract below."


async def run_generator(
    runner: CodexRunner,
    *,
    contract_text: str,
    target_dir: Path,
    surface: str,
    run_log_path: Path | None = None,
) -> list[CodexEvent]:
    """Run one autonomous Codex turn that edits *target_dir* directly (§5).

    Design: §5 the Generator edits the repository in place under workspace-write
        with network access and no human approval, bounded to *target_dir* (writes
        outside it fail closed). There is no copy-sandbox and no change_set — the
        edits ARE the output, left in *target_dir* for the human's git to review.
    Implementation: build the Codex config rooted at *target_dir* via
        build_codex_config(codex_bin=codex_bin(), cwd=target_dir); compose
        instructions from the generator_system prompt, a surface-specific capability
        preface, and *contract_text*; stream the turn to exhaustion via ``async
        for`` and return the collected CodexEvents. The thread itself runs
        sandbox=workspace_write, approval_mode=deny_all, with the inline network
        config (see drivers/_codex.py).
    Example: ``await run_generator(runner, contract_text="do X", target_dir=p,
        surface="backend")`` returns all events emitted while editing *p*.
    """
    from forge_mcp.config import codex_bin
    from forge_mcp.drivers._codex import CodexEvent, build_codex_config  # noqa: F401
    from forge_mcp.prompts import load_prompt

    config = build_codex_config(codex_bin=str(codex_bin()), cwd=target_dir)
    preface = _SURFACE_PREFACE.get(surface, _DEFAULT_PREFACE)
    instructions = f"{load_prompt('generator_system')}\n\n{preface}\n\n{contract_text}"

    events: list[CodexEvent] = []
    async for event in runner.generate(
        instructions=instructions,
        config=config,
        run_log_path=run_log_path,
    ):
        events.append(event)
    return events
