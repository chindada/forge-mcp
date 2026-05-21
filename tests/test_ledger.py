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
