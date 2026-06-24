from __future__ import annotations

import json

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.plan_state import PlanState


def test_plan_state_writes_own_file_no_merge_status(tmp_path):
    """Design: §3.3/I1 per-plan state is single-writer and excludes merge_status.
    Implementation: set states and assert the file; assert no merge_status field.
    Example: state.json under plans/p1/.
    """
    ps = PlanState(RunLayout.for_run(tmp_path), plan_id="p1", sandbox_path="/sb")
    ps.set_state("generating", now="t")
    ps.bump_iteration(now="t")
    ps.set_state("done", now="t")
    data = json.loads((tmp_path / "plans" / "p1" / "state.json").read_text())
    assert data["state"] == "done" and data["iteration"] == 1
    assert "merge_status" not in data
