# tests/test_generator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.drivers.generator import run_generator
from tests.fakes import FakeCodexRunner


@pytest.mark.driver
async def test_generator_streams_events(tmp_path: Path):
    """Design: §5.2 the Generator runs one full-access turn in the sandbox.
    Implementation: a fake runner yields two events; run_generator collects them.
    Example: returns both events in order.
    """
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    runner = FakeCodexRunner(
        [
            CodexEvent(kind="item.started", payload={}),
            CodexEvent(kind="turn.completed", payload={"ok": True}),
        ]
    )
    events = await run_generator(runner, contract_text="do X", sandbox=sandbox, surface="backend")
    assert [e.kind for e in events] == ["item.started", "turn.completed"]
