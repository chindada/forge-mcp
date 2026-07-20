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


def test_claude_prompts_name_their_skill_capability():
    """Design: §5.1/§5.3/§10.1-§10.2 the Claude stage prompts must name their skill
        capability (the prompt-half of the "both halves" wiring), referenced by
        capability so the literal id stays [verify-against-installed].
    Implementation: assert the planner names the plan-writing capability and the
        evaluator names the code-review capability.
    Example: 'plan-writing capability' in planner_system; 'code-review capability'
        in evaluator_system.
    """
    planner = load_prompt("planner_system").lower()
    assert "plan-writing capability" in planner or "writing-plans skill" in planner
    evaluator = load_prompt("evaluator_system").lower()
    assert "code-review capability" in evaluator or "code-review skill" in evaluator
    remediation = load_prompt("remediation").lower()
    assert "plan-writing capability" in remediation or "writing-plans skill" in remediation
