# tests/test_planner.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.planner import run_planner
from forge_mcp.models import Plan
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_planner_parses_single_plan(tmp_path: Path):
    """Design: §3/§6 the Planner emits exactly one Plan; run_planner validates the
        structured output into the collapsed Plan model {surface, verification_command,
        body} and returns it (no PlanSet, no plans list).
    Implementation: a fake runner returns one Plan dict; run_planner parses it and
        returns a Plan whose fields round-trip.
    Example: Plan(surface='backend', verification_command='pytest -q', body='plan 1').
    """
    payload = {
        "surface": "backend",
        "verification_command": "pytest -q",
        "body": "plan 1",
    }
    runner = FakeClaudeRunner([structured(payload)])
    plan = await run_planner(
        runner, spec_text="# design", plan_schema=Plan.model_json_schema(), cwd=tmp_path
    )
    assert isinstance(plan, Plan)
    assert plan.surface == "backend"
    assert plan.verification_command == "pytest -q"
    assert plan.body == "plan 1"


@pytest.mark.driver
async def test_planner_allows_no_verification_command(tmp_path: Path):
    """Design: §3 verification_command is optional (None ⇒ no completion gate);
        run_planner must accept a Plan that omits it and default to None.
    Implementation: a fake runner returns a Plan dict without verification_command;
        run_planner returns a Plan whose verification_command is None.
    Example: Plan(surface='frontend', body='x').verification_command is None.
    """
    payload = {"surface": "frontend", "body": "build the UI"}
    runner = FakeClaudeRunner([structured(payload)])
    plan = await run_planner(
        runner, spec_text="# design", plan_schema=Plan.model_json_schema(), cwd=tmp_path
    )
    assert plan.surface == "frontend"
    assert plan.verification_command is None
    assert plan.body == "build the UI"
