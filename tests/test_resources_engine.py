"""§R9.5 — engine register/deregister around the outer try/finally."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from forge_mcp.resources import _ACTIVE_RUNS


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty active-run registry.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    _ACTIVE_RUNS.clear()
    yield
    _ACTIVE_RUNS.clear()


def test_engine_signature_accepts_harness_token():
    """§R3.2 Orchestrator.__init__ accepts harness_token.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.orchestrator.engine import Orchestrator

    sig = inspect.signature(Orchestrator.__init__)
    assert "harness_token" in sig.parameters
    assert sig.parameters["harness_token"].default is None


def test_build_result_signature_accepts_harness_token():
    """§R5.2 build_result accepts harness_token.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.orchestrator.result import build_result

    sig = inspect.signature(build_result)
    assert "harness_token" in sig.parameters
    assert sig.parameters["harness_token"].default is None


def test_build_result_populates_uris_when_token_present(tmp_path):
    """§R4.2 / §R4.3 result artifacts get URI companions.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.models import RunForgeInput
    from forge_mcp.orchestrator.ledger import RunLedger
    from forge_mcp.orchestrator.result import build_result
    from forge_mcp.orchestrator.statemachine import RunStateMachine
    from forge_mcp.state import RunState

    run_id = "12345678"
    run_dir = tmp_path / ".harness" / run_id
    (run_dir / "plan").mkdir(parents=True)
    (run_dir / "plan" / "plan.md").write_text("# plan")
    (run_dir / "plan" / "sessions.json").write_text("{}")
    (run_dir / "inputs").mkdir()
    (run_dir / "inputs" / "git-state.txt").write_text("clean")
    (run_dir / "state.json").write_text('{"state":"completed"}')
    (run_dir / "status.log").write_text("")
    iteration = run_dir / "iteration-1"
    iteration.mkdir()
    (iteration / "contract.md").write_text("# c")
    (iteration / "eval.md").write_text("# e")
    (iteration / "verify.txt").write_text("PASS")

    started = datetime.now(UTC)
    state = RunState(
        state="completed",
        run_id=run_id,
        iteration=1,
        target_dir=str(tmp_path),
        started_at=started,
        last_updated_at=started,
    )
    sm = RunStateMachine(run_dir / "state.json", state)
    ledger = RunLedger()
    ledger.completed_phases = ["init", "plan", "iter1"]
    ledger.decided_at = started
    inputs = RunForgeInput(target_dir=str(tmp_path), design_doc_content="x")

    result = build_result(
        run_id=run_id,
        run_dir=run_dir,
        status="completed",
        inputs=inputs,
        sm=sm,
        ledger=ledger,
        started_at=started,
        task_id=None,
        harness_token="aBcDeFgHiJkL",
    )

    assert result.artifacts.plan_uri == "forge://aBcDeFgHiJkL/12345678/plan/plan.md"
    assert result.artifacts.plan_sessions_uri == (
        "forge://aBcDeFgHiJkL/12345678/plan/sessions.json"
    )
    assert result.artifacts.state_json_uri == "forge://aBcDeFgHiJkL/12345678/state.json"
    assert result.artifacts.status_log_uri == "forge://aBcDeFgHiJkL/12345678/status.log"
    assert result.artifacts.git_state_uri == ("forge://aBcDeFgHiJkL/12345678/inputs/git-state.txt")
    it1 = next(i for i in result.artifacts.iterations if i.n == 1)
    assert it1.contract_uri == "forge://aBcDeFgHiJkL/12345678/iteration-1/contract.md"
    assert it1.eval_md_uri == "forge://aBcDeFgHiJkL/12345678/iteration-1/eval.md"
    assert it1.verify_uri == "forge://aBcDeFgHiJkL/12345678/iteration-1/verify.txt"
    assert it1.summary_uri is None


def test_build_result_leaves_uris_none_when_token_none(tmp_path):
    """§R4.2 token None leaves URI companions unset.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.models import RunForgeInput
    from forge_mcp.orchestrator.ledger import RunLedger
    from forge_mcp.orchestrator.result import build_result
    from forge_mcp.orchestrator.statemachine import RunStateMachine
    from forge_mcp.state import RunState

    run_id = "12345678"
    run_dir = tmp_path / ".harness" / run_id
    (run_dir / "plan").mkdir(parents=True)
    (run_dir / "plan" / "plan.md").write_text("# p")
    (run_dir / "state.json").write_text("{}")
    (run_dir / "status.log").write_text("")

    started = datetime.now(UTC)
    state = RunState(
        state="completed",
        run_id=run_id,
        iteration=0,
        target_dir=str(tmp_path),
        started_at=started,
        last_updated_at=started,
    )
    sm = RunStateMachine(run_dir / "state.json", state)
    ledger = RunLedger()
    ledger.decided_at = started
    inputs = RunForgeInput(target_dir=str(tmp_path), design_doc_content="x")

    result = build_result(
        run_id=run_id,
        run_dir=run_dir,
        status="completed",
        inputs=inputs,
        sm=sm,
        ledger=ledger,
        started_at=started,
        task_id=None,
        harness_token=None,
    )
    assert result.artifacts.plan_uri is None
    assert result.artifacts.state_json_uri is None
    assert result.artifacts.status_log_uri is None


def test_registry_deregister_simulates_engine_finally(tmp_path):
    """§R3.2 / §R9.5 register then finally-deregister leaves registry empty.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.resources import (
        _ResourceScope,
        deregister_active_run,
        list_active_runs,
        register_active_run,
    )

    scope = _ResourceScope("12345678", tmp_path / ".harness", "aBcDeFgHiJkL")
    register_active_run(scope)
    assert any(s.harness_token == "aBcDeFgHiJkL" for s in list_active_runs())
    try:
        raise RuntimeError("synthetic")
    except RuntimeError:
        deregister_active_run("aBcDeFgHiJkL", "12345678")
    assert list_active_runs() == []


async def test_engine_finally_releases_lock_when_register_raises(tmp_path, monkeypatch):
    """§R9.5 register_active_run exception still releases lock + restores umask.

    Design: §R9.5 pins the iteration-3 ordering invariant — register_active_run
        is the first statement inside the same outer try whose finally releases
        self._prepared.lock and restores os.umask. Injecting an exception into
        register_active_run must (1) NOT leak the lock, (2) leave _ACTIVE_RUNS
        empty, and (3) restore the prior umask. The exception itself propagates
        out of run() because it is raised outside the inner phase try/except.
    Implementation: build a real Orchestrator with MagicMock drivers and a
        MagicMock lock; monkeypatch forge_mcp.resources.register_active_run to
        raise RuntimeError so the engine's per-call `from ..resources import
        register_active_run` rebinds to the raising stub; snapshot the prior
        umask; await orchestrator.run() under pytest.raises(RuntimeError);
        assert lock.release was called, _ACTIVE_RUNS is empty, umask restored.
    Example: pytest tests/test_resources_engine.py::test_engine_finally_releases_lock_when_
        register_raises -v.
    """
    # §R9.5 — register-time exception injection pin (iteration-3 ordering).
    import os
    from unittest.mock import AsyncMock, MagicMock

    from forge_mcp.config import RunConfig
    from forge_mcp.models import RunForgeInput
    from forge_mcp.orchestrator.engine import Orchestrator
    from forge_mcp.preflight import PreparedRun

    # Build PreparedRun (mirrors tests/test_orchestrator_engine.py::_prepared).
    target = tmp_path / "target"
    target.mkdir()
    (target / "AGENTS.md").write_text("guidance")
    harness = target / ".harness"
    harness.mkdir()
    lock = MagicMock()
    prepared = PreparedRun(
        harness_dir=harness,
        lock=lock,
        run_id="abcd1234",
        config=RunConfig(),
    )

    inputs = RunForgeInput(
        target_dir=str(target),
        design_doc_content="design body with implementation requirements",
        max_iterations=1,
        max_runtime_minutes=1,
    )
    config = RunConfig()

    # Minimal ctx + drivers fakes (mirrors tests/test_orchestrator_engine.py).
    class _Ctx:
        """Minimal async-context stand-in for Status / phase plumbing.

        Design: engine.run() drives Status which calls ctx.report_progress and
            ctx.info; these stubs must accept the calls without raising.
        Implementation: async methods that no-op.
        Example: ctx = _Ctx(); await ctx.info('x').
        """

        async def report_progress(self, **kwargs) -> None:
            """Swallow progress events.

            Design: Status fans out progress; the test does not assert on it.
            Implementation: async no-op.
            Example: await ctx.report_progress(progress=1, total=None).
            """
            return None

        async def info(self, line: str) -> None:
            """Swallow info events.

            Design: Status mirrors events to ctx.info; the test does not assert.
            Implementation: async no-op.
            Example: await ctx.info('msg').
            """
            return None

    drivers = MagicMock(
        planner=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
        generator=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
        evaluator=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
    )

    # Inject the RuntimeError at the engine's register call site.
    def _raise(_scope):
        """Raise to simulate registry mutation failure.

        Design: §R9.5 fault injection — the call site is the first statement
            inside engine.run()'s outer try, so the exception exercises the
            finally-cleanup path.
        Implementation: raise RuntimeError unconditionally.
        Example: register_active_run(scope) -> RuntimeError.
        """
        raise RuntimeError("boom")

    monkeypatch.setattr("forge_mcp.resources.register_active_run", _raise)

    # Capture prior umask before the run touches os.umask.
    prior_umask = os.umask(0o022)
    os.umask(prior_umask)

    orchestrator = Orchestrator(
        prepared,
        inputs,
        config,
        _Ctx(),
        drivers,
        harness_token="aBcDeFgHiJkL",
    )

    with pytest.raises(RuntimeError, match="boom"):
        await orchestrator.run()

    # 1. Lock was released by the finally.
    assert lock.release.called, "outer finally did not release the lock"
    # 2. Registry stayed empty (register never succeeded, deregister is a no-op).
    assert _ACTIVE_RUNS == {}
    # 3. Umask was restored to its pre-run value.
    restored = os.umask(0o022)
    os.umask(restored)
    assert restored == prior_umask, f"umask not restored: {restored:o} != {prior_umask:o}"
