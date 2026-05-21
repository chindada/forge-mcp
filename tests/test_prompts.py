"""§10.4 / §15 prompts ship from the package and stay browser-tool-free."""

from __future__ import annotations

from importlib.resources import files

PROMPTS = (
    "planner_system.md",
    "generator_system.md",
    "evaluator_system.md",
    "evaluator_triage.md",
    "evaluator_remediation.md",
)


def _read(name: str) -> str:
    """Read one packaged prompt.

    Design: prompt tests pin wheel-shipped package data.
    Implementation: use importlib.resources against forge_mcp.prompts.
    Example: _read('planner_system.md').
    """
    return (files("forge_mcp.prompts") / name).read_text()


def test_all_five_prompts_ship() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    for name in PROMPTS:
        assert _read(name).strip(), name


def test_no_prompt_mentions_removed_removed_tool_surfaces() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    forbidden = ("removed_removed_tool_surface", "removed_tool_")
    for name in PROMPTS:
        text = _read(name).lower()
        for token in forbidden:
            assert token not in text, name


def test_removed_eval_probe_md_does_not_ship() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert not (files("forge_mcp.prompts") / "removed_eval_probe.md").is_file()


def test_generator_forbids_git_mutations() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    text = _read("generator_system.md")
    for mutation in (
        "git commit",
        "git add",
        "git push",
        "git branch",
        "git tag",
        "git rebase",
        "git reset --hard",
        "git worktree",
    ):
        assert mutation in text


def test_plan_and_remediation_forbid_absolute_paths_and_default_save_location() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    for name in ("planner_system.md", "evaluator_remediation.md"):
        text = _read(name)
        assert "absolute path" in text.lower()
        assert "docs/superpowers/plans" in text


def test_plan_and_remediation_scrub_commit_steps() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    for name in ("planner_system.md", "evaluator_remediation.md"):
        text = _read(name).lower()
        assert "scrub" in text or "remove" in text
        assert "step 5: commit" in text or "git commit" in text


def test_planner_prompt_states_general_mcp_tool_id_rule() -> None:
    """Pin a forge-mcp behavior.

    Design: §15 — the OVERRIDE block in planner_system.md must preserve
        the general 'don't leak MCP tool identifiers' rule (any
        `mcp__<server>__<tool>` token), not only browser-specific tools.
    Implementation: read the shipped prompt and assert it contains the
        general-rule wording and the `mcp__<server>__<tool>` template.
    Example: pytest runs this test in the non-slow suite.
    """
    from importlib import resources

    text = resources.files("forge_mcp.prompts").joinpath("planner_system.md").read_text()
    assert "MCP tool identifier" in text
    assert "mcp__<server>__<tool>" in text


def test_remediation_prompt_states_general_mcp_tool_id_rule() -> None:
    """Pin a forge-mcp behavior.

    Design: §15 — evaluator_remediation.md's OVERRIDE block carries the
        same general 'don't leak MCP tool identifiers' rule.
    Implementation: read the shipped prompt and assert it contains the
        general-rule wording and the `mcp__<server>__<tool>` template.
    Example: pytest runs this test in the non-slow suite.
    """
    from importlib import resources

    text = resources.files("forge_mcp.prompts").joinpath("evaluator_remediation.md").read_text()
    assert "MCP tool identifier" in text
    assert "mcp__<server>__<tool>" in text
