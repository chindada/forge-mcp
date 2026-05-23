"""§8.1 orchestrator engine terminal-path compliance tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_mcp.config import RunConfig
from forge_mcp.models import RunForgeInput
from forge_mcp.orchestrator import engine as engine_mod
from forge_mcp.orchestrator.engine import (
    Orchestrator,
    canonicalize_design,
    warn_if_missing_target_agents_md,
)
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.preflight import PreparedRun


class Ctx:
    """Tiny MCP context fake for Status.

    Design: engine tests need Status to report progress/info without a server.
    Implementation: collect calls in lists via async methods.
    Example: ctx = Ctx(); await ctx.info('line').
    """

    def __init__(self) -> None:
        """Initialize empty call storage.

        Design: each engine test should observe isolated status side effects.
        Implementation: assign list attributes for progress and info events.
        Example: Ctx().infos == [].
        """
        self.progress: list[dict] = []
        self.infos: list[str] = []

    async def report_progress(self, **kwargs) -> None:
        """Record one progress event.

        Design: Status fans out progress to the MCP context.
        Implementation: append kwargs for later assertions.
        Example: await ctx.report_progress(progress=1, total=None).
        """
        self.progress.append(kwargs)

    async def info(self, line: str) -> None:
        """Record one info line.

        Design: Status mirrors each event to ctx.info for live callers.
        Implementation: append the rendered line.
        Example: await ctx.info('[run abcd1234] done').
        """
        self.infos.append(line)


def _prepared(tmp_path: Path, lock: MagicMock | None = None) -> PreparedRun:
    """Build a PreparedRun rooted in a temporary target directory.

    Design: engine tests start after preflight and own a prepared lock handoff.
    Implementation: create target/.harness and return the production dataclass.
    Example: prepared = _prepared(tmp_path).
    """
    target = tmp_path / "target"
    target.mkdir()
    (target / "AGENTS.md").write_text("guidance")
    harness = target / ".harness"
    harness.mkdir()
    return PreparedRun(
        harness_dir=harness,
        lock=lock or MagicMock(),
        run_id="abcd1234",
        config=RunConfig(),
    )


def _inputs(prepared: PreparedRun) -> RunForgeInput:
    """Return minimal RunForgeInput for engine tests.

    Design: Orchestrator receives already-validated inputs from server/preflight.
    Implementation: point at PreparedRun's target and inline design content.
    Example: inputs = _inputs(prepared).
    """
    return RunForgeInput(
        target_dir=str(prepared.harness_dir.parent),
        design_doc_content="design body with implementation requirements",
        max_iterations=1,
        max_runtime_minutes=1,
    )


def _drivers() -> MagicMock:
    """Return closeable fake driver bundle.

    Design: lifecycle paths close planner/generator/evaluator runners.
    Implementation: expose _runner objects with async aclose and terminate mocks.
    Example: drivers = _drivers(); drivers.planner._runner.aclose.
    """
    return MagicMock(
        planner=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
        generator=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
        evaluator=MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock())),
    )


def test_canonicalize_design_writes_canonical_copy(tmp_path: Path) -> None:
    """Pin §8.1 canonical design artifact writing.

    Design: engine canonicalization owns inputs/design.md creation.
    Implementation: call helper with inline content and assert exact file text.
    Example: canonicalize_design(inputs, tmp_path, ledger).
    """
    inputs = MagicMock(spec=RunForgeInput)
    inputs.design_doc_path = None
    inputs.design_doc_content = "hello design"
    ledger = RunLedger()
    canonicalize_design(inputs, tmp_path, ledger)
    assert (tmp_path / "inputs" / "design.md").read_text() == "hello design"
    assert ledger.warnings == []


def test_canonicalize_design_rejects_empty(tmp_path: Path) -> None:
    """Pin §8.1 empty design rejection.

    Design: canonicalization refuses whitespace-only source text.
    Implementation: call the helper with blank inline content and expect error.
    Example: pytest.raises(ValueError) around canonicalize_design.
    """
    inputs = MagicMock(spec=RunForgeInput)
    inputs.design_doc_path = None
    inputs.design_doc_content = "   "
    with pytest.raises(ValueError):
        canonicalize_design(inputs, tmp_path, RunLedger())


def test_canonicalize_design_warns_on_large_doc(tmp_path: Path) -> None:
    """Pin §8.1 large design warning without truncation.

    Design: docs over one megabyte warn but remain fully preserved.
    Implementation: write a >1 MiB inline string and inspect ledger warning.
    Example: canonical design file length equals the source length.
    """
    text = "x" * (1024 * 1024 + 1)
    inputs = MagicMock(spec=RunForgeInput)
    inputs.design_doc_path = None
    inputs.design_doc_content = text
    ledger = RunLedger()
    canonicalize_design(inputs, tmp_path, ledger)
    assert (tmp_path / "inputs" / "design.md").read_text() == text
    assert any("1 MB" in warning or "MB" in warning.upper() for warning in ledger.warnings)


async def test_warns_when_agents_md_absent(tmp_path: Path) -> None:
    """Pin §8.1 missing AGENTS guidance warning.

    Design: absence of Codex-readable guidance is non-fatal but visible.
    Implementation: call helper in an empty target directory and inspect warning.
    Example: ledger.warnings contains AGENTS.md.
    """
    ledger = RunLedger()
    status = MagicMock(update=AsyncMock())
    await warn_if_missing_target_agents_md(tmp_path, ledger, status)
    status.update.assert_awaited()
    assert any("AGENTS.md" in warning for warning in ledger.warnings)


async def test_silent_when_agents_md_present(tmp_path: Path) -> None:
    """Pin §8.1 AGENTS.md suppresses guidance warning.

    Design: an existing AGENTS.md is sufficient project guidance for Codex.
    Implementation: create the file and assert no status update is emitted.
    Example: status.update.assert_not_awaited().
    """
    (tmp_path / "AGENTS.md").write_text("hi")
    ledger = RunLedger()
    status = MagicMock(update=AsyncMock())
    await warn_if_missing_target_agents_md(tmp_path, ledger, status)
    status.update.assert_not_awaited()
    assert ledger.warnings == []


async def test_silent_when_agents_override_present(tmp_path: Path) -> None:
    """Pin §8.1 AGENTS.override.md suppresses guidance warning.

    Design: AGENTS.override.md is accepted as the explicit guidance override.
    Implementation: create the override file and assert no warning update.
    Example: ledger.warnings remains empty.
    """
    (tmp_path / "AGENTS.override.md").write_text("hi")
    ledger = RunLedger()
    status = MagicMock(update=AsyncMock())
    await warn_if_missing_target_agents_md(tmp_path, ledger, status)
    status.update.assert_not_awaited()
    assert ledger.warnings == []


async def test_hint_when_only_claude_md_present(tmp_path: Path) -> None:
    """Pin §8.1 CLAUDE.md hint for missing Codex guidance.

    Design: CLAUDE.md does not substitute for AGENTS.md in the Generator.
    Implementation: create only CLAUDE.md and assert warning mentions it.
    Example: any('CLAUDE.md' in warning for warning in ledger.warnings).
    """
    (tmp_path / "CLAUDE.md").write_text("hi")
    ledger = RunLedger()
    status = MagicMock(update=AsyncMock())
    await warn_if_missing_target_agents_md(tmp_path, ledger, status)
    assert any("CLAUDE.md" in warning for warning in ledger.warnings)


async def test_engine_success_emits_terminal_status(tmp_path: Path, monkeypatch) -> None:
    """Pin §8.1 success path terminal status update.

    Design: completed runs emit a final phase-kind status event.
    Implementation: monkeypatch phase execution to return completed immediately.
    Example: final status.log JSON has phase='completed'.
    """
    prepared = _prepared(tmp_path)

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return a completed phase result without SDK calls.

        Design: this test targets engine terminalization, not phase drivers.
        Implementation: append a representative completed phase and return.
        Example: await fake_run_phases(...) == ('completed', 1).
        """
        ledger.completed_phases.append("iter-1")
        return ("completed", 1)

    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    status_log_text = await asyncio.to_thread(Path(result.artifacts.status_log_path).read_text)
    records = [json.loads(line) for line in status_log_text.splitlines()]
    assert records[-1]["kind"] == "phase"
    assert records[-1]["phase"] == "completed"


async def test_engine_resume_skips_planning_and_starts_at_anchor(tmp_path, monkeypatch) -> None:
    """Pin §H2 engine resume: skip run_phases, start loop at L+1.

    Design: a resumed run must not re-plan; it enters run_iteration_loop at
        last_completed_iteration+1 so completed iterations stay append-only.
    Implementation: build a PreparedRun carrying a ResumePoint, replace
        run_phases with a sentinel that records if called, and capture the
        start_iteration passed to a fake run_iteration_loop.
    Example: await Orchestrator(prepared_with_resume, ...).run().
    """
    from forge_mcp.orchestrator.resume import ResumePoint

    base = _prepared(tmp_path)
    run_dir = base.harness_dir / base.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    point = ResumePoint(
        run_id=base.run_id,
        run_dir=run_dir,
        last_completed_iteration=4,
        start_iteration=5,
    )
    prepared = PreparedRun(
        harness_dir=base.harness_dir,
        lock=base.lock,
        run_id=base.run_id,
        config=base.config,
        resume_point=point,
    )

    planning_called = {"run_phases": False}

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Fail the test if planning is reached on the resume path.

        Design: resume must bypass run_phases entirely.
        Implementation: record the call so the assertion can fail loudly.
        Example: await fake_run_phases(...) should never run here.
        """
        planning_called["run_phases"] = True
        return ("completed", 0)

    captured: dict[str, object] = {}

    async def fake_run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration=1):
        """Capture the start_iteration the engine resumes at.

        Design: pins resume entry at last_completed_iteration+1.
        Implementation: record start_iteration and return a completed result.
        Example: await fake_run_iteration_loop(..., start_iteration=5).
        """
        captured["start_iteration"] = start_iteration
        ledger.completed_phases.append("iter-5")
        return ("completed", 5)

    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "run_iteration_loop", fake_run_iteration_loop)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)

    await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()

    assert planning_called["run_phases"] is False
    assert captured["start_iteration"] == 5


async def test_engine_resume_regenerates_cross_design_digest(tmp_path, monkeypatch) -> None:
    """§X7: a §H2 resume regenerates inputs/cross_design_patterns.md from scratch.

    Design: the cross-design digest must reflect harness state at planner
        cold-start time, so a resumed run re-runs the aggregator and rewrites the
        file rather than reusing the original run's digest.
    Implementation: build a PreparedRun carrying a ResumePoint, stub the
        aggregator render to a sentinel string, drive the engine resume path with
        fake phase functions, and assert the file was written with the sentinel.
    Example: after a resume run, cross_design_patterns.md contains the new digest.
    """
    from forge_mcp.orchestrator.resume import ResumePoint

    base = _prepared(tmp_path)
    run_dir = base.harness_dir / base.run_id
    (run_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (run_dir / "inputs" / "cross_design_patterns.md").write_text("STALE DIGEST")
    point = ResumePoint(
        run_id=base.run_id,
        run_dir=run_dir,
        last_completed_iteration=4,
        start_iteration=5,
    )
    prepared = PreparedRun(
        harness_dir=base.harness_dir,
        lock=base.lock,
        run_id=base.run_id,
        config=base.config,
        resume_point=point,
    )

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Fail loudly if planning runs on the resume path.

        Design: resume must bypass run_phases.
        Implementation: raise so the test fails if planning is reached.
        Example: never invoked on a resume run.
        """
        raise AssertionError("run_phases must not run on resume")

    async def fake_run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration=1):
        """Return a completed terminal status for the resume loop.

        Design: stand in for the real iteration loop on resume.
        Implementation: record nothing and return a completed result tuple.
        Example: await fake_run_iteration_loop(..., start_iteration=5).
        """
        ledger.completed_phases.append("iter-5")
        return ("completed", 5)

    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "run_iteration_loop", fake_run_iteration_loop)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    monkeypatch.setattr(
        engine_mod, "render_cross_design_digest", lambda _harness_dir, _logger: "FRESH DIGEST"
    )

    await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()

    digest = (run_dir / "inputs" / "cross_design_patterns.md").read_text()
    assert digest == "FRESH DIGEST"


async def test_engine_cancellation_propagates_and_skips_result(tmp_path: Path, monkeypatch) -> None:
    """Pin §8.5 cancellation propagation through engine.run.

    Design: cancellation writes failed/cancelled state and raises CancelledError.
    Implementation: monkeypatch phases to raise cancellation and assert lock release.
    Example: await orchestrator.run() raises asyncio.CancelledError.
    """
    lock = MagicMock()
    prepared = _prepared(tmp_path, lock=lock)

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Raise cancellation from the phase task.

        Design: engine cancellation branch is tested without driver execution.
        Implementation: raise asyncio.CancelledError immediately.
        Example: await fake_run_phases(...) raises CancelledError.
        """
        raise asyncio.CancelledError()

    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    with pytest.raises(asyncio.CancelledError):
        await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    lock.release.assert_called_once()


async def test_engine_does_not_swallow_keyboard_interrupt(tmp_path: Path, monkeypatch) -> None:
    """Pin §8.1 BaseException propagation from engine.run.

    Design: KeyboardInterrupt/SystemExit are not terminal RunResult failures.
    Implementation: monkeypatch phases to raise KeyboardInterrupt and assert it escapes.
    Example: pytest.raises(KeyboardInterrupt) around orchestrator.run().
    """
    prepared = _prepared(tmp_path)

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Raise KeyboardInterrupt from the phase task.

        Design: engine catch-all must be Exception-only.
        Implementation: raise the BaseException subclass directly.
        Example: await fake_run_phases(...) raises KeyboardInterrupt.
        """
        raise KeyboardInterrupt()

    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    with pytest.raises(KeyboardInterrupt):
        await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()


async def test_iteration_cap_incomplete_runs_caps_and_overflow(tmp_path, monkeypatch):
    """Pin a forge-mcp behavior.

    Design: §8.1 — apply_caps_and_overflow must run on the iteration-cap
        incomplete inline path, not only on completed, so unresolved_gaps
        cannot exceed GAP_LIST_CAP in the returned RunResult.
    Implementation: drive the orchestrator with a fake iteration loop that
        returns ('incomplete', 1) after stuffing 51 unresolved gaps, then
        assert the returned RunResult has at most 50 unresolved_gaps and
        unresolved_gaps_overflow_path is populated.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.models import EvalGap, EvalResult
    from forge_mcp.orchestrator.caps import GAP_LIST_CAP

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Populate overflow-sized unresolved gaps and hit iteration cap.

        Design: this isolates engine inline terminal handling from phase logic.
        Implementation: assign GAP_LIST_CAP + 1 gaps and return incomplete.
        Example: await fake_run_phases(...) == ('incomplete', 1).
        """
        gaps = [
            EvalGap(
                title=f"g{i}",
                severity="low",
                design_doc_section="§x",
                current_state="cs",
                expected_state="es",
                suggested_fix="sf",
            )
            for i in range(GAP_LIST_CAP + 1)
        ]
        iter_dir = deps.run_dir / "iteration-1"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "eval.json").write_text(
            EvalResult(no_gaps=False, gaps=gaps, summary="cap hit").model_dump_json()
        )
        ledger.unresolved_gaps = gaps
        return ("incomplete", 1)

    prepared = _prepared(tmp_path)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    assert result.status == "incomplete"
    assert len(result.unresolved_gaps) == GAP_LIST_CAP
    assert result.artifacts.unresolved_gaps_overflow_path is not None


async def test_iteration_cap_unresolved_gaps_come_from_latest_eval_json(tmp_path, monkeypatch):
    """Pin a forge-mcp behavior.

    Design: §8.3 — unresolved_gaps is collected from the latest
        iteration-N/eval.json via collect_unresolved_gaps, never directly
        from the in-memory triage-filtered list. The engine's iteration-cap
        branch must read from disk so behavior matches handle_timeout.
    Implementation: write an iteration-2/eval.json containing a sentinel gap,
        drive run_phases to return ('incomplete', 2), assert the resulting
        RunResult.unresolved_gaps contains the sentinel from disk.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.models import EvalGap, EvalResult

    sentinel = EvalGap(
        title="DISK_SENTINEL",
        severity="medium",
        design_doc_section="§x",
        current_state="cs",
        expected_state="es",
        suggested_fix="sf",
    )

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Write disk eval data and return iteration-cap incomplete.

        Design: engine must prefer latest eval.json over in-memory ledger data.
        Implementation: create iteration-2/eval.json with a sentinel gap.
        Example: await fake_run_phases(...) == ('incomplete', 2).
        """
        iter_dir = deps.run_dir / "iteration-2"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "eval.json").write_text(
            EvalResult(no_gaps=False, gaps=[sentinel], summary="cap hit").model_dump_json()
        )
        ledger.unresolved_gaps = []
        return ("incomplete", 2)

    prepared = _prepared(tmp_path)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    assert result.status == "incomplete"
    assert any(g.title == "DISK_SENTINEL" for g in result.unresolved_gaps)


async def test_inline_incomplete_applies_caps_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """§8.1 — inline incomplete outcome applies result caps exactly once.

    Design: finding 1 — the inline path must call the shared tail (and thus
        caps) once, not run a separate post-try caps block.
    Implementation: count apply_caps_and_overflow invocations during run().
    Example: pytest asserts the counter equals 1 after Orchestrator.run().
    """
    import forge_mcp.orchestrator.lifecycle as lifecycle_mod

    calls = {"n": 0}
    real = lifecycle_mod.apply_caps_and_overflow

    def counting(*args, **kwargs):
        """Count calls before delegating to the real caps helper.

        Design: the test observes call cardinality without changing behavior.
        Implementation: increment a mutable counter and forward all arguments.
        Example: counting(ledger, tmp_path, logger) returns real result.
        """
        calls["n"] += 1
        return real(*args, **kwargs)

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return an inline incomplete status without SDK calls.

        Design: exercise the inline finalization branch, not timeout handling.
        Implementation: return incomplete directly from the phase runner.
        Example: await fake_run_phases(...) == ('incomplete', None).
        """
        return ("incomplete", None)

    prepared = _prepared(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "apply_caps_and_overflow", counting)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)

    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    assert calls["n"] == 1
    assert result.status == "incomplete"


async def test_resume_preserves_durable_started_at_and_lci(tmp_path: Path, monkeypatch) -> None:
    """§7 / §H2 / finding 6 — resume continues the durable record, not a zeroed one.

    Design: RunStateMachine.__init__ must not overwrite started_at,
        last_completed_iteration, and iteration with fresh zeros on resume.
    Implementation: pre-write a durable state.json with lci=3 and an old
        started_at, build a resuming orchestrator, and assert state.json still
        carries those durable values after a finalizing incomplete run.
    Example: pytest asserts last_completed_iteration == 3 post-seed.
    """
    from forge_mcp.orchestrator.resume import ResumePoint
    from forge_mcp.state import RunState, read_state, write_state

    base = _prepared(tmp_path)
    run_dir = base.harness_dir / base.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    point = ResumePoint(
        run_id=base.run_id,
        run_dir=run_dir,
        last_completed_iteration=3,
        start_iteration=4,
    )
    prepared = PreparedRun(
        harness_dir=base.harness_dir,
        lock=base.lock,
        run_id=base.run_id,
        config=base.config,
        resume_point=point,
    )
    old_started = datetime.now(UTC) - timedelta(hours=2)
    write_state(
        run_dir / "state.json",
        RunState(
            state="iter_evaluating",
            run_id=base.run_id,
            iteration=3,
            target_dir=str(base.harness_dir.parent),
            started_at=old_started,
            last_updated_at=old_started,
            last_completed_iteration=3,
        ),
    )

    async def fake_run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration=1):
        """Return immediately so resume seeding is the behavior under test.

        Design: no new iter_done transition should rewrite last_completed_iteration.
        Implementation: assert the resume anchor and return incomplete.
        Example: await fake_run_iteration_loop(..., start_iteration=4).
        """
        assert start_iteration == 4
        return ("incomplete", None)

    monkeypatch.setattr(engine_mod, "run_iteration_loop", fake_run_iteration_loop)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)

    await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    durable = read_state(run_dir / "state.json")
    assert durable.started_at == old_started
    assert durable.last_completed_iteration == 3


def test_engine_run_initializes_logger_and_deps_before_try() -> None:
    """Pin a forge-mcp behavior.

    Design: §8.1 control-flow invariant — logger creation, disk-space warn,
        and PhaseDeps construction must happen BEFORE the outer `try`, so
        their failures cannot be funneled into handle_failure and deps is
        bound for every except handler.
    Implementation: parse engine.py source, find the `run` method, and
        assert that `previous_umask = os.umask(0o077)` appears on the line
        immediately preceding `try:` and that the disk_space_warn_if_low
        and deps=PhaseDeps lines appear BEFORE that umask call.
    Example: pytest runs this test in the non-slow suite.
    """
    import textwrap

    src = Path("src/forge_mcp/orchestrator/engine.py").read_text()
    run_body = src.split("async def run(self)", 1)[1]
    body = textwrap.dedent(run_body.split("return result", 1)[0])
    disk_idx = body.find("disk_space_warn_if_low")
    deps_idx = body.find("deps = PhaseDeps")
    umask_idx = body.find("previous_umask = os.umask(0o077)")
    try_idx = body.find("try:", umask_idx)
    assert disk_idx != -1 and deps_idx != -1 and umask_idx != -1 and try_idx != -1
    assert disk_idx < umask_idx < try_idx
    assert deps_idx < umask_idx < try_idx


def test_engine_run_does_not_re_transition_failed_after_build_result() -> None:
    """Pin a forge-mcp behavior.

    Design: §8.5 — handle_failure is the sole writer of the terminal
        'failed' state; engine.run must not re-transition after
        build_result. A second transition would re-write state.json with
        a different reason and violate the forensic single-writer rule.
    Implementation: scan engine.py source for any `sm.transition("failed"`
        / `sm.transition('failed'` call appearing AFTER `build_result(`.
        Assert no such occurrence exists.
    Example: pytest runs this test in the non-slow suite.
    """
    src = Path("src/forge_mcp/orchestrator/engine.py").read_text()
    after_build = src.split("build_result(", 1)[1] if "build_result(" in src else ""
    assert 'sm.transition("failed"' not in after_build
    assert "sm.transition('failed'" not in after_build


async def test_engine_writes_fingerprint_after_canonicalize(tmp_path: Path, monkeypatch) -> None:
    """§L10 — fingerprint file lands next to design.md after canonicalize.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return completed without invoking drivers.

        Design: lineage wiring is pre-loop orchestration behavior, independent
            of planner/generator/evaluator phase internals.
        Implementation: return a completed terminal tuple immediately.
        Example: await fake_run_phases(...) == ("completed", 0).
        """
        return ("completed", 0)

    prepared = _prepared(tmp_path)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    text = (
        (prepared.harness_dir / prepared.run_id / "inputs" / "design.fingerprint")
        .read_text()
        .strip()
    )
    assert len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _write_prior_lineage_run(
    harness: Path, run_id: str, design_text: str, *, hours_old: int = 1
) -> None:
    """Write a synthetic prior terminal run for engine lineage tests.

    Design: §L13.3 engine tests need prior-run artifacts realistic enough for
        find_lineage_runs and summarize_prior_run to accept them.
    Implementation: write design.fingerprint, terminal state.json, and a small
        latest eval.json under the requested run id.
    Example: _write_prior_lineage_run(harness, "aaaa0001", "design").
    """
    from datetime import UTC, datetime, timedelta

    from forge_mcp.models import EvalGap, EvalResult
    from forge_mcp.orchestrator.lineage import fingerprint_design

    run_dir = harness / run_id
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "iteration-1").mkdir()
    (run_dir / "inputs" / "design.fingerprint").write_text(fingerprint_design(design_text) + "\n")
    now = datetime.now(UTC) - timedelta(hours=hours_old)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "state": "incomplete",
                "run_id": run_id,
                "target_dir": str(harness.parent),
                "iteration": 1,
                "started_at": (now - timedelta(minutes=5)).isoformat(),
                "last_updated_at": now.isoformat(),
                "cancelled": False,
                "last_completed_iteration": 1,
                "reason": "non-progress: synthetic prior",
            }
        )
    )
    gap = EvalGap(
        title=f"prior gap {run_id}",
        severity="high",
        design_doc_section="§1",
        current_state="missing",
        expected_state="present",
        suggested_fix="different strategy",
    )
    (run_dir / "iteration-1" / "eval.json").write_text(
        EvalResult(no_gaps=False, gaps=[gap], summary="prior failed").model_dump_json()
    )


async def test_engine_links_prior_run_and_writes_digest(tmp_path: Path, monkeypatch) -> None:
    """§L13.3 — eligible prior run feeds planner digest and RunResult linkage.

    Design: engine wiring must connect fingerprinting, sibling scan, summary,
        digest write, result linked_prior_runs, and ArtifactIndex path fields.
    Implementation: synthesize one matching prior run and run mocked phases to
        terminal completed, then assert disk and model outputs.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return completed without invoking drivers.

        Design: lineage integration is independent of phase implementation.
        Implementation: return a completed terminal tuple immediately.
        Example: await fake_run_phases(...) == ("completed", 0).
        """
        return ("completed", 0)

    prepared = _prepared(tmp_path)
    design_text = _inputs(prepared).design_doc_content or ""
    _write_prior_lineage_run(prepared.harness_dir, "aaaa0001", design_text)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    prior_path = prepared.harness_dir / prepared.run_id / "inputs" / "prior_attempts.md"
    assert result.linked_prior_runs == ["aaaa0001"]
    assert prior_path.exists()
    assert result.artifacts.prior_attempts_path == str(prior_path)


async def test_engine_ignore_prior_attempts_still_writes_fingerprint(
    tmp_path: Path, monkeypatch
) -> None:
    """§L13.3 — per-call opt-out skips digest but keeps fingerprint.

    Design: §L-Inv 2 fingerprint recording is independent of whether this run
        elects to consume prior attempts.
    Implementation: synthesize a matching prior run, set ignore_prior_attempts,
        then assert no digest/linkage but fingerprint exists.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return completed without invoking drivers.

        Design: opt-out behavior is pre-loop orchestration behavior.
        Implementation: return a completed terminal tuple immediately.
        Example: await fake_run_phases(...) == ("completed", 0).
        """
        return ("completed", 0)

    prepared = _prepared(tmp_path)
    inputs = _inputs(prepared).model_copy(update={"ignore_prior_attempts": True})
    _write_prior_lineage_run(prepared.harness_dir, "aaaa0001", inputs.design_doc_content or "")
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    result = await Orchestrator(prepared, inputs, RunConfig(), Ctx(), _drivers()).run()
    inputs_dir = prepared.harness_dir / prepared.run_id / "inputs"
    assert result.linked_prior_runs == []
    assert not (inputs_dir / "prior_attempts.md").exists()
    assert (inputs_dir / "design.fingerprint").exists()


async def test_engine_fingerprint_write_oserror_skips_lineage_and_warns(
    tmp_path: Path, monkeypatch
) -> None:
    """§L13.3 / §L16 risk 2 — fingerprint-write OSError forces cold start.

    Design: when write_design_fingerprint raises OSError (ENOSPC / EROFS),
        engine.run MUST catch it, set fp=None, surface a ledger warning, and
        skip the lineage discovery block even when an eligible prior run
        exists — proving the §L-Inv 1 cold-start fallback covers the §L10
        fingerprint write per §L16 risk 2.
    Implementation: synthesize one matching prior run via the existing
        helper, monkeypatch engine_mod.write_design_fingerprint to raise
        OSError, drive the orchestrator to completed via a fake run_phases,
        and assert no linkage and no prior_attempts.md plus a warning whose
        text contains "design.fingerprint write failed".
    Example: pytest runs this in the non-slow suite.
    """

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Return completed without invoking drivers.

        Design: this test exercises the pre-loop lineage block only.
        Implementation: return the completed terminal tuple immediately.
        Example: await fake_run_phases(...) == ("completed", 0).
        """
        return ("completed", 0)

    def fake_write_design_fingerprint(_inputs_dir, _fp):
        """Simulate a disk-full failure at the §L10 fingerprint write site.

        Design: §L16 risk 2 demands the fingerprint write be best-effort;
            OSError must short-circuit lineage without failing the run.
        Implementation: raise OSError unconditionally so the engine's
            except branch fires.
        Example: monkeypatch.setattr(engine_mod, "write_design_fingerprint",
            fake_write_design_fingerprint).
        """
        raise OSError(28, "No space left on device")

    prepared = _prepared(tmp_path)
    design_text = _inputs(prepared).design_doc_content or ""
    # §L13.3 — prior run is OTHERWISE eligible; only the failed fingerprint
    # write should suppress lineage. If the fix regresses, this run would
    # link to "aaaa0001" and write prior_attempts.md.
    _write_prior_lineage_run(prepared.harness_dir, "aaaa0001", design_text)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    monkeypatch.setattr(engine_mod, "write_design_fingerprint", fake_write_design_fingerprint)
    result = await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()
    inputs_dir = prepared.harness_dir / prepared.run_id / "inputs"
    # §L10 — fp must be None on the OSError branch, so lineage block is
    # skipped and prior_attempts.md is never written.
    assert result.linked_prior_runs == []
    assert not (inputs_dir / "prior_attempts.md").exists()
    # §L-Inv 1 — the ledger surfaces the failure as a warning so the caller
    # can audit the cold-start fallback.
    assert any("design.fingerprint write failed" in warning for warning in result.warnings), (
        f"expected fingerprint-write warning in result.warnings; got {result.warnings!r}"
    )


async def test_engine_stop_reason_persists_to_state_json_and_prior_summary(
    tmp_path: Path, monkeypatch
) -> None:
    """§L13.3 / §L16 risk 4 — §H3 break reason round-trips through state.json.

    Design: when the iteration loop sets ledger.stop_reason and returns
        ("incomplete", N), engine.run MUST call
        sm.transition("incomplete", reason=ledger.stop_reason) so the
        durable state.json carries the text; summarize_prior_run MUST then
        read it back as PriorRunSummary.reason. This is the load-bearing
        signal the next planner uses to avoid replaying a plateau (§L5.2,
        §L16 risk 4).
    Implementation: monkeypatch run_phases to set ledger.stop_reason and
        return an incomplete terminal, run the orchestrator, then (a)
        re-read state.json from disk and assert reason matches; (b) call
        summarize_prior_run on the run directory and assert the
        PriorRunSummary.reason equals the same text.
    Example: pytest runs this in the non-slow suite.
    """
    from forge_mcp.orchestrator.lineage import summarize_prior_run

    expected_reason = "non-progress: oscillation detected at iteration 3"

    async def fake_run_phases(deps, sm, ledger, base_git):
        """Simulate a §H3 non-progress break.

        Design: phases.py sets ledger.stop_reason before returning
            ("incomplete", N) when the §H3 break fires (see
            phases.py:613). This fake mirrors that contract.
        Implementation: assign expected_reason to ledger.stop_reason and
            return the incomplete terminal tuple.
        Example: await fake_run_phases(deps, sm, ledger, git) sets reason.
        """
        ledger.stop_reason = expected_reason
        return ("incomplete", 1)

    prepared = _prepared(tmp_path)
    monkeypatch.setattr(engine_mod, "run_phases", fake_run_phases)
    monkeypatch.setattr(engine_mod, "capture_state", lambda _target: None)
    monkeypatch.setattr(engine_mod, "capture_uncommitted", lambda _target: None)
    await Orchestrator(prepared, _inputs(prepared), RunConfig(), Ctx(), _drivers()).run()

    run_dir = prepared.harness_dir / prepared.run_id
    # §L10 — assert the durable state.json carries the reason text.
    state_payload = json.loads((run_dir / "state.json").read_text())
    assert state_payload.get("state") == "incomplete"
    assert state_payload.get("reason") == expected_reason, (
        f"state.json.reason must carry the §H3 stop_reason; got {state_payload.get('reason')!r}"
    )

    # §L5.2 — assert summarize_prior_run reads it back as
    # PriorRunSummary.reason (the load-bearing field for next-planner
    # plateau-avoidance signaling per §L16 risk 4).
    summary = summarize_prior_run(run_dir)
    assert summary is not None
    assert summary.reason == expected_reason
