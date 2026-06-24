"""Generator stage (§5.2): run one full-access Codex turn in the plan sandbox."""

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
    sandbox: Path,
    surface: str,
    run_log_path: Path | None = None,
) -> list[CodexEvent]:
    """Run one full-access Codex turn in *sandbox* and return all streamed events (§5.2).

    Design: §5.2 the Generator stage executes exactly one Codex turn with
        full filesystem access inside the plan's sandbox; it collects the
        streamed CodexEvents and returns them for downstream evaluation.
    Implementation: build the Codex config rooted at *sandbox*; compose
        instructions from the generator_system prompt, a surface-specific
        capability preface, and *contract_text*; stream events to exhaustion
        via ``async for``; return the collected list.
    Example: ``await run_generator(runner, contract_text="do X", sandbox=p, surface="backend")``
        returns all events emitted by the runner.
    """
    from forge_mcp.config import codex_bin
    from forge_mcp.drivers._codex import CodexEvent, build_codex_config  # noqa: F401
    from forge_mcp.prompts import load_prompt

    config = build_codex_config(codex_bin=str(codex_bin()), cwd=sandbox)
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
