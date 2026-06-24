"""Stage prompt loader (§5)."""

from __future__ import annotations

from importlib.resources import files

PROMPT_NAMES = (
    "planner_system",
    "generator_system",
    "evaluator_system",
    "evaluator_triage",
    "remediation",
)


def load_prompt(name: str) -> str:
    """Load a stage prompt by name from the package resources (§5).

    Design: §5 prompts are file-based handoff content, versioned with the code
        and shipped in the wheel (pyproject force-include).
    Implementation: read prompts/<name>.md via importlib.resources.
    Example: load_prompt('planner_system') -> the planner system prompt text.
    """
    return (files("forge_mcp.prompts") / f"{name}.md").read_text(encoding="utf-8")
