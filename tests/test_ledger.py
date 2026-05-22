"""§8.3 RunLedger defaults."""

from __future__ import annotations

from forge_mcp.orchestrator.ledger import RunLedger


def test_ledger_defaults_are_empty() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ledger = RunLedger()
    assert ledger.warnings == []
    assert ledger.lock_released is False


def test_ledger_linked_prior_runs_defaults_empty() -> None:
    """§L8.5 — linked_prior_runs defaults empty.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.orchestrator.ledger import RunLedger

    assert RunLedger().linked_prior_runs == []


def test_ledger_lineage_overflow_path_defaults_none() -> None:
    """§L8.5 — lineage_overflow_path defaults None.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.orchestrator.ledger import RunLedger

    assert RunLedger().lineage_overflow_path is None
