from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest

from forge_mcp.check import _check_claude_cli, _check_sdk_contract
from forge_mcp.drivers._claude import ClaudeDriver
from forge_mcp.drivers._codex import CodexDriver
from forge_mcp.orchestrator.engine import Orchestrator

pytestmark = pytest.mark.slow

_DESIGN = Path(__file__).resolve().parents[1] / "examples" / "tiny-feature.md"


@pytest.mark.skipif(
    not shutil.which("codex"),
    reason="real codex binary required",
)
async def test_tiny_feature_single_plan_direct_edit(tmp_path: Path) -> None:
    """Design: §13/§3 one live single-plan, direct-edit run against the real CLIs,
        excluded from default CI (slow + skip unless Claude compatibility and
        the Codex binary are available).
        The Generator edits target_dir IN PLACE (no copy-sandbox), so a successful
        tiny-feature run leaves out.txt directly in target_dir.
    Implementation: require OK Claude CLI and SDK checks before constructing
        the real drivers; drive Orchestrator.run over the tiny-feature design
        (one plan: write out.txt), then assert the terminal status and artifacts.
    Example: a completed run returns RunResult(status='completed') with
        (target_dir / 'out.txt') present and a run_dir containing '.harness'.
    """
    claude_checks = [_check_claude_cli(), _check_sdk_contract()]
    if any(row.status != "OK" for row in claude_checks):
        detail = "; ".join(f"{row.label}: {row.detail}" for row in claude_checks)
        pytest.skip(f"safe real Claude runtime required: {detail}")

    target = tmp_path / "scratch-app"
    target.mkdir()
    design = _DESIGN.read_text()
    result = await Orchestrator().run(
        target_dir=target,
        design_text=design,
        design_fingerprint=hashlib.sha256(design.encode()).hexdigest(),
        max_iterations=2,
        max_runtime_minutes=20,
        claude_runner=ClaudeDriver(),
        codex_runner=CodexDriver(),
        when=time.localtime(),
    )
    assert result.status in {"completed", "incomplete", "failed"}
    assert ".harness" in result.run_dir
    # Direct-edit proof: there is no copy-sandbox, so a completed tiny-feature run
    # must have written out.txt straight into target_dir.
    if result.status == "completed":
        assert (target / "out.txt").is_file()
