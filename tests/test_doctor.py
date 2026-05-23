"""§14 doctor synchronous environment checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_mcp.config import RunConfig
from forge_mcp.doctor import (
    check_disk_space,
    check_harness_writable,
    check_target_dir_writable,
    disk_space_warn_if_low,
)
from forge_mcp.orchestrator.ledger import RunLedger


def test_sync_checks_report_ok_for_tmp_dirs(target_dir, harness_dir) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert check_target_dir_writable(target_dir)[1] == "OK"
    assert check_harness_writable(harness_dir)[1] == "OK"
    assert check_disk_space(target_dir)[1] in {"OK", "WARN"}
    assert RunConfig.from_env().codex_bin


@pytest.mark.asyncio
async def test_disk_space_warn_if_low_emits_warning_when_below_threshold(
    tmp_path, monkeypatch
) -> None:
    """Pin §8.1/§14 low disk warning side effects.

    Design: disk pressure is reported as warning status plus ledger entry.
    Implementation: monkeypatch shutil.disk_usage below the 5 GiB threshold.
    Example: ledger.warnings contains a disk-free message.
    """
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: MagicMock(free=1024))
    status = MagicMock(update=AsyncMock())
    ledger = RunLedger()
    await disk_space_warn_if_low(tmp_path, status, MagicMock(), ledger)
    status.update.assert_awaited()
    assert any(
        "disk" in warning.lower() or "free" in warning.lower() for warning in ledger.warnings
    )


@pytest.mark.asyncio
async def test_disk_space_warn_if_low_silent_when_ok(tmp_path, monkeypatch) -> None:
    """Pin §14 no-op behavior when disk space is sufficient.

    Design: normal free-space probes should not add noise to run warnings.
    Implementation: monkeypatch shutil.disk_usage above the threshold.
    Example: status.update.assert_not_awaited().
    """
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: MagicMock(free=50 * 1024**3))
    status = MagicMock(update=AsyncMock())
    ledger = RunLedger()
    await disk_space_warn_if_low(tmp_path, status, MagicMock(), ledger)
    status.update.assert_not_awaited()
    assert ledger.warnings == []


def test_check_claude_auth_accepts_env_var(monkeypatch) -> None:
    """Pin A4 — ANTHROPIC_API_KEY or OAuth token yields OK env-var.

    Design: env credentials are the first accepted source.
    Implementation: set the env var and assert an OK status.
    Example: pytest tests/test_doctor.py -k env_var -v.
    """
    from forge_mcp.doctor import check_claude_auth

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    label, status, _ = check_claude_auth()
    assert (label, status) == ("claude_auth", "OK")


def test_check_claude_auth_accepts_dot_credentials_file(monkeypatch, tmp_path) -> None:
    """Pin A4 — non-empty .credentials.json under config dir is OK.

    Design: A4 uses the leading-dot credentials filename.
    Implementation: write .credentials.json into CLAUDE_CONFIG_DIR and assert OK.
    Example: pytest tests/test_doctor.py -k dot_credentials -v.
    """
    from forge_mcp.doctor import check_claude_auth

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".credentials.json").write_text('{"token": "x"}')
    label, status, _ = check_claude_auth()
    assert (label, status) == ("claude_auth", "OK")


def test_check_claude_auth_keychain_default_service(monkeypatch) -> None:
    """Pin A4 — Darwin Keychain hit with no suffix is OK.

    Design: on Darwin, security find-generic-password returncode 0 counts as auth.
    Implementation: force sys.platform, clear env, and stub subprocess.run.
    Example: pytest tests/test_doctor.py -k keychain_default -v.
    """
    import subprocess

    import forge_mcp.doctor as doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(doctor.sys, "platform", "darwin", raising=False)
    captured = {}

    def fake_run(cmd, **kwargs):
        """Capture security command and return success.

        Design: test must not invoke the real Keychain.
        Implementation: record cmd and return CompletedProcess(0).
        Example: fake_run(['security'], timeout=10).
        """
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor.Path, "home", staticmethod(lambda: doctor.Path("/nonexistent-home")))
    label, status, _ = doctor.check_claude_auth()
    assert (label, status) == ("claude_auth", "OK")
    assert "Claude Code-credentials" in " ".join(captured["cmd"])


def test_check_claude_auth_keychain_suffix_when_config_dir_set(monkeypatch, tmp_path) -> None:
    """Pin A4 — Keychain service gets sha256(abspath(config_dir))[:8] suffix.

    Design: CLAUDE_CONFIG_DIR uses a per-config-dir Keychain service suffix.
    Implementation: set empty config dir, stub security success, assert suffix.
    Example: pytest tests/test_doctor.py -k keychain_suffix -v.
    """
    import hashlib
    import os
    import subprocess

    import forge_mcp.doctor as doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(doctor.sys, "platform", "darwin", raising=False)
    expected = hashlib.sha256(os.path.abspath(str(tmp_path)).encode()).hexdigest()[:8]
    captured = {}

    def fake_run(cmd, **kwargs):
        """Capture security command and return success.

        Design: test must not invoke the real Keychain.
        Implementation: record cmd and return CompletedProcess(0).
        Example: fake_run(['security'], timeout=10).
        """
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    label, status, _ = doctor.check_claude_auth()
    assert (label, status) == ("claude_auth", "OK")
    assert f"Claude Code-credentials-{expected}" in " ".join(captured["cmd"])


def test_check_claude_auth_fails_when_no_source(monkeypatch, tmp_path) -> None:
    """Pin A4 — FAIL when env, file, and Keychain all miss.

    Design: Rule 8 — no auth source means a hard FAIL status from doctor.
    Implementation: clear env, point config dir at empty tmp, and stub security fail.
    Example: pytest tests/test_doctor.py -k no_source -v.
    """
    import subprocess

    import forge_mcp.doctor as doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b""),
    )
    label, status, _ = doctor.check_claude_auth()
    assert (label, status) == ("claude_auth", "FAIL")
