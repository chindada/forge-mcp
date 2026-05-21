"""§H6 handoff: prose well-formedness validation (empty=ok contract)."""

from __future__ import annotations

from forge_mcp.orchestrator.handoff import (
    MIN_CONTRACT_CHARS,
    validate_contract,
    validate_plan,
    validate_summary,
)


def test_empty_contract_flagged() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert validate_contract("") == ["contract is empty"]
    assert validate_contract("   \n\t ") == ["contract is empty"]


def test_too_short_contract_flagged() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    problems = validate_contract("# H\nimplement")
    assert any("too short" in p for p in problems)


def test_no_heading_flagged() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    body = "implement the acceptance criteria and verify tests pass. " * 3
    assert "contract has no markdown heading" in validate_contract(body)


def test_no_actionable_signal_flagged() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    body = "# Heading\n" + ("lorem ipsum dolor sit amet consectetur. " * 4)
    assert "contract lacks any actionable/acceptance signal" in validate_contract(body)


def test_well_formed_contract_returns_empty() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    body = (
        "# Remediation Contract\n\n"
        "Implement the following steps and verify each acceptance criterion "
        "with tests; the build must pass before this is considered done.\n"
    )
    assert len(body.strip()) >= MIN_CONTRACT_CHARS
    assert validate_contract(body) == []


def test_validate_plan_and_summary_light_rules() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert validate_plan("# Plan\nbuild it thoroughly with care") == []
    assert "plan has no markdown heading" in validate_plan("just prose, no heading here")
    assert validate_summary("") != []
    assert validate_summary("# Summary\nall done and verified") == []
