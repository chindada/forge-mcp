from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.statemachine import (
    RunState,
    RunStateMachine,
    RunStatePayload,
)


def test_legal_single_plan_path(tmp_path):
    """Design: §9 the run advances init->planning->executing->finalizing->terminal.
    Implementation: drive the full legal chain and read back the final state.
    Example: state.json reflects 'completed' at the end.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in ["planning", "executing", "finalizing", "completed"]:
        sm.transition(to, now="t")
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "completed"


def test_failed_reachable_from_any_nonterminal(tmp_path):
    """Design: §9 'failed' is reachable from any non-terminal state.
    Implementation: transition init->planning then planning->failed directly.
    Example: state becomes 'failed' without passing through finalizing.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    sm.transition("planning", now="t")
    sm.transition("failed", now="t")
    assert sm.payload.state == "failed"


def test_removed_states_are_illegal(tmp_path):
    """Design: §9 scheduling/merging/amending/verifying edges are removed.
    Implementation: planning->scheduling and executing->merging both raise.
    Example: no path threads the old wave-cycle states.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    sm.transition("planning", now="t")
    with pytest.raises(ValueError):
        sm.transition("scheduling", now="t")
    sm.transition("executing", now="t")
    with pytest.raises(ValueError):
        sm.transition("merging", now="t")


def test_illegal_skip_raises(tmp_path):
    """Design: §9 illegal edges are rejected (single source of legal ordering).
    Implementation: jump init->finalizing.
    Example: raises ValueError.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    with pytest.raises(ValueError):
        sm.transition("finalizing", now="t")


def test_terminal_cannot_advance(tmp_path):
    """Design: §9 terminal states do not advance.
    Implementation: reach 'incomplete' then attempt another transition.
    Example: transitioning out of a terminal state raises ValueError.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in ["planning", "executing", "finalizing", "incomplete"]:
        sm.transition(to, now="t")
    with pytest.raises(ValueError):
        sm.transition("completed", now="t")


def test_wave_field_removed():
    """Design: §9 the dead RunStatePayload.wave field is removed.
    Implementation: the model has no 'wave' field and extra='forbid' rejects it.
    Example: passing wave=0 raises ValidationError.
    """
    assert "wave" not in RunStatePayload.model_fields
    assert "scheduling" not in RunState.__args__
    with pytest.raises(ValidationError):
        # Route through model_validate (dict) so pyright doesn't flag the
        # deliberately-invalid 'wave' key as a Literal mismatch.
        RunStatePayload.model_validate(
            {"state": "init", "last_phase": None, "last_updated_at": "t", "wave": 0}
        )
