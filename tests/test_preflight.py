"""§6.4 preflight ordering and canonicalization pins."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError

from forge_mcp.config import RunConfig
from forge_mcp.models import RunForgeInput
from forge_mcp.preflight import _canonicalize_target_dir, prepare_run


async def test_target_dir_missing_raises_invalid_params(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    inputs = RunForgeInput(target_dir=str(tmp_path / "missing"), design_doc_content="x")
    with pytest.raises(McpError) as exc:
        await prepare_run(inputs, RunConfig.from_env())
    assert exc.value.error.code == -32602


async def test_lock_busy_raises_server_error(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.lockfile import TargetLock

    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    held = TargetLock(target_dir / ".harness" / "run.lock")
    held.acquire()
    try:
        inputs = RunForgeInput(target_dir=str(target_dir), design_doc_content="x")
        with pytest.raises(McpError) as exc:
            await prepare_run(inputs, RunConfig.from_env())
        assert exc.value.error.code == -32000
    finally:
        held.release()


async def test_abspath_not_realpath_pins_symlinked_alias(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    alias = target_dir.parent / (target_dir.name + "_alias")
    os.symlink(target_dir, alias)
    assert _canonicalize_target_dir(str(target_dir)) != _canonicalize_target_dir(str(alias))


async def test_prepare_run_does_not_write_canonical_design(target_dir: Path, monkeypatch) -> None:
    """Pin preflight validation-only design handling.

    Design: §8.1 canonicalize_design, not prepare_run, writes inputs/design.md.
    Implementation: monkeypatch slow environment probes and inspect run dir after preflight.
    Example: prepared.harness_dir / run_id / 'inputs/design.md' is absent.
    """
    from forge_mcp import preflight as preflight_mod

    monkeypatch.setattr(preflight_mod, "check_claude_cli", lambda _config: ("claude", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "check_codex", AsyncMock(return_value=("codex", "OK", "x")))
    monkeypatch.setattr(preflight_mod, "check_claude_auth", lambda: ("auth", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "probe_required_skills", AsyncMock(return_value=None))
    prepared = await prepare_run(
        RunForgeInput(target_dir=str(target_dir), design_doc_content="design"), RunConfig()
    )
    try:
        assert not (prepared.harness_dir / prepared.run_id / "inputs" / "design.md").exists()
    finally:
        prepared.lock.release()
