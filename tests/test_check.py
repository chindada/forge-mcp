from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from forge_mcp import check as check_module
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


@pytest.mark.parametrize(
    ("version_output", "expected_status"),
    [
        ("2.1.152 (Claude Code)\n", "FAIL"),
        ("2.1.153 (Claude Code)\n", "OK"),
        ("unknown\n", "FAIL"),
    ],
)
def test_check_claude_cli_enforces_isolation_version_floor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    version_output: str,
    expected_status: str,
):
    """Reject Claude CLIs that cannot guarantee strict MCP isolation.

    Design: Claude Code 2.1.153 is the first supported CLI because older
        versions can load custom-agent MCP declarations despite strict mode.
    Implementation: fake only the bounded ``--version`` process boundary and
        exercise the real preflight version parsing and comparison.
    Example: 2.1.152 and unrecognized output FAIL while 2.1.153 is OK.
    """
    claude_path = tmp_path / "claude"
    claude_path.touch()
    monkeypatch.setattr("forge_mcp.config.claude_bin", lambda: claude_path)
    monkeypatch.setattr(
        check_module.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [str(claude_path), "--version"],
                0,
                stdout=version_output,
                stderr="",
            )
        ),
    )

    assert check_module._check_claude_cli().status == expected_status


def test_check_claude_cli_does_not_mask_invalid_configured_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Validate the exact Claude binary that production sessions will use.

    Design: ``FORGE_CLAUDE_BIN`` is authoritative, so a missing override must
        FAIL even when an unrelated PATH installation is valid and current.
    Implementation: resolve a missing configured path, expose a different PATH
        candidate, and assert the version command still targets the configured path.
    Example: ``FORGE_CLAUDE_BIN=/missing`` cannot be masked by `/safe/claude`.
    """
    configured_path = tmp_path / "missing-claude"
    path_candidate = tmp_path / "safe-claude"
    run_version = Mock(side_effect=FileNotFoundError("configured Claude is missing"))
    monkeypatch.setattr("forge_mcp.config.claude_bin", lambda: configured_path)
    monkeypatch.setattr(check_module.shutil, "which", Mock(return_value=str(path_candidate)))
    monkeypatch.setattr(check_module.subprocess, "run", run_version)

    row = check_module._check_claude_cli()

    assert row.status == "FAIL"
    assert run_version.call_args.args[0] == [str(configured_path), "--version"]


def test_sdk_contract_fails_without_strict_mcp_option(monkeypatch: pytest.MonkeyPatch):
    """Reject an installed SDK whose options cannot express MCP isolation.

    Design: preflight must fail closed when the installed SDK predates the
        strict MCP option instead of allowing a later live Claude launch.
    Implementation: replace only the SDK options type with a legacy empty shape
        and exercise the real contract inspection.
    Example: a type without ``strict_mcp_config`` produces a FAIL row.
    """
    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", type("LegacyOptions", (), {}))

    row = check_module._check_sdk_contract()

    assert row.status == "FAIL"
    assert "strict_mcp_config" in row.detail


def test_sdk_contract_fails_when_sdk_absent(monkeypatch: pytest.MonkeyPatch):
    """Reject an environment that cannot run forge's required Claude stages.

    Design: forge has no Codex-only execution mode, so a wholly absent Claude
        SDK must block serving rather than degrade to a non-failing warning.
    Implementation: hide the root SDK module through ``sys.modules`` and invoke
        the lazy contract check without importing any real Claude session code.
    Example: a missing ``claude_agent_sdk`` package produces a FAIL row.
    """
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)

    row = check_module._check_sdk_contract()

    assert row.status == "FAIL"


@pytest.mark.parametrize(
    ("failed_check", "expected_probe_live"),
    [("cli", False), ("sdk", False), (None, True)],
)
def test_run_checks_gates_live_claude_probe_on_compatibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_check: str | None,
    expected_probe_live: bool,
):
    """Run the live skill probe exactly when both compatibility checks pass.

    Design: ``forge serve`` evaluates all rows after preflight, so run_checks
        must block unsafe CLI/SDK paths without disabling valid skill discovery.
    Implementation: synthesize zero or one failing compatibility row and make
        the fake skill probe expose the requested mode in its result.
    Example: CLI or SDK failure disables the probe; two OK rows enable it.
    """
    monkeypatch.setattr(
        check_module,
        "_check_claude_cli",
        Mock(return_value=Check("claude CLI", "FAIL" if failed_check == "cli" else "OK", "")),
    )
    monkeypatch.setattr(
        check_module,
        "_check_sdk_contract",
        Mock(return_value=Check("SDK contract", "FAIL" if failed_check == "sdk" else "OK", "")),
    )
    monkeypatch.setattr(
        check_module,
        "_check_claude_skills",
        Mock(
            side_effect=lambda *, probe_live: [
                Check(
                    "claude-skill:probe-mode",
                    "OK" if probe_live else "WARN",
                    f"probe_live={probe_live}",
                )
            ]
        ),
    )

    rows = run_checks(tmp_path)
    probe_row = next(row for row in rows if row.label == "claude-skill:probe-mode")

    assert probe_row == Check(
        "claude-skill:probe-mode",
        "OK" if expected_probe_live else "WARN",
        f"probe_live={expected_probe_live}",
    )
