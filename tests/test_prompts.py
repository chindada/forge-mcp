# tests/test_prompts.py
from __future__ import annotations

import pytest

from forge_mcp.prompts import PROMPT_NAMES, load_prompt


@pytest.mark.parametrize(
    "name",
    [
        "planner_system",
        "generator_system",
        "evaluator_system",
        "evaluator_triage",
        "remediation",
    ],
)
def test_each_prompt_loads_nonempty(name: str):
    """Design: §5 each stage has a loadable, non-trivial prompt.
    Implementation: load_prompt returns substantial text.
    Example: load_prompt('planner_system') -> str length > 200.
    """
    assert name in PROMPT_NAMES
    assert len(load_prompt(name)) > 200


def test_generator_prompt_forbids_git_and_references_skill_by_capability():
    """Design: §5.2/§10.1 Codex has no Skill tool; reference by capability; forbid git.
    Implementation: assert key stances appear.
    Example: 'git commit' forbidden phrasing present.
    """
    text = load_prompt("generator_system").lower()
    assert "git" in text and "commit" in text
    assert "skill tool" not in text  # Codex references skills by behavior, not a tool
