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


def test_planner_system_md_has_prior_attempts_addendum() -> None:
    """Pin §L13.4 prior-attempts planner directive.

    Design: planner must read prior attempts before writing a plan so lineage
        can influence strategy selection.
    Implementation: canonicalize prompt whitespace and assert the directive
        fragment is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("planner_system.md").read_text()
    expected = """If `inputs/prior_attempts.md` exists, read it BEFORE writing the plan."""
    assert canonicalize_for_citation(expected) in canonicalize_for_citation(text)


def test_planner_system_md_has_anti_anchoring_rule() -> None:
    """Pin §L13.4 planner anti-anchoring rule.

    Design: the planner must choose different strategies rather than refining
        approaches that previous terminal runs already failed.
    Implementation: canonicalize prompt whitespace and assert the load-bearing
        DIFFERENT-strategy phrase is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("planner_system.md").read_text()
    fragment = "your plan MUST propose a DIFFERENT implementation strategy"
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)


def test_planner_system_md_has_write_scope_paragraph() -> None:
    """Pin §L-Inv 6 planner write-scope directive.

    Design: planners may read inputs/prior_attempts.md but must not poison
        orchestrator-owned inputs or other run artifacts.
    Implementation: canonicalize prompt whitespace and assert the write-scope
        prohibition is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("planner_system.md").read_text()
    fragment = "You MUST NOT write to any path outside the `plan/` directory"
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)


def test_evaluator_remediation_md_has_cross_run_directive() -> None:
    """Pin §L13.4 remediation anti-anchoring directive.

    Design: evaluator remediation must not ask the next generator to repeat a
        documented failed prior-run strategy.
    Implementation: canonicalize prompt whitespace and assert the remediation
        DIFFERENT-strategy phrase is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("evaluator_remediation.md").read_text()
    fragment = (
        "must propose a DIFFERENT implementation strategy than any documented-failed approach"
    )
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)
