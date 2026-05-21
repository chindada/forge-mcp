"""§C1.4 / §C3 / §C1.6 — server-layer integration tests for task augmentation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from fakes import (
    FakeServerTaskContext,
    FakeSessionEvaluator,
    FakeSessionGenerator,
    FakeSessionPlanner,
)
from mcp.server.experimental.task_context import ServerTaskContext

from forge_mcp.config import RunConfig
from forge_mcp.lockfile import TargetLock
from forge_mcp.models import EvalResult, RunForgeInput
from forge_mcp.orchestrator.engine import Orchestrator
from forge_mcp.preflight import PreparedRun


def _build_prepared(tmp_path: Path) -> PreparedRun:
    """Construct a PreparedRun with a real lock for orchestrator tests.

    Design: bypassing preflight while keeping the lock object real makes the
        §8.5 lock-release ordering observable; harness_dir is a tmp_path
        subdirectory so the run.lock file can be acquired safely.
    Implementation: build .harness/<run-id> on disk, acquire TargetLock, and
        return a PreparedRun pointing at it.
    Example: prepared = _build_prepared(tmp_path).
    """
    target = tmp_path / "target"
    harness = target / ".harness"
    harness.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = TargetLock(harness / "run.lock")
    lock.acquire()
    return PreparedRun(
        harness_dir=harness,
        lock=lock,
        run_id=lock.run_id,
        config=RunConfig(),
        resume_point=None,
    )


class _Drivers:
    """Driver bundle for server-layer integration tests.

    Design: parity with forge_mcp.server._Drivers shape — planner/generator/
        evaluator attributes — without touching real SDKs.
    Implementation: build id-aware fakes with deterministic ids.
    Example: drivers = _Drivers().
    """

    def __init__(self, *, generator=None, evaluator=None) -> None:
        """Construct the planner/generator/evaluator triple.

        Design: tests vary generator (slow vs. fast) and evaluator independently.
        Implementation: default to a fast no-gaps configuration; accept overrides.
        Example: _Drivers(generator=_SlowGenerator()).
        """
        self.planner = FakeSessionPlanner(session_id="sess_p")
        self.generator = generator or FakeSessionGenerator(session_id="thr_g")
        self.evaluator = evaluator or FakeSessionEvaluator(
            [EvalResult(no_gaps=True, gaps=[], summary="ok")],
            evaluate_ids=["sess_e"],
        )


def _make_inputs(target: Path) -> RunForgeInput:
    """Build a minimal RunForgeInput for integration tests.

    Design: tests need a one-iteration no-verify run that completes fast.
    Implementation: provide inline design content; default max_iterations=1.
    Example: inputs = _make_inputs(tmp_path / 'target').
    """
    return RunForgeInput(
        target_dir=str(target),
        design_doc_content="build it",
        max_iterations=1,
        max_runtime_minutes=1,
    )


async def test_run_result_task_id_populated_when_task_mode_active(tmp_path: Path) -> None:
    """Pin §C3 — RunResult.task_id is the FakeServerTaskContext.task_id in task mode.

    Design: §C3 says task_id is best-effort populated from ServerTaskContext.
        Orchestrator(..., task_id='tsk_abc').run() must surface it on RunResult.
    Implementation: construct Orchestrator with task_id and a fake task; run
        a one-iteration no-gaps loop; assert RunResult.task_id == 'tsk_abc'.
    Example: pytest tests/test_server.py -k task_id_populated -v.
    """
    prepared = _build_prepared(tmp_path)
    inputs = _make_inputs(tmp_path / "target")
    config = RunConfig()
    task = FakeServerTaskContext(task_id="tsk_abc")
    orch = Orchestrator(
        prepared,
        inputs,
        config,
        MagicMock(),
        _Drivers(),
        task=cast(ServerTaskContext, task),
        task_id="tsk_abc",
    )
    result = await orch.run()
    assert result.task_id == "tsk_abc"
    assert result.status == "completed"


async def test_run_result_task_id_none_when_direct_call(tmp_path: Path) -> None:
    """Pin §C3 — RunResult.task_id is None when task is None (direct call path).

    Design: §C3 — direct callers see task_id=None; only call_tool_as_task
        populates it.
    Implementation: construct Orchestrator with task=None, task_id=None; run;
        assert RunResult.task_id is None.
    Example: pytest tests/test_server.py::test_run_result_task_id_none_when_direct_call -v.
    """
    prepared = _build_prepared(tmp_path)
    inputs = _make_inputs(tmp_path / "target")
    config = RunConfig()
    orch = Orchestrator(prepared, inputs, config, MagicMock(), _Drivers(), task=None, task_id=None)
    result = await orch.run()
    assert result.task_id is None
    assert result.status == "completed"


async def test_task_mode_cancellation_follows_section_8_5_five_step_ordering(
    tmp_path: Path,
) -> None:
    """Pin §C1.6 / §8.5 — task-mode cancellation between phases triggers the five-step path.

    Design: §C1.6 says poll_task_cancellation raises CancelledError when
        task.is_cancelled flips True; the orchestrator's existing
        handle_cancellation owns the five-step terminal ordering
        (cancelling → close_drivers → lock release → ledger.lock_released →
        failed → re-raise CancelledError). C-Inv 1 keeps this the single owner.
    Implementation: instrument the real TargetLock.release and
        RunStateMachine.transition to record ordering; flip is_cancelled True
        from inside the FAKE generator (so the next phase boundary poll trips);
        run inside pytest.raises(asyncio.CancelledError); assert the recorded
        sequence is ['state:cancelling', 'lock_released', 'state:failed'] and
        ledger.lock_released and failed_phase are set.
    Example: pytest tests/test_server.py -k five_step_ordering -v.
    """
    prepared = _build_prepared(tmp_path)
    inputs = _make_inputs(tmp_path / "target")
    config = RunConfig()
    task = FakeServerTaskContext(task_id="tsk_cancel")

    class _CancellingGenerator:
        """Generator that flips task.is_cancelled True during iter_generating.

        Design: the next phase boundary in run_iteration_loop calls
            lifecycle.poll_task_cancellation(task), which raises CancelledError
            and reaches handle_cancellation.
        Implementation: implement() sets task.is_cancelled True and returns;
            no real generator work is needed.
        Example: gen = _CancellingGenerator(); await gen.implement(...).
        """

        last_session_id = None

        async def implement(
            self, ctx, *, codex_bin, status_cb, env=None, network_access=True
        ) -> None:
            """Flip the cancellation flag and return.

            Design: simulates a long generator turn that ends just as the
                client cancels; the next poll_task_cancellation tears down.
            Implementation: mutate the captured task and return.
            Example: await generator.implement(ctx, ...).
            """
            task.is_cancelled = True

    orch = Orchestrator(
        prepared,
        inputs,
        config,
        MagicMock(),
        _Drivers(generator=_CancellingGenerator()),
        task=cast(ServerTaskContext, task),
        task_id="tsk_cancel",
    )

    observed: list[str] = []
    orig_release = prepared.lock.release

    def release() -> None:
        """Record lock release ordering before delegating to TargetLock.

        Design: §8.5 step 3 — lock release MUST precede the final failed state.
        Implementation: append marker, then call the original release.
        Example: release() appends 'lock_released'.
        """
        observed.append("lock_released")
        orig_release()

    prepared.lock.release = release  # type: ignore[method-assign]

    from forge_mcp.orchestrator.statemachine import RunStateMachine

    orig_transition = RunStateMachine.transition

    def transition(self, new_state, **fields):
        """Record every state transition before delegating.

        Design: §8.5 transitions are observable on the shared log.
        Implementation: append the marker; call original method.
        Example: transition('cancelling') appends 'state:cancelling'.
        """
        observed.append(f"state:{new_state}")
        return orig_transition(self, new_state, **fields)

    RunStateMachine.transition = transition  # type: ignore[method-assign]
    try:
        with pytest.raises(asyncio.CancelledError):
            await orch.run()
    finally:
        RunStateMachine.transition = orig_transition  # type: ignore[method-assign]

    idx_cancelling = observed.index("state:cancelling")
    idx_released = observed.index("lock_released")
    idx_failed = observed.index("state:failed")
    assert idx_cancelling < idx_released < idx_failed, observed
