# tests/test_generator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.drivers.generator import run_generator
from tests.fakes import FakeCodexRunner


@pytest.mark.driver
async def test_generator_streams_events(tmp_path: Path):
    """Design: §5 the Generator runs one autonomous Codex turn editing target_dir.
    Implementation: a fake runner yields two events; run_generator collects them.
    Example: returns both events in order.
    """
    target_dir = tmp_path / "repo"
    target_dir.mkdir()
    runner = FakeCodexRunner(
        [
            CodexEvent(kind="item.started", payload={}),
            CodexEvent(kind="turn.completed", payload={"ok": True}),
        ]
    )
    events = await run_generator(
        runner, contract_text="do X", target_dir=target_dir, surface="backend"
    )
    assert [e.kind for e in events] == ["item.started", "turn.completed"]


@pytest.mark.driver
async def test_generator_roots_codex_at_target_dir(tmp_path: Path):
    """Design: §5 build_codex_config(cwd=target_dir) roots Codex at the repo so the
        direct edit is bounded to target_dir; the cwd reaching the runner must be it.
    Implementation: capture config.cwd via the FakeCodexRunner on_generate hook,
        which is called with str(config.cwd) before the first event is yielded.
    Example: on_generate sees str(target_dir).
    """
    target_dir = tmp_path / "repo"
    target_dir.mkdir()
    seen: list[str] = []
    runner = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=seen.append,
    )
    await run_generator(runner, contract_text="do X", target_dir=target_dir, surface="backend")
    assert seen == [str(target_dir)]
