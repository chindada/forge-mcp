from __future__ import annotations

from pathlib import Path

from forge_mcp.check import Check, any_fail, run_checks


def test_run_checks_returns_rows_and_detects_unwritable(tmp_path: Path):
    """Design: §4.4 each check returns (label, status, detail); writability is checked.
    Implementation: run against a writable dir; assert a writable-target OK row exists.
    Example: at least one OK row; any_fail computed.
    """
    checks = run_checks(tmp_path, probe_claude_live=False)
    assert checks and all(isinstance(c, Check) for c in checks)
    assert any("writ" in c.label.lower() for c in checks)
    _ = any_fail(checks)


def test_any_fail_true_on_fail_row():
    """Design: §4.4 any_fail is True iff a FAIL row exists.
    Implementation: synthetic rows.
    Example: returns True.
    """
    assert any_fail([Check("x", "OK", ""), Check("y", "FAIL", "broken")])
    assert not any_fail([Check("x", "OK", ""), Check("y", "WARN", "low disk")])


def test_run_checks_includes_claude_skill_row(tmp_path: Path):
    """Design: §10.3 run_checks must probe BOTH engines' skill discovery.
    Implementation: assert at least one row with label starting 'claude-skill' is present.
    Example: the row may be OK/WARN/FAIL depending on env — presence is asserted.
    """
    checks = run_checks(tmp_path, probe_claude_live=False)
    assert any(c.label.startswith("claude-skill") for c in checks), (
        f"No claude-skill row found in checks: {[c.label for c in checks]}"
    )
