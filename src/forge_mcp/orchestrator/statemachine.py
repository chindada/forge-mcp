from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from forge_mcp.artifacts import RunLayout
from forge_mcp.state import write_json

# All valid state names for the single-plan, direct-edit run (§9).
RunState = Literal[
    "init",
    "planning",
    "executing",
    "finalizing",
    "completed",
    "incomplete",
    "failed",
]

_TERMINAL_STATES: frozenset[str] = frozenset({"completed", "incomplete", "failed"})

# Explicit legal adjacency map (non-failed edges). The wave-cycle states
# (scheduling/merging/amending/verifying) are gone: the run plans once, runs one
# plan loop, then finalizes.
_LEGAL_EDGES: dict[str, frozenset[str]] = {
    "init": frozenset({"planning"}),
    "planning": frozenset({"executing"}),
    "executing": frozenset({"finalizing"}),
    "finalizing": frozenset({"completed", "incomplete", "failed"}),
}


class RunStatePayload(BaseModel, extra="forbid"):
    """Durable checkpoint for a forge-mcp run.

    Design: §9 this payload is the sole content of run-level state.json; all
        fields are explicit so an unexpected key from a corrupt write is caught
        at parse time via extra='forbid'. The dead 'wave' field (write-only,
        never read — there are no waves) is removed.
    Implementation: Pydantic BaseModel with a Literal state field; last_phase
        records the FROM-state of the most recent non-terminal transition so
        that failure attribution is meaningful.
    Example: RunStatePayload(state='init', last_phase=None,
        last_updated_at='t', run_dir='/r', iterations=0).
    """

    state: RunState
    last_phase: str | None
    last_updated_at: str
    run_dir: str = ""
    iterations: int = 0


class RunStateMachine:
    """Single writer of run-level state.json (Invariant I1).

    Design: §9 the orchestrator advances the run through the collapsed state
        graph init->planning->executing->finalizing->terminal; illegal edges are
        rejected to prevent the run from entering an undefined state.
    Implementation: holds an in-memory RunStatePayload; each transition
        validates the requested edge against _LEGAL_EDGES, updates the payload,
        model_validates, and durably writes to RunLayout.state_json.
    Example: sm = RunStateMachine(layout); sm.transition('planning', now='t').
    """

    def __init__(self, layout: RunLayout) -> None:
        """Initialise the state machine in the 'init' state and write state.json.

        Design: §9 start state is always 'init' so recovery tools can detect an
            un-started run by inspecting state.json.
        Implementation: build a RunStatePayload at 'init', write it durably, and
            store layout for later transitions.
        Example: RunStateMachine(layout).payload.state == 'init'.
        """
        self._layout = layout
        self._payload = RunStatePayload(
            state="init",
            last_phase=None,
            last_updated_at="",
            run_dir=str(layout.root),
        )
        write_json(layout.state_json, self._payload, durable=True)

    @property
    def payload(self) -> RunStatePayload:
        """Return the current in-memory payload (read-only view).

        Design: §9 exposes the payload for inspection without granting write
            access; the only mutation path is transition().
        Implementation: return the private attribute directly.
        Example: sm.payload.state == 'init' after construction.
        """
        return self._payload

    def transition(self, to: str, *, now: str) -> None:
        """Advance the run to state *to* and durably write state.json.

        Design: §9 legal edges are validated against an explicit adjacency map;
            'failed' is reachable from any non-terminal; terminal states do not
            advance last_phase so failure attribution is preserved.
        Implementation: look up legal neighbours for the current state; raise
            ValueError if *to* is not among them (and is not 'failed'); update
            state, conditionally update last_phase, set last_updated_at,
            model_validate, then durably write.
        Example: sm.transition('planning', now='2024-01-01T00:00:00Z') moves the
            run from 'init' to 'planning'.
        """
        current = self._payload.state

        if current in _TERMINAL_STATES:
            raise ValueError(f"Cannot transition from terminal state '{current}' to '{to}'.")

        allowed = _LEGAL_EDGES.get(current, frozenset())
        # 'failed' is reachable from any non-terminal state.
        if to != "failed" and to not in allowed:
            raise ValueError(
                f"Illegal transition: '{current}' -> '{to}'. "
                f"Allowed: {sorted(allowed | {'failed'})}."
            )

        # Advance last_phase only when transitioning to a non-terminal state.
        new_last_phase = self._payload.last_phase
        if to not in _TERMINAL_STATES:
            new_last_phase = current

        self._payload = RunStatePayload.model_validate(
            {
                **self._payload.model_dump(),
                "state": to,
                "last_phase": new_last_phase,
                "last_updated_at": now,
            }
        )
        write_json(self._layout.state_json, self._payload, durable=True)
