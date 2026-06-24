from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest

from forge_mcp.drivers._claude import ClaudeDriver
from forge_mcp.drivers._codex import CodexDriver
from forge_mcp.orchestrator.engine import Orchestrator

pytestmark = pytest.mark.slow

_DESIGN = Path(__file__).resolve().parents[1] / "examples" / "tiny-feature.md"


@pytest.mark.skipif(
    not (shutil.which("claude") and shutil.which("codex")),
    reason="real claude/codex binaries required",
)
async def test_tiny_feature_end_to_end(tmp_path: Path) -> None:
    """Design: §17 one live end-to-end run against real CLIs, excluded from default CI.
    Implementation: drive Orchestrator.run with real drivers over a tiny design.
    Example: returns a terminal RunResult under a .harness run dir.
    """
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
