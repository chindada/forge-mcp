from __future__ import annotations

import os
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
    "awaiting_amendment",
    "done",
    "incomplete",
    "failed",
]


class PlanStatePayload(BaseModel, extra="forbid"):
    """Durable per-plan state checkpoint written to plans/<id>/state.json.

    Design: §3.3/I1 each plan owns exactly one state.json; this model is the
        sole schema for that file and must never include merge_status (which
        belongs in the orchestrator-owned merge.json).
    Implementation: Pydantic BaseModel with extra='forbid' to reject unknown
        fields; all fields are required except stop_reason.
    Example: PlanStatePayload(plan_id='p1', sandbox_path='/sb',
        state='generating', iteration=0, last_completed_iteration=0,
        last_updated_at='t').
    """

    plan_id: str
    sandbox_path: str
    state: PlanStateValue
    iteration: int
    last_completed_iteration: int
    last_updated_at: str
    stop_reason: str | None = None


class PlanState:
    """Sole writer of plans/<id>/state.json for a single plan.

    Design: §3.3/I1 per-plan state is owned by one object to prevent
        concurrent writers and enforce the invariant that merge_status never
        appears in this file.
    Implementation: holds a PlanStatePayload in memory; every mutation
        immediately durably writes via write_json so the file is always
        consistent with the latest call.
    Example: PlanState(layout, plan_id='p1', sandbox_path='/sb').
    """

    def __init__(
        self,
        layout: RunLayout,
        *,
        plan_id: str,
        sandbox_path: str,
    ) -> None:
        """Initialise PlanState and create the plan directory.

        Design: §3.3 the plan dir must exist before the first write so that
            os.makedirs is called during construction rather than lazily.
        Implementation: create plan_dir with mode 0o700, exist_ok=True; build
            the initial PlanStatePayload with state='generating', iteration=0,
            last_completed_iteration=0, and a placeholder last_updated_at.
        Example: PlanState(lay, plan_id='p1', sandbox_path='/sb') creates
            lay.root/plans/p1/ and sets state='generating'.
        """
        self._layout = layout
        self._plan_id = plan_id
        os.makedirs(layout.plan_dir(plan_id), mode=0o700, exist_ok=True)
        self._payload = PlanStatePayload(
            plan_id=plan_id,
            sandbox_path=sandbox_path,
            state="generating",
            iteration=0,
            last_completed_iteration=0,
            last_updated_at="",
        )

    def _persist(self) -> None:
        """Durably write the current payload to the plan state file.

        Design: §3.3/§13 every mutation must be crash-safe, so we always use
            durable=True to call durable_replace under the hood.
        Implementation: delegate to write_json with durable=True, passing the
            layout-derived path for this plan.
        Example: _persist() writes plans/p1/state.json atomically.
        """
        write_json(self._layout.plan_state(self._plan_id), self._payload, durable=True)

    def set_state(self, state: PlanStateValue, *, now: str) -> None:
        """Update the plan state and durably persist.

        Design: §3.3 state transitions are the primary mutation; persisting
            immediately ensures the file reflects the latest logical state even
            if the process crashes after this call.
        Implementation: update payload.state and payload.last_updated_at in
            place, then call _persist().
        Example: set_state('done', now='2024-01-01T00:00:00Z') writes
            state='done' to state.json.
        """
        self._payload = self._payload.model_copy(update={"state": state, "last_updated_at": now})
        self._persist()

    def bump_iteration(self, now: str) -> None:
        """Increment iteration by 1 and durably persist.

        Design: §3.3 each iteration of the sandbox loop increments this counter
            so the orchestrator can detect runaway loops and track progress.
        Implementation: update payload.iteration to iteration + 1 and update
            last_updated_at, then call _persist().
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

        Design: §3.3 records the most recently completed iteration for forensics;
            there is no cross-run resume, so this field is forensic-only (records
            how far this plan got, for forensics; there is no cross-run resume).
        Implementation: update payload.last_completed_iteration to n and
            last_updated_at, then call _persist().
        Example: record_completed(2, now='t') writes last_completed_iteration=2.
        """
        self._payload = self._payload.model_copy(
            update={"last_completed_iteration": n, "last_updated_at": now}
        )
        self._persist()
