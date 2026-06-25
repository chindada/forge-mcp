from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.plan_state import (
    PlanState,
    PlanStatePayload,
    PlanStateValue,
)


def test_plan_state_writes_run_level_file(tmp_path):
    """Design: §9 plan state is a single run-level plan_state.json (no plans/<id>/ nesting).
    Implementation: construct PlanState(layout) with no plan_id/sandbox_path, drive states.
    Example: <run_dir>/plan_state.json holds state='done', iteration=1.
    """
    ps = PlanState(RunLayout.for_run(tmp_path))
    ps.set_state("generating", now="t")
    ps.bump_iteration(now="t")
    ps.record_completed(1, now="t")
    ps.set_state("done", now="t")
    data = json.loads((tmp_path / "plan_state.json").read_text())
    assert data["state"] == "done"
    assert data["iteration"] == 1
    assert data["last_completed_iteration"] == 1


def test_payload_rejects_dropped_fields():
    """Design: §9 plan_id and sandbox_path are removed from the payload schema.
    Implementation: extra='forbid' rejects either key; the model has neither field.
    Example: passing plan_id raises ValidationError.
    """
    base = {
        "state": "generating",
        "iteration": 0,
        "last_completed_iteration": 0,
        "last_updated_at": "t",
    }
    PlanStatePayload.model_validate(base)  # accepted
    with pytest.raises(ValidationError):
        PlanStatePayload.model_validate({**base, "plan_id": "p1"})
    with pytest.raises(ValidationError):
        PlanStatePayload.model_validate({**base, "sandbox_path": "/sb"})


def test_awaiting_amendment_is_not_a_state():
    """Design: §9 'awaiting_amendment' is removed from PlanStateValue.
    Implementation: the Literal no longer admits it; the payload rejects it.
    Example: state='awaiting_amendment' raises ValidationError.
    """
    assert "awaiting_amendment" not in PlanStateValue.__args__
    with pytest.raises(ValidationError):
        PlanStatePayload.model_validate(
            {
                "state": "awaiting_amendment",
                "iteration": 0,
                "last_completed_iteration": 0,
                "last_updated_at": "t",
            }
        )


def test_no_plans_subdir_created(tmp_path):
    """Design: §9/§10 the flat layout writes plan_state.json at the run root, not plans/<id>/.
    Implementation: construct PlanState and assert no plans/ directory appears.
    Example: only <run_dir>/plan_state.json exists after a write.
    """
    ps = PlanState(RunLayout.for_run(tmp_path))
    ps.set_state("generating", now="t")
    assert (tmp_path / "plan_state.json").is_file()
    assert not (tmp_path / "plans").exists()
