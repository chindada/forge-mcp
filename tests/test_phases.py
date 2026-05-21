"""§9.2 iteration loop sequencing with fake drivers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fakes import FakeEvaluator, FakeGenerator, FakePlanner

from forge_mcp.config import RunConfig
from forge_mcp.models import EvalGap, EvalResult, GapTriage, RunForgeInput, TriageResult
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.orchestrator.phases import PhaseDeps, run_iteration_loop, run_phases, run_plan_phase
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import RunState


class Status:
    """Fake status sink for phase tests.

    Design: phases await a Status-like object but assertions focus on outcomes.
    Implementation: collect update kwargs in a list.
    Example: await Status().update(phase='planning', ...).
    """

    def __init__(self) -> None:
        """Initialize empty event storage.

        Design: each test sees isolated status events.
        Implementation: assign an empty list.
        Example: Status().events == [].
        """
        self.events: list[dict] = []

    async def update(self, **kwargs) -> None:
        """Record one phase update.

        Design: phase code only requires an awaitable update method.
        Implementation: append kwargs.
        Example: await status.update(phase='x').
        """
        self.events.append(kwargs)


class Drivers:
    """Bundle fake drivers for phase tests.

    Design: PhaseDeps expects planner/generator/evaluator attributes.
    Implementation: store constructor-provided evaluator with canned planner/generator.
    Example: Drivers(FakeEvaluator([er])).
    """

    def __init__(self, evaluator: FakeEvaluator) -> None:
        """Create the fake driver bundle.

        Design: evaluator varies per scenario while other fakes are fixed.
        Implementation: assign attributes matching production bundle shape.
        Example: Drivers(evaluator).planner.
        """
        self.planner = FakePlanner()
        self.generator = FakeGenerator()
        self.evaluator = evaluator


def _deps(
    tmp_path: Path, evaluator: FakeEvaluator, max_iterations: int = 2
) -> tuple[PhaseDeps, RunStateMachine, RunLedger]:
    """Build PhaseDeps, state machine, and ledger for a phase test.

    Design: tests exercise phase helpers without full preflight/server setup.
    Implementation: create run/input dirs, design.md, and initial state.json.
    Example: deps, sm, ledger = _deps(tmp_path, evaluator).
    """
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "inputs" / "design.md").write_text("design text with enough citation content")
    target = tmp_path / "target"
    target.mkdir()
    inputs = RunForgeInput(
        target_dir=str(target), design_doc_content="x", max_iterations=max_iterations
    )
    deps = PhaseDeps(
        Drivers(evaluator),
        Status(),
        logging.getLogger("test"),
        run_dir,
        target,
        inputs,
        RunConfig(),
    )
    sm = RunStateMachine(
        run_dir / "state.json",
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(target),
            iteration=0,
            started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        ),
    )
    return deps, sm, RunLedger()


def _gap(title: str) -> EvalGap:
    """Build a minimal EvalGap for phase loop tests.

    Design: tests need compact gap fixtures matching the public schema.
    Implementation: populate required fields with deterministic placeholder text.
    Example: gap = _gap('missing validation').
    """
    return EvalGap(
        title=title,
        severity="high",
        design_doc_section="§x",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )


async def test_no_gaps_completes_iteration_one(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator)
    assert await run_phases(deps, sm, ledger, None) == ("completed", 1)


async def test_gap_then_no_gaps_completes_iteration_two(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    gap = EvalGap(
        title="g",
        severity="high",
        design_doc_section="§x",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )
    evaluator = FakeEvaluator(
        [
            EvalResult(no_gaps=False, gaps=[gap], summary="bad"),
            EvalResult(no_gaps=True, gaps=[], summary="ok"),
        ]
    )
    deps, sm, ledger = _deps(tmp_path, evaluator)
    assert await run_phases(deps, sm, ledger, None) == ("completed", 2)
    assert evaluator.remediation_calls == 1


async def test_planner_ctx_has_no_target_dir(tmp_path: Path) -> None:
    """Pin §9.1 planner RunContext shape.

    Design: planner is outside the target-writing loop and receives no target_dir.
    Implementation: inspect the context passed to a mocked planner.write_plan.
    Example: ctx.target_dir is None and ctx.iteration_n is None.
    """
    planner = MagicMock()
    planner.write_plan = AsyncMock(return_value=None)
    drivers = MagicMock(planner=planner)
    status = MagicMock()
    status.update = AsyncMock()
    deps = PhaseDeps(
        drivers=drivers,
        status=status,
        logger=None,
        run_dir=tmp_path / "run",
        target_dir=tmp_path / "repo",
        inputs=MagicMock(),
        config=MagicMock(claude_config_dir=None, claude_cli_path=None),
    )
    started = __import__("datetime").datetime.now(__import__("datetime").UTC)
    sm = RunStateMachine(
        tmp_path / "state.json",
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(tmp_path / "repo"),
            iteration=0,
            started_at=started,
        ),
    )
    ledger = RunLedger()
    await run_plan_phase(deps, sm, ledger)
    (ctx,), _kwargs = planner.write_plan.call_args
    assert ctx.target_dir is None
    assert ctx.iteration_n is None
    assert ledger.completed_phases == ["plan"]


async def test_iteration_completed_phase_label_uses_iter_dash_n(tmp_path: Path) -> None:
    """Pin §7/§9.2 canonical completed phase labels.

    Design: iteration labels use iter-N, not iteration-N, in RunResult metadata.
    Implementation: run the smallest no-gap iteration loop and inspect ledger.
    Example: ledger.completed_phases[-1] == 'iter-1'.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=1)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    assert await run_iteration_loop(deps, sm, ledger, None) == ("completed", 1)
    assert ledger.completed_phases[-1] == "iter-1"


async def test_git_violation_artifact_filename_is_txt(tmp_path: Path) -> None:
    """Pin §13 git violation artifact filename.

    Design: forbidden git mutations are recorded as git-violation.txt.
    Implementation: pass a base git snapshot and monkeypatch capture_state via
        a fake target path that produces a diff against current state.
    Example: iteration_dir / 'git-violation.txt' exists after the loop.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=1)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    status, used = await run_iteration_loop(deps, sm, ledger, "old refs")
    assert (status, used) == ("incomplete", 1)
    iteration_dir = deps.run_dir / "iteration-1"
    assert (iteration_dir / "git-violation.txt").exists()
    assert not (iteration_dir / "git-violation.md").exists()


async def test_write_remediation_receives_filtered_eval_for_loop(tmp_path: Path) -> None:
    """Pin §9.2 remediation sees triage-filtered gaps only.

    Design: design-flaw gaps are removed before remediation contract generation.
    Implementation: evaluator triage marks one gap as a valid design flaw and
        write_remediation captures the filtered EvalResult.
    Example: captured eval_result.gaps contains only the code-bug title.
    """
    design_citation = "design text with enough citation content"
    design_gap = _gap("design gap")
    code_gap = _gap("code gap")

    class TriageEvaluator(FakeEvaluator):
        """Evaluator fake with one accepted design-flaw triage row.

        Design: phase tests isolate triage filtering from SDK behavior.
        Implementation: override triage and remediation to capture arguments.
        Example: evaluator.captured_eval_result is inspected after iteration one.
        """

        def __init__(self) -> None:
            """Initialize the fake with one gapful and one clean evaluation.

            Design: max_iterations=2 keeps the loop on the remediation path.
            Implementation: delegate canned results to FakeEvaluator.
            Example: TriageEvaluator().remediation_calls == 0 initially.
            """
            super().__init__(
                [
                    EvalResult(no_gaps=False, gaps=[design_gap, code_gap], summary="bad"),
                    EvalResult(no_gaps=True, gaps=[], summary="ok"),
                ]
            )
            self.captured_eval_result = None

        async def triage_design_flaws(self, ctx, *, eval_result, retry=False):
            """Return one strict design-flaw row.

            Design: §11.1 accepts only cited design flaw classifications.
            Implementation: cite the exact design text fixture used by _deps.
            Example: classify_gaps promotes only 'design gap'.
            """
            return TriageResult(
                triages=[
                    GapTriage(
                        gap_title="design gap",
                        design_fault=True,
                        fault_kind="ambiguity",
                        cited_sections=[design_citation],
                        explanation="ambiguous design",
                    )
                ],
                summary="triaged",
            )

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result):
            """Capture the EvalResult passed to remediation.

            Design: remediation must receive only unresolved code-bug gaps.
            Implementation: store eval_result then delegate artifact writing.
            Example: self.captured_eval_result.gaps has length one.
            """
            self.captured_eval_result = eval_result
            return await super().write_remediation(
                ctx, next_iteration_n=next_iteration_n, eval_result=eval_result
            )

    evaluator = TriageEvaluator()
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    assert await run_iteration_loop(deps, sm, ledger, None) == ("completed", 2)
    assert evaluator.captured_eval_result is not None
    assert [g.title for g in evaluator.captured_eval_result.gaps] == ["code gap"]


async def test_run_iteration_loop_does_not_set_unresolved_gaps(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §8.3 — the loop must not write ledger.unresolved_gaps directly;
        the engine collects them from the latest eval.json. This test pins
        that the loop leaves ledger.unresolved_gaps empty when the iteration
        cap is hit.
    Implementation: drive run_iteration_loop with max_iterations=1 and an
        evaluator that returns one gap; assert ledger.unresolved_gaps == [].
    Example: pytest runs this test in the non-slow suite.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=False, gaps=[_gap("g1")], summary="x")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=1)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    status, used = await run_iteration_loop(deps, sm, ledger, base_git=None)
    assert status == "incomplete"
    assert used == 1
    assert ledger.unresolved_gaps == []
