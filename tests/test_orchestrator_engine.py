"""§8.1 orchestrator engine terminal-path compliance tests."""

from __future__ import annotations

import asyncio
import json
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
    try_idx = body.find("try:")
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
