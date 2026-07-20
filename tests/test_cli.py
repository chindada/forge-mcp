from __future__ import annotations

from typer.testing import CliRunner

from forge_mcp.cli import app

runner = CliRunner()


def test_check_command_runs_and_reports(tmp_path):
    """Design: §4.4 `forge check` prints rows and sets a non-zero exit on FAIL.
    Implementation: invoke check on a writable dir; assert it runs and prints labels.
    Example: output contains a status token.
    """
    result = runner.invoke(app, ["check", "--no-live-claude", "--target-dir", str(tmp_path)])
    assert result.exit_code in (0, 1)  # 1 if an environment probe FAILs (e.g. SDK absent)
    assert any(tok in result.stdout for tok in ("OK", "WARN", "FAIL"))
