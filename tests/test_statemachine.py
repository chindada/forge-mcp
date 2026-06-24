from __future__ import annotations

import json

import pytest

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.statemachine import RunStateMachine


def test_legal_path_and_wave_cycle(tmp_path):
    """Design: §3.3 the run advances init->planning->wave-cycle->verifying->finalizing->terminal.
    Implementation: drive a legal sequence including a repeated wave cycle.
    Example: state.json reflects each transition.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in [
        "planning",
        "scheduling",
        "executing",
        "merging",
        "amending",
        "scheduling",
        "executing",
        "merging",
        "amending",
        "verifying",
        "finalizing",
        "completed",
    ]:
        sm.transition(to, now="t")
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "completed"


def test_illegal_transition_raises(tmp_path):
    """Design: §3.3 illegal edges are rejected (single source of legal ordering).
    Implementation: jump init->verifying.
    Example: raises.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    with pytest.raises(Exception):  # noqa: B017
        sm.transition("verifying", now="t")
