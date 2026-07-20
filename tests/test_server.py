from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp import server


def test_tool_input_schema_top_level_names_are_anchors():
    """Design: §4.1/§17 spread params -> flat schema whose names are RunForgeInput fields.
    Implementation: read the FastMCP tool input schema; assert top-level keys.
    Example: target_dir, design_doc_path, max_runtime_minutes present at top level.
    """
    schema = server.run_forge_input_schema()  # helper returning the tool's inputSchema
    props = set(schema["properties"])
    assert {
        "target_dir",
        "design_doc_path",
        "design_doc_content",
        "max_iterations",
        "max_runtime_minutes",
    } <= props


@pytest.mark.driver
async def test_timeout_finalizes_incomplete(monkeypatch, tmp_path: Path):
    """Design: §4.1 the runtime cap (asyncio.wait_for) finalizes 'incomplete', never raises.
    Implementation: patch the orchestrator to sleep past a tiny cap.
    Example: RunResult.status == 'incomplete'.
    """
    import asyncio

    async def slow_run(**kw):
        """Fake orchestrator run that sleeps well past the tiny cap.

        Design: §4.1 we need a coroutine that outlasts the timeout so the cap fires.
        Implementation: sleep 5 seconds, which far exceeds the 60 ms cap.
        Example: monkeypatched onto Orchestrator.run; raises TimeoutError from wait_for.
        """
        await asyncio.sleep(5)

    monkeypatch.setattr(server.Orchestrator, "run", staticmethod(slow_run))

    class Ctx:  # advisory-only; correctness must not depend on it
        async def report_progress(self, *a, **k): ...
        async def info(self, *a, **k): ...

    res = await server._run_forge_impl(
        target_dir=str(tmp_path),
        design_doc_content="# d",
        max_iterations=1,
        max_runtime_minutes=0.001,
        ctx=Ctx(),
    )  # cap ~ 60ms
    assert res.status == "incomplete"
