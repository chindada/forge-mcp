# tests/test_planner.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.planner import run_planner
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_planner_parses_planset(tmp_path: Path):
    """Design: §5.1 the Planner emits a structured PlanSet from spec.md.
    Implementation: a fake runner returns a PlanSet dict; run_planner parses it.
    Example: PlanSet with 2 plans and a dependency edge.
    """
    payload = {
        "plans": [
            {
                "id": "p1",
                "depends_on": [],
                "surface": "backend",
                "file_scope": ["src/**"],
                "verification_command": "pytest -q",
                "body": "plan 1",
            },
            {
                "id": "p2",
                "depends_on": ["p1"],
                "surface": "frontend",
                "file_scope": ["web/**"],
                "verification_command": None,
                "body": "plan 2",
            },
        ],
        "run_verification_command": "pytest -q",
    }
    runner = FakeClaudeRunner([structured(payload)])
    ps = await run_planner(
        runner, spec_text="# design", plan_schema={"type": "object"}, cwd=tmp_path
    )
    assert [p.id for p in ps.plans] == ["p1", "p2"]
    assert ps.plans[1].depends_on == ["p1"]
    assert ps.run_verification_command == "pytest -q"
