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


@pytest.mark.driver
async def test_planner_scopes_clean_tree_conjunct(tmp_path: Path):
    """Design: the direct-edit loop never commits (spec 0003 §1), so a planner-authored
        verification_command that gates on a clean tree (`test -z "$(git status
        --porcelain)"`) is structurally unsatisfiable and would burn the whole iteration
        cap. run_planner drops the clean-tree conjunct (keeping the correctness conjuncts)
        and records the change in the run log — it never silently certifies it nor crashes.
    Implementation: a fake runner returns a plan whose verification_command ends in the
        porcelain gate; run_planner returns a Plan with that conjunct stripped and writes a
        forensic note to run.log.
    Example: '... build && test -z "$(git status --porcelain)"' -> '... build'.
    """
    payload = {
        "surface": "backend",
        "verification_command": (
            'make generate fmt lint test build && test -z "$(git status --porcelain)"'
        ),
        "body": "scaffold the platform",
    }
    runner = FakeClaudeRunner([structured(payload)])
    run_log = tmp_path / "run.log"
    plan = await run_planner(
        runner,
        spec_text="# design",
        plan_schema=Plan.model_json_schema(),
        cwd=tmp_path,
        run_log_path=run_log,
    )
    assert plan.verification_command == "make generate fmt lint test build"
    assert run_log.exists()
    log_text = run_log.read_text()
    assert "git status --porcelain" in log_text
    # the forensic note records the kept (scoped) command, not just the drop
    assert "make generate fmt lint test build" in log_text


@pytest.mark.driver
async def test_planner_scopes_without_run_log(tmp_path: Path):
    """Design: scoping the clean-tree gate is a correctness step, NOT a side effect of
        logging — when no run_log_path is supplied the plan must STILL be scoped (only the
        forensic note is skipped). Pins that the `model_copy` is not nested under the
        `tee is not None` log guard, which would silently reintroduce the unsatisfiable-gate
        bug whenever no run log is configured.
    Implementation: run_planner is called WITHOUT run_log_path on a plan carrying the
        porcelain gate; the returned Plan still has the conjunct stripped.
    Example: 'make build && test -z "$(git status --porcelain)"' -> 'make build', no log.
    """
    payload = {
        "surface": "backend",
        "verification_command": 'make build && test -z "$(git status --porcelain)"',
        "body": "x",
    }
    runner = FakeClaudeRunner([structured(payload)])
    plan = await run_planner(
        runner, spec_text="# design", plan_schema=Plan.model_json_schema(), cwd=tmp_path
    )
    assert plan.verification_command == "make build"
