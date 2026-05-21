"""§8.5 cancellation ordering, timeout vs cancel terminals."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_mcp.lockfile import TargetLock
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.orchestrator.lifecycle import (
    close_drivers,
    collect_unresolved_gaps,
    emit_terminal_status,
    handle_cancellation,
    handle_failure,
    handle_timeout,
)
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import RunState


def _sm(p: Path) -> RunStateMachine:
    """Create a state machine rooted at p.

    Design: lifecycle tests need a durable state writer for transition order.
    Implementation: construct RunStateMachine with fixed run id.
    Example: sm = _sm(tmp_path).
    """
    return RunStateMachine(
        p / "state.json",
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(p),
            iteration=0,
            started_at=datetime.now(UTC),
            last_updated_at=datetime.now(UTC),
        ),
    )


def _write_eval(dir_: Path, title: str) -> None:
    """Write a minimal eval.json fixture with one gap.

    Design: lifecycle gap collection reads durable evaluator artifacts.
    Implementation: serialize the smallest EvalResult-compatible JSON body.
    Example: _write_eval(tmp_path / 'iteration-1', 'gap').
    """
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "eval.json").write_text(
        '{"no_gaps": false, "summary": "x", "gaps": [{'
        f'"title": "{title}", "severity": "high",'
        '"design_doc_section": "§1", "current_state": "a",'
        '"expected_state": "b", "suggested_fix": "c"}]}'
    )


def _deps(tmp_path: Path):
    """Build lifecycle deps with closeable fake driver runners.

    Design: timeout/failure handlers close all SDK runners before terminalizing.
    Implementation: MagicMock bundle mirrors the production deps shape.
    Example: deps = _deps(tmp_path); await handle_timeout(sm, ledger, deps).
    """
    planner = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    generator = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    evaluator = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    status = MagicMock()
    status.update = AsyncMock()
    return MagicMock(
        drivers=MagicMock(planner=planner, generator=generator, evaluator=evaluator),
        run_dir=tmp_path,
        status=status,
        logger=MagicMock(),
    )


class _OrderRunner:
    """Driver runner double recording the order of close lifecycle calls.

    Design: §H10/§8.5 require interrupt() to be awaited before aclose()/
        terminate(); asserting on a shared call log makes that ordering
        observable in a unit test.
    Implementation: each method appends its name to a shared list; aclose can
        be configured to raise so terminate escalation is exercised.
    Example: r = _OrderRunner(calls); await r.interrupt().
    """

    def __init__(self, calls: list[str], *, aclose_fails: bool = False) -> None:
        """Store the shared call log and aclose failure mode.

        Design: tests inspect a single ordered log across all runners.
        Implementation: keep the list reference and the failure flag.
        Example: _OrderRunner([], aclose_fails=True).
        """
        self.calls = calls
        self._aclose_fails = aclose_fails

    async def interrupt(self) -> None:
        """Record an interrupt invocation.

        Design: §H10 best-effort interrupt precedes the hard close.
        Implementation: append a marker to the shared log.
        Example: await runner.interrupt().
        """
        self.calls.append("interrupt")

    async def aclose(self) -> None:
        """Record an aclose invocation and optionally fail.

        Design: aclose is the graceful close before terminate escalation.
        Implementation: append a marker; raise when configured to force escalation.
        Example: await runner.aclose().
        """
        self.calls.append("aclose")
        if self._aclose_fails:
            raise RuntimeError("aclose boom")

    def terminate(self) -> None:
        """Record a terminate invocation.

        Design: terminate is the hard escalation after a failed/slow aclose.
        Implementation: append a marker to the shared log.
        Example: runner.terminate().
        """
        self.calls.append("terminate")


def _order_deps(*runners: object):
    """Bundle the given runners as the three phase drivers for close_drivers.

    Design: close_drivers iterates planner/generator/evaluator and reads each
        driver's _runner; tests supply purpose-built runners here.
    Implementation: wrap each runner in a MagicMock exposing it as _runner.
    Example: deps = _order_deps(r1, r2, r3).
    """
    drivers = MagicMock(
        planner=MagicMock(_runner=runners[0]),
        generator=MagicMock(_runner=runners[1]),
        evaluator=MagicMock(_runner=runners[2]),
    )
    return MagicMock(drivers=drivers)


async def test_cancellation_releases_lock_before_final_state(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = target_dir / "abcd1234"
    run_dir.mkdir(parents=True, exist_ok=True)
    sm = _sm(run_dir)
    sm.transition("iter_generating", iteration=1)
    ledger = RunLedger()
    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = TargetLock(target_dir / ".harness" / "run.lock")
    lock.acquire()
    observed: list[str] = []
    orig_release = lock.release
    orig_transition = sm.transition

    def release() -> None:
        """Record lock-release ordering before delegating.

        Design: §8.5 requires lock release before final failed state.
        Implementation: append a marker and call the original release method.
        Example: release() appends 'lock_released'.
        """
        observed.append("lock_released")
        orig_release()

    def transition(new_state, **fields):
        """Record state-transition ordering before delegating.

        Design: cancellation tests assert exact transition/release order.
        Implementation: append the new state marker and call the original.
        Example: transition('failed') appends 'state:failed'.
        """
        observed.append(f"state:{new_state}")
        return orig_transition(new_state, **fields)

    lock.release = release  # type: ignore[method-assign]
    sm.transition = transition  # type: ignore[method-assign]

    class Deps:
        """Dummy lifecycle deps with no real drivers.

        Design: close_drivers needs driver attributes during cancellation tests.
        Implementation: expose planner/generator/evaluator as None.
        Example: deps = Deps().
        """

        drivers = type("D", (), {"planner": None, "generator": None, "evaluator": None})()

    with pytest.raises(asyncio.CancelledError):
        await handle_cancellation(sm, ledger, Deps(), lock)
    assert observed == ["state:cancelling", "lock_released", "state:failed"]
    assert ledger.lock_released is True


async def test_handle_cancellation_re_raises(tmp_path: Path) -> None:
    """Pin §8.5 step 5 cancellation propagation.

    Design: client cancellation produces forensic state but no RunResult.
    Implementation: assert the helper raises after failed/cancelled is written.
    Example: pytest.raises(asyncio.CancelledError) around handle_cancellation.
    """
    sm = _sm(tmp_path)
    sm.transition("iter_generating", iteration=1)
    ledger = RunLedger()
    lock = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await handle_cancellation(sm, ledger, _deps(tmp_path), lock)
    lock.release.assert_called_once()
    assert ledger.lock_released is True
    assert sm.current.state == "failed"
    assert sm.current.cancelled is True


async def test_close_drivers_interrupts_before_terminate() -> None:
    """Pin §H10 close ordering: interrupt precedes aclose and terminate.

    Design: §8.5 escalation must call SDK-native interrupt() before the hard
        terminate(); a forced aclose failure ensures terminate is reached.
    Implementation: use runners that fail aclose and assert interrupt appears
        before both aclose and terminate in the shared call log.
    Example: await close_drivers(deps).
    """
    calls: list[str] = []
    runners = [_OrderRunner(calls, aclose_fails=True) for _ in range(3)]
    await close_drivers(_order_deps(*runners))
    assert calls[:3] == ["interrupt", "aclose", "terminate"]
    assert calls.index("interrupt") < calls.index("terminate")


async def test_close_drivers_without_interrupt_degrades_silently() -> None:
    """Pin §H19 note 1: a runner lacking interrupt() still closes cleanly.

    Design: interrupt() is best-effort; runners predating §H10 expose only
        aclose/terminate and must not raise during close.
    Implementation: build runners with no interrupt attribute and assert only
        aclose runs (no terminate, no exception) on a successful close.
    Example: await close_drivers(deps) returns without raising.
    """
    calls: list[str] = []

    def _make():
        """Build a runner double exposing only aclose/terminate.

        Design: models a pre-interrupt runner shape.
        Implementation: AsyncMock aclose appends 'aclose'; terminate appends.
        Example: r = _make().
        """
        runner = MagicMock(spec=["aclose", "terminate"])

        async def _aclose() -> None:
            """Record a successful aclose.

            Design: graceful close path for a no-interrupt runner.
            Implementation: append marker to the shared log.
            Example: await runner.aclose().
            """
            calls.append("aclose")

        runner.aclose = _aclose
        runner.terminate = lambda: calls.append("terminate")
        return runner

    await close_drivers(_order_deps(_make(), _make(), _make()))
    assert "aclose" in calls
    assert "interrupt" not in calls
    assert "terminate" not in calls


async def test_close_drivers_interrupt_timeout_still_escalates() -> None:
    """Pin §H10 interrupt failure still proceeds to aclose then terminate.

    Design: a hung/raising interrupt must be swallowed and must not block the
        aclose-then-terminate escalation.
    Implementation: a runner whose interrupt raises and whose aclose fails;
        assert the log is interrupt -> aclose -> terminate.
    Example: await close_drivers(deps).
    """
    calls: list[str] = []

    class _BadInterrupt(_OrderRunner):
        """Runner whose interrupt raises before the close escalation.

        Design: exercises the swallowed-interrupt branch of close_drivers.
        Implementation: record then raise from interrupt; inherit aclose/terminate.
        Example: r = _BadInterrupt(calls, aclose_fails=True).
        """

        async def interrupt(self) -> None:
            """Record then raise to simulate an interrupt failure.

            Design: close_drivers must swallow this and continue.
            Implementation: append marker, then raise RuntimeError.
            Example: await runner.interrupt() raises.
            """
            self.calls.append("interrupt")
            raise RuntimeError("interrupt boom")

    runner = _BadInterrupt(calls, aclose_fails=True)
    await close_drivers(_order_deps(runner, _OrderRunner([]), _OrderRunner([])))
    assert calls == ["interrupt", "aclose", "terminate"]


async def test_timeout_goes_finalizing_then_incomplete_not_cancelling(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    run_dir = target_dir / "abcd1234"
    run_dir.mkdir(parents=True, exist_ok=True)
    sm = _sm(run_dir)
    sm.transition("iter_generating", iteration=1)
    ledger = RunLedger()
    transitions: list[str] = []
    orig = sm.transition
    sm.transition = lambda s, **f: (transitions.append(s), orig(s, **f))[1]  # type: ignore[method-assign]

    deps = _deps(run_dir)
    await handle_timeout(sm, ledger, deps)
    assert "cancelling" not in transitions
    assert "finalizing" in transitions
    assert transitions[-1] == "incomplete"


def test_collect_unresolved_gaps_returns_latest_only(tmp_path: Path) -> None:
    """Pin §11.5 latest-only unresolved gap collection.

    Design: terminal results must not aggregate stale gaps from older iterations.
    Implementation: create numeric iteration dirs and assert only highest eval is read.
    Example: iteration-10 wins over iteration-2.
    """
    _write_eval(tmp_path / "iteration-1", "old")
    _write_eval(tmp_path / "iteration-2", "newer")
    _write_eval(tmp_path / "iteration-10", "latest")
    gaps = collect_unresolved_gaps(tmp_path)
    assert [g.title for g in gaps] == ["latest"]


def test_collect_unresolved_gaps_skips_iterations_without_eval(tmp_path: Path) -> None:
    """Pin §11.5 handling of in-progress iteration dirs.

    Design: an iteration directory without eval.json is ignored during terminalization.
    Implementation: make iteration-3 empty and assert iteration-2 remains latest.
    Example: collect_unresolved_gaps(tmp_path) returns gaps from iteration-2.
    """
    (tmp_path / "iteration-1").mkdir()
    _write_eval(tmp_path / "iteration-2", "from-2")
    (tmp_path / "iteration-3").mkdir()
    gaps = collect_unresolved_gaps(tmp_path)
    assert [g.title for g in gaps] == ["from-2"]


async def test_handle_timeout_calls_close_drivers(tmp_path: Path) -> None:
    """Pin §8.5 timeout closes drivers before incomplete transition.

    Design: runtime cap must not leave in-flight SDK sessions alive.
    Implementation: use closeable runner mocks and assert aclose awaited.
    Example: await handle_timeout(sm, ledger, deps).
    """
    sm = _sm(tmp_path)
    sm.transition("iter_generating", iteration=1)
    deps = _deps(tmp_path)
    await handle_timeout(sm, RunLedger(), deps)
    deps.drivers.planner._runner.aclose.assert_awaited()
    deps.drivers.generator._runner.aclose.assert_awaited()
    deps.drivers.evaluator._runner.aclose.assert_awaited()
    assert sm.current.state == "incomplete"


async def test_handle_failure_populates_unresolved_gaps_best_effort(tmp_path: Path) -> None:
    """Pin §8.5 failed results retain latest known gaps.

    Design: a crash after evaluation should still surface durable unresolved gaps.
    Implementation: write eval.json before invoking the failure lifecycle helper.
    Example: ledger.unresolved_gaps contains the leftover title.
    """
    _write_eval(tmp_path / "iteration-1", "leftover")
    sm = _sm(tmp_path)
    sm.transition("iter_evaluating", iteration=1)
    ledger = RunLedger()
    await handle_failure(sm, ledger, _deps(tmp_path), RuntimeError("boom"))
    assert [g.title for g in ledger.unresolved_gaps] == ["leftover"]
    assert ledger.error_class == "RuntimeError"


async def test_handle_failure_swallows_eval_json_errors(tmp_path: Path) -> None:
    """Pin §8.5 malformed eval.json never masks original failure.

    Design: failure metadata must preserve the original exception over artifact errors.
    Implementation: write invalid JSON and assert handler still records RuntimeError.
    Example: ledger.unresolved_gaps remains empty after parse failure.
    """
    iter_dir = tmp_path / "iteration-1"
    iter_dir.mkdir()
    (iter_dir / "eval.json").write_text("not json {{{")
    sm = _sm(tmp_path)
    sm.transition("iter_evaluating", iteration=1)
    ledger = RunLedger()
    await handle_failure(sm, ledger, _deps(tmp_path), RuntimeError("boom"))
    assert ledger.error_class == "RuntimeError"
    assert ledger.unresolved_gaps == []


async def test_emit_terminal_status_emits_one_phase_update() -> None:
    """Pin §8.1 terminal status event shape.

    Design: every terminal path emits one canonical phase-kind status update.
    Implementation: call the helper with a mocked status sink.
    Example: await emit_terminal_status(status, 'completed').
    """
    status = MagicMock()
    status.update = AsyncMock()
    await emit_terminal_status(status, "completed")
    status.update.assert_awaited_once()
    _, kwargs = status.update.await_args
    assert kwargs["phase"] == "completed"
    assert kwargs["kind"] == "phase"


def test_poll_task_cancellation_raises_cancelled_error() -> None:
    """poll_task_cancellation raises only when task.is_cancelled is true (§C1.6).

    Design: C-Inv 1 keeps cancellation forensics owned by handle_cancellation.
    Implementation: pass simple duck-typed task objects.
    Example: task.is_cancelled=True raises CancelledError.
    """
    import asyncio

    import pytest

    from forge_mcp.orchestrator.lifecycle import poll_task_cancellation

    class _Task:
        """Task double exposing is_cancelled.

        Design: lifecycle polling is intentionally duck-typed.
        Implementation: class attribute is enough for getattr.
        Example: _Task.is_cancelled is True.
        """

        is_cancelled = True

    poll_task_cancellation(None)
    with pytest.raises(asyncio.CancelledError):
        poll_task_cancellation(_Task())
