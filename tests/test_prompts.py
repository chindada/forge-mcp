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


def test_no_prompt_mentions_playwright_or_browser_tools() -> None:
    """Pin the real §15 Playwright-excision guard (P-Inv 2).

    Design: P-Inv 2 — no shipped prompt may contain the case-insensitive
        substring "playwright" or a "browser_"-prefixed tool identifier;
        the old placeholder tokens left this invariant unguarded.
    Implementation: lower-case each packaged prompt body and assert neither
        forbidden substring is present.
    Example: pytest runs this test in the non-slow suite.
    """
    forbidden = ("playwright", "browser_")
    for name in PROMPTS:
        text = _read(name).lower()
        for token in forbidden:
            assert token not in text, name


def test_evaluator_probe_md_does_not_ship() -> None:
    """Pin that the real legacy file evaluator_probe.md is not shipped (P-Inv 2).

    Design: P-Inv 2 — the genuine legacy Playwright probe prompt was named
        evaluator_probe.md; the old test checked a nonexistent placeholder
        filename and so never guarded the real surface.
    Implementation: assert the package data file evaluator_probe.md is absent.
    Example: pytest runs this test in the non-slow suite.
    """
    assert not (files("forge_mcp.prompts") / "evaluator_probe.md").is_file()


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


def test_planner_forbids_absolute_path_and_default_save_location() -> None:
    """Pin the planner save-location override (P2.1).

    Design: the planner invokes superpowers:writing-plans, so it must keep
        the override forbidding an absolute path and the skill default
        docs/superpowers/plans save location.
    Implementation: read planner_system.md and assert both fragments.
    Example: pytest runs this test in the non-slow suite.
    """
    text = _read("planner_system.md")
    assert "absolute path" in text.lower()
    assert "docs/superpowers/plans" in text


def test_remediation_uses_relative_contract_path() -> None:
    """Pin the remediation relative-path rule, sans skill framing (P-Decision 3).

    Design: P-Decision 3 — remediation authors contract.md directly and never
        invokes the writing-plans skill, so it forbids an absolute path but
        must NOT carry the docs/superpowers/plans skill-default reference.
    Implementation: read evaluator_remediation.md, assert "absolute path" is
        present and "docs/superpowers/plans" is absent.
    Example: pytest runs this test in the non-slow suite.
    """
    text = _read("evaluator_remediation.md")
    assert "absolute path" in text.lower()
    assert "docs/superpowers/plans" not in text


def test_planner_scrubs_commit_steps() -> None:
    """Pin the planner commit-scrub instruction (P2.1).

    Design: the planner must tell writing-plans output to scrub/remove the
        skill's commit section because Rule 11 forbids git mutations.
    Implementation: read planner_system.md (lower-cased) and assert a
        scrub/remove verb plus a commit-step reference are both present.
    Example: pytest runs this test in the non-slow suite.
    """
    text = _read("planner_system.md").lower()
    assert "scrub" in text or "remove" in text
    assert "step 5: commit" in text or "git commit" in text


def test_remediation_forbids_git_in_contract() -> None:
    """Pin the remediation contract-level no-git rule (P-Inv 3).

    Design: P-Inv 3 — the remediation prompt must forbid the contract from
        instructing the generator to mutate git, replacing the dropped
        skill-override commit-scrub framing.
    Implementation: read evaluator_remediation.md and assert it contains
        "git commit" and the phrase "must never instruct".
    Example: pytest runs this test in the non-slow suite.
    """
    text = _read("evaluator_remediation.md")
    assert "git commit" in text
    assert "must never instruct" in text


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


def test_generator_has_budget_clause() -> None:
    """Pin the generator anti-premature-wrap budget clause (P-Inv 0).

    Design: P-Inv 0 — the generator drives a long-horizon turn and MUST carry
        an explicit budget / anti-premature-wrap clause; "Completeness beats
        brevity." is its load-bearing fragment.
    Implementation: canonicalize whitespace and assert the fragment is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("generator_system.md").read_text()
    fragment = "Completeness beats brevity."
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)


def test_generator_has_honesty_clause() -> None:
    """Pin the generator honest-non-convergence clause (P-Inv 0).

    Design: P-Inv 0 — the generator MUST carry an honest-non-convergence clause
        (implement what you can; record the blocker; never fabricate done).
    Implementation: canonicalize whitespace and assert the fragment is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("generator_system.md").read_text()
    fragment = "honest non-convergence is a designed outcome"
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)


def test_evaluator_has_good_gap_example() -> None:
    """Pin the evaluator good-gap actionability example (P3 principle 4).

    Design: §P3 principle 4 — a concrete worked example raises gap quality; the
        evaluator carries the "A good gap is specific" lead-in.
    Implementation: canonicalize whitespace and assert the fragment is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("evaluator_system.md").read_text()
    fragment = "A good gap is specific"
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)


def test_triage_has_accept_vs_demote_example() -> None:
    """Pin the triage accept-vs-demote worked example (P3 principle 4).

    Design: §P3 principle 4 — the triage prompt carries an example contrasting
        an acceptable verbatim citation with a title-collision demotion.
    Implementation: canonicalize whitespace and assert the fragment is present.
    Example: pytest runs this prompt pin in the non-slow suite.
    """
    from importlib.resources import files

    from forge_mcp.orchestrator.triage import canonicalize_for_citation

    text = files("forge_mcp.prompts").joinpath("evaluator_triage.md").read_text()
    fragment = "For example: a gap citing the exact sentence"
    assert canonicalize_for_citation(fragment) in canonicalize_for_citation(text)
