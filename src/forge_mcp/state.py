"""Run state model and atomic JSON persistence (§7, Invariant 1)."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StateLiteral = Literal[
    "init",
    "canonicalizing",
    "planning",
    "planned",
    "iter_generating",
    "iter_evaluating",
    "iter_triaging",
    "iter_done",
    "iter_remediating",
    "finalizing",
    "completed",
    "incomplete",
    "failed",
    "cancelling",
]


class RunState(BaseModel):
    """Durable state.json payload for a run.

    Design: §7 makes state.json the forensic single source for live phase,
        iteration, cancellation flags, AND the run's target_dir + start
        timestamp — auditors must be able to identify where and when the
        run was rooted from state.json alone.
    Implementation: Pydantic forbids extras; target_dir and started_at are
        required and preserved across every transition via model_dump in
        RunStateMachine.transition().
    Example: RunState(state='init', run_id='abcd1234', iteration=0,
        target_dir='/repo', started_at=now, last_updated_at=now).
    """

    model_config = ConfigDict(extra="forbid")

    state: StateLiteral
    run_id: str = Field(min_length=8, max_length=8, pattern=r"^[0-9a-f]{8}$")
    target_dir: str
    iteration: int = Field(ge=0)
    started_at: datetime
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str | None = None
    cancelled: bool = False


def write_state(path: Path, state: RunState) -> None:
    """Atomically write a RunState JSON file with private permissions.

    Design: only orchestrator state machinery writes this file (§8.2), and
        artifacts use 0600 to preserve sensitive run details (§13).
    Implementation: create a same-directory temp file, chmod it, write JSON,
        then replace the destination; failures unlink the temp path.
    Example: write_state(Path('state.json'), RunState(...)).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(state.model_dump_json(indent=2))
            handle.write("\n")
        if os.name == "posix":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def read_state(path: Path) -> RunState:
    """Read and validate a persisted RunState file.

    Design: all consumers re-enter through the model so corrupt state fails
        loudly instead of drifting through orchestration.
    Implementation: read text from disk and delegate JSON parsing to Pydantic.
    Example: state = read_state(Path('state.json')).
    """
    return RunState.model_validate_json(path.read_text())
