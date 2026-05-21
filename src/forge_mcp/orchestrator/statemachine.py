"""§8.2 RunStateMachine. Sole writer of state.json (Invariant 1)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..state import RunState, StateLiteral, write_state

_TERMINAL: frozenset[str] = frozenset({"failed", "completed", "incomplete", "cancelling"})


class RunStateMachine:
    """Wrap RunState + atomic write; track last non-terminal phase.

    Design: Invariant 1 gives state.json one writer, and §8.5 needs the last
        non-terminal phase for accurate failure attribution.
    Implementation: every transition validates through RunState and writes via
        write_state; terminal states do not advance last_phase.
    Example: sm.transition('iter_generating', iteration=1).
    """

    def __init__(self, state_path: Path, initial: RunState) -> None:
        """Persist the initial RunState and record it in memory.

        Design: state.json exists from run initialization onward for observers.
        Implementation: write initial state immediately and initialize
            last_phase unless the initial state is terminal.
        Example: RunStateMachine(path, initial=RunState(state='init', ...)).
        """
        self._path = state_path
        self._current = initial
        self._last_phase = initial.state if initial.state not in _TERMINAL else ""
        write_state(state_path, initial)

    @property
    def current(self) -> RunState:
        """Return the current in-memory RunState.

        Design: phase code needs state without rereading state.json.
        Implementation: return the latest model written by transition.
        Example: sm.current.state == 'planning'.
        """
        return self._current

    @property
    def last_phase(self) -> str:
        """Return the last non-terminal phase.

        Design: terminal failures report the phase that actually failed rather
            than the final terminal state.
        Implementation: return the memo updated only on non-terminal states.
        Example: sm.last_phase == 'iter_evaluating'.
        """
        return self._last_phase

    @property
    def iteration(self) -> int:
        """Return the current iteration counter.

        Design: result building and lifecycle need the durable iteration value.
        Implementation: proxy the field from the current RunState.
        Example: sm.iteration returns 2.
        """
        return self._current.iteration

    def transition(self, new_state: StateLiteral, **fields: Any) -> None:
        """Validate, advance, and persist a state transition.

        Design: §8.2 last_phase advances only for non-terminal states so later
            failure handling records the correct failed_phase.
        Implementation: merge fields with current state, bump timestamp, write
            atomically, then update in-memory state.
        Example: sm.transition('iter_done', iteration=2).
        """
        payload = self._current.model_dump()
        payload.update(state=new_state, **fields)
        payload["last_updated_at"] = datetime.now(UTC)
        new = RunState.model_validate(payload)
        write_state(self._path, new)
        self._current = new
        if new_state not in _TERMINAL:
            self._last_phase = new_state
