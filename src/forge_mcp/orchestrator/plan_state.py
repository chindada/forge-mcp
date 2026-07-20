from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from forge_mcp.artifacts import RunLayout
from forge_mcp.state import write_json

PlanStateValue = Literal[
    "generating",
    "verifying",
    "evaluating",
    "triaging",
    "remediating",
    "done",
    "incomplete",
    "failed",
]


class PlanStatePayload(BaseModel, extra="forbid"):
    """Durable plan-level state checkpoint written to <run_dir>/plan_state.json.

    Design: §9/§10 the run executes exactly one plan, so plan state is a single
        run-level file with no plan_id and no sandbox (the Generator edits
        target_dir in place — there is no copy-sandbox path to record).
    Implementation: Pydantic BaseModel with extra='forbid' to reject unknown
        fields; all fields required except stop_reason. 'awaiting_amendment' is
        gone from PlanStateValue because amendments are applied in-loop (§4).
    Example: PlanStatePayload(state='generating', iteration=0,
        last_completed_iteration=0, last_updated_at='t').
    """

    state: PlanStateValue
    iteration: int
    last_completed_iteration: int
    last_updated_at: str
    stop_reason: str | None = None


class PlanState:
    """Sole writer of the single run-level plan_state.json.

    Design: §9/§10 with one plan per run, plan state collapses to one file at the
        run root (was plans/<id>/state.json); a single object owns it to keep the
        write path single-writer and crash-safe.
    Implementation: holds a PlanStatePayload in memory; every mutation durably
        writes via write_json to layout.plan_state_json so the file always
        matches the latest call.
    Example: PlanState(layout).set_state('done', now='t').
    """

    def __init__(self, layout: RunLayout) -> None:
        """Initialise PlanState at the run root with state='generating'.

        Design: §9/§10 the run dir already exists (init_run_layout ran), so no
            per-plan directory needs creating; the first write lands directly at
            the run root. The constructor loses the plan_id and sandbox_path
            params of the multi-plan design.
        Implementation: store layout; build the initial PlanStatePayload with
            state='generating', iteration=0, last_completed_iteration=0, and an
            empty last_updated_at placeholder. No os.makedirs (the run root is
            already present).
        Example: PlanState(lay).payload would be state='generating', iteration=0.
        """
        self._layout = layout
        self._payload = PlanStatePayload(
            state="generating",
            iteration=0,
            last_completed_iteration=0,
            last_updated_at="",
        )

    def _persist(self) -> None:
        """Durably write the current payload to <run_dir>/plan_state.json.

        Design: §9/§13 every mutation must be crash-safe, so we always use
            durable=True to call durable_replace under the hood.
        Implementation: delegate to write_json with durable=True against
            layout.plan_state_json (the single run-level path).
        Example: _persist() writes plan_state.json atomically at the run root.
        """
        write_json(self._layout.plan_state_json, self._payload, durable=True)

    def set_state(self, state: PlanStateValue, *, now: str) -> None:
        """Update the plan state and durably persist.

        Design: §9 state transitions are the primary mutation; persisting
            immediately ensures the file reflects the latest logical state even
            if the process crashes right after this call.
        Implementation: update payload.state and payload.last_updated_at in
            place via model_copy, then call _persist().
        Example: set_state('done', now='2024-01-01T00:00:00Z') writes
            state='done' to plan_state.json.
        """
        self._payload = self._payload.model_copy(update={"state": state, "last_updated_at": now})
        self._persist()

    def bump_iteration(self, now: str) -> None:
        """Increment iteration by 1 and durably persist.

        Design: §9 each iteration of the plan loop increments this counter so the
            orchestrator can track progress against the cap.
        Implementation: update payload.iteration to iteration + 1 and update
            last_updated_at via model_copy, then call _persist().
        Example: bump_iteration(now='t') on iteration=0 writes iteration=1.
        """
        self._payload = self._payload.model_copy(
            update={
                "iteration": self._payload.iteration + 1,
                "last_updated_at": now,
            }
        )
        self._persist()

    def record_completed(self, n: int, now: str) -> None:
        """Set last_completed_iteration to n and durably persist.

        Design: §9 records the most recently completed iteration for forensics;
            there is no cross-run resume, so this field is forensic-only.
        Implementation: update payload.last_completed_iteration to n and
            last_updated_at via model_copy, then call _persist().
        Example: record_completed(2, now='t') writes last_completed_iteration=2.
        """
        self._payload = self._payload.model_copy(
            update={"last_completed_iteration": n, "last_updated_at": now}
        )
        self._persist()
