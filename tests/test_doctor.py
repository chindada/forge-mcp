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
