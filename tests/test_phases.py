"""§9.2 iteration loop sequencing with fake drivers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fakes import FakeEvaluator, FakeGenerator, FakePlanner

from forge_mcp.config import RunConfig
from forge_mcp.models import EvalGap, EvalResult, GapTriage, RunForgeInput, TriageResult
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.orchestrator.phases import (
    PhaseDeps,
    _seed_start_contract,
    run_iteration_loop,
    run_phases,
    run_plan_phase,
)
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


async def test_break_on_final_iteration_sets_stop_reason(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    gap = EvalGap(
        title="Stuck",
        severity="high",
        design_doc_section="§H3",
        current_state="x",
        expected_state="y",
        suggested_fix="z",
    )
    plan = [EvalResult(no_gaps=False, gaps=[gap], summary="stuck") for _ in range(4)]
    deps, sm, ledger = _deps(tmp_path, FakeEvaluator(plan), max_iterations=4)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    status, n = await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert status == "incomplete"
    assert n == 4
    assert ledger.stop_reason is not None
    assert "unchanged" in ledger.stop_reason


async def test_break_mid_loop_returns_incomplete_before_cap(tmp_path: Path) -> None:
    """Pin §H3/§H13 mid-loop non-progress break ahead of the iteration cap.

    Design: a stuck gap-set must trip the §H13 break (return incomplete with a
        stop_reason) on a NON-final iteration, proving the break check precedes
        the max_iterations cap rather than only coinciding with it.
    Implementation: feed the same gap every iteration with a large
        max_iterations and assert the loop stops at iteration 4 (2*window) with
        a stop_reason mentioning the unchanged gap-set.
    Example: pytest runs this test in the non-slow suite.
    """
    repeated = _gap("Stuck gap")
    plan = [EvalResult(no_gaps=False, gaps=[repeated], summary="stuck") for _ in range(10)]
    deps, sm, ledger = _deps(tmp_path, FakeEvaluator(plan), max_iterations=10)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    status, n = await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert (status, n) == ("incomplete", 4)
    assert n < deps.inputs.max_iterations
    assert ledger.stop_reason is not None
    assert "unchanged" in ledger.stop_reason


async def test_generator_receives_network_access(tmp_path: Path) -> None:
    """Pin §H7 generator threading of inputs.network_access.

    Design: the network toggle must reach the generator only when its implement
        seam accepts it; phases inspects the signature before passing it.
    Implementation: a generator fake whose implement accepts network_access
        records the value; drive one no-gap iteration with network_access False
        and assert the recorded value.
    Example: await run_iteration_loop(...) with inputs.network_access False.
    """

    class _NetGenerator:
        """Generator fake recording the network_access it is called with.

        Design: isolates the threading behavior from Codex execution.
        Implementation: implement accepts network_access and writes a summary.
        Example: gen.seen_network_access is False after the call.
        """

        def __init__(self) -> None:
            """Initialize the recorded flag as unset.

            Design: a sentinel distinguishes 'not called' from a real value.
            Implementation: assign None until implement runs.
            Example: _NetGenerator().seen_network_access is None.
            """
            self.seen_network_access = None

        async def implement(self, ctx, *, codex_bin, status_cb, network_access=True, env=None):
            """Record network_access and write the iteration summary.

            Design: mirrors the production implement seam shape (§H7).
            Implementation: store the flag and create iteration-N/summary.md.
            Example: await gen.implement(ctx, codex_bin='codex', status_cb=cb).
            """
            self.seen_network_access = network_access
            iter_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "summary.md").write_text("# summary\n")

    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=1)
    gen = _NetGenerator()
    deps.drivers.generator = gen
    deps.inputs.network_access = False
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    status, n = await run_iteration_loop(deps, sm, ledger, base_git=None)
    assert (status, n) == ("completed", 1)
    assert gen.seen_network_access is False


async def test_no_verify_command_gate_is_inert(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    plan = [EvalResult(no_gaps=True, gaps=[], summary="done")]
    deps, sm, ledger = _deps(tmp_path, FakeEvaluator(plan), max_iterations=5)
    deps.inputs.verify_command = None
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    status, n = await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert status == "completed"
    assert n == 1


async def test_verify_failure_blocks_completed(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    plan = [EvalResult(no_gaps=True, gaps=[], summary="looks done") for _ in range(2)]
    deps, sm, ledger = _deps(tmp_path, FakeEvaluator(plan), max_iterations=2)
    deps.inputs.verify_command = "exit 1"
    deps.inputs.verify_timeout_seconds = 30
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    status, n = await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert status == "incomplete"
    assert n == 2
    assert (deps.run_dir / "iteration-1" / "verify.txt").exists()


async def test_pivot_signal_threads_pivot_true_to_remediation(tmp_path: Path) -> None:
    """Pin §H3 pivot directive carried into the next remediation contract.

    Design: when the gap-set is unchanged for one window, the loop nudges the
        evaluator with pivot=True so the next contract changes strategy.
    Implementation: a hardened evaluator fake (write_remediation accepts pivot)
        returns the same gap on iterations 1 and 2; capture the pivot flag for
        the remediation that seeds iteration 3.
    Example: evaluator.pivots == [True] after the loop.
    """
    repeated = _gap("Stuck")

    class _PivotEvaluator(FakeEvaluator):
        """Evaluator fake recording pivot flags and writing valid contracts.

        Design: write_remediation must accept pivot for the hardened seam and
            for §H6 validation to run.
        Implementation: append pivot to a list and write a well-formed contract.
        Example: _PivotEvaluator(plan).pivots.
        """

        def __init__(self, plan) -> None:
            """Initialize with the canned plan and an empty pivot log.

            Design: each remediation records its pivot flag.
            Implementation: delegate to FakeEvaluator and add a list.
            Example: _PivotEvaluator([...]).pivots == [].
            """
            super().__init__(plan)
            self.pivots: list[bool] = []

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Record pivot and write a well-formed remediation contract.

            Design: a valid contract avoids the §H6 re-author branch so the
                test isolates the pivot flag.
            Implementation: append pivot, write a heading + acceptance criteria.
            Example: await ev.write_remediation(ctx, next_iteration_n=3, ...).
            """
            self.remediation_calls += 1
            self.pivots.append(pivot)
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text(
                "# Remediation contract\n\n"
                "This implementation contract must change strategy and verify behavior.\n"
                "## Acceptance criteria\n- [ ] Fix the failing behavior with a test.\n"
            )
            return None

    plan = [EvalResult(no_gaps=False, gaps=[repeated], summary="stuck") for _ in range(3)]
    evaluator = _PivotEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=3)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert evaluator.pivots[0] is False
    assert True in evaluator.pivots


async def test_degenerate_contract_reauthors_once_then_synthesizes_gap(
    tmp_path: Path,
) -> None:
    """Pin §H6 degenerate contract: one re-author, then synthesized gap + warning.

    Design: a contract that fails validation triggers exactly one re-author; if
        still degenerate the loop records a warning and a high-severity §H6 gap.
    Implementation: an evaluator fake whose write_remediation always writes an
        empty (degenerate) contract; count calls and inspect the synthesized gap.
    Example: evaluator.remediation_calls == 2 after the iteration.
    """
    gap = _gap("real gap")

    class _DegenerateEvaluator(FakeEvaluator):
        """Evaluator fake that always writes a degenerate contract.

        Design: forces the §H6 re-author-then-synthesize branch.
        Implementation: write an empty contract.md and count calls via base.
        Example: _DegenerateEvaluator(plan).
        """

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Write an empty (degenerate) contract and count the call.

            Design: an empty contract fails validate_contract every time.
            Implementation: increment remediation_calls and write whitespace.
            Example: await ev.write_remediation(ctx, next_iteration_n=2, ...).
            """
            self.remediation_calls += 1
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text("   \n")
            return None

    plan = [
        EvalResult(no_gaps=False, gaps=[gap], summary="bad"),
        EvalResult(no_gaps=False, gaps=[_gap("other gap")], summary="bad2"),
    ]
    evaluator = _DegenerateEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert evaluator.remediation_calls >= 2
    assert any("Degenerate remediation contract" in warning for warning in ledger.warnings)


async def test_degenerate_contract_gap_reaches_next_iteration(tmp_path: Path) -> None:
    """Pin §H6.3: a degenerate-contract gap from iteration n reaches iteration n+1.

    Design: the high-severity "Degenerate remediation contract" gap synthesized
        in iteration n must be carried into iteration n+1's eval_for_loop so it is
        fingerprinted there and surfaces to the next evaluation/seed, not appended
        to a discarded loop-local EvalResult after fingerprint/remediation ran.
    Implementation: drive two iterations with an evaluator that always writes a
        degenerate contract; assert the iteration-2 fingerprint contains the
        carried gap token and that the ledger's carry buffer is drained empty.
    Example: "Degenerate remediation contract|high" in ledger.gap_fingerprints[-1].
    """
    gap = _gap("real gap")

    class _DegenerateEvaluator(FakeEvaluator):
        """Evaluator fake that always writes a degenerate contract.

        Design: forces the §H6 re-author-then-synthesize branch every iteration.
        Implementation: write a whitespace-only contract.md and count calls.
        Example: _DegenerateEvaluator(plan).
        """

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Write an empty (degenerate) contract and count the call.

            Design: an empty contract fails validate_contract every time.
            Implementation: increment remediation_calls and write whitespace.
            Example: await ev.write_remediation(ctx, next_iteration_n=2, ...).
            """
            self.remediation_calls += 1
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text("   \n")
            return None

    plan = [
        EvalResult(no_gaps=False, gaps=[gap], summary="bad"),
        EvalResult(no_gaps=False, gaps=[_gap("other gap")], summary="bad2"),
    ]
    evaluator = _DegenerateEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)

    # Iteration 1 synthesizes the degenerate gap for iteration 2; iteration 2
    # drains it into its own eval_for_loop, so the LAST fingerprint carries it.
    assert "Degenerate remediation contract|high" in ledger.gap_fingerprints[-1]
    # The carry buffer is drained once consumed.
    assert ledger.carried_gaps == []


async def test_degenerate_contract_gap_cites_section_h6(tmp_path: Path) -> None:
    """Pin §H6.3: the synthesized degenerate-contract gap cites design_doc_section "§H6".

    Design: §H6.3 prescribes the synthesized "Degenerate remediation contract" gap
        carry design_doc_section="§H6" verbatim; the literal must match the brief.
    Implementation: inspect ledger.carried_gaps at the start of iteration 2,
        before the loop drains the synthesized gap into eval_for_loop.
    Example: evaluator.parked_sections == ["§H6"].
    """
    gap = _gap("real gap")

    class _DegenerateEvaluator(FakeEvaluator):
        """Evaluator fake that always writes a degenerate contract.

        Design: forces the §H6 re-author-then-synthesize branch.
        Implementation: write whitespace contracts and inspect carried gaps.
        Example: _DegenerateEvaluator(plan).
        """

        def __init__(self, plan):
            """Initialize canned results plus carried-gap inspection state.

            Design: the test must observe the parked §H6 gap before it drains.
            Implementation: store a ledger reference later and captured sections.
            Example: evaluator.parked_sections == ["§H6"].
            """
            super().__init__(plan)
            self.ledger: RunLedger | None = None
            self.parked_sections: list[str] = []

        async def evaluate(self, ctx, *, retry=False):
            """Return the next result, capturing parked sections first.

            Design: §H6.3 drains carried gaps after evaluator output in the loop.
            Implementation: on iteration 2, read ledger.carried_gaps before super.
            Example: await evaluator.evaluate(ctx) captures ["§H6"].
            """
            if ctx.iteration_n == 2:
                assert self.ledger is not None
                self.parked_sections = [gap.design_doc_section for gap in self.ledger.carried_gaps]
            return await super().evaluate(ctx, retry=retry)

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Write an empty (degenerate) contract and count the call.

            Design: an empty contract fails validate_contract every time.
            Implementation: increment remediation_calls and write whitespace.
            Example: await ev.write_remediation(ctx, next_iteration_n=2, ...).
            """
            self.remediation_calls += 1
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text("   \n")
            return None

    plan = [
        EvalResult(no_gaps=False, gaps=[gap], summary="bad"),
        EvalResult(no_gaps=True, gaps=[], summary="ok"),
    ]
    evaluator = _DegenerateEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    evaluator.ledger = ledger
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert evaluator.parked_sections == ["§H6"]


async def test_valid_contract_triggers_no_reauthor(tmp_path: Path) -> None:
    """Pin §H6 a well-formed contract avoids the re-author retry.

    Design: validation must not re-invoke remediation when the contract is fine.
    Implementation: a hardened evaluator writing a valid contract over two
        differing-gap iterations; assert exactly one remediation call (iter 1).
    Example: evaluator.remediation_calls == 1 after the loop.
    """

    class _ValidEvaluator(FakeEvaluator):
        """Evaluator fake writing a well-formed contract with a pivot param.

        Design: enables §H6 validation (signature has pivot) but passes it.
        Implementation: write heading + acceptance criteria; count via base.
        Example: _ValidEvaluator(plan).
        """

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Write a valid contract and count the call.

            Design: a well-formed contract must not trigger a re-author.
            Implementation: increment remediation_calls and write a real body.
            Example: await ev.write_remediation(ctx, next_iteration_n=2, ...).
            """
            self.remediation_calls += 1
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text(
                "# Remediation contract\n\n"
                "Implement the missing behavior and verify it using a focused test.\n"
                "## Acceptance criteria\n- [ ] Add a failing test then fix it.\n"
            )
            return None

    plan = [
        EvalResult(no_gaps=False, gaps=[_gap("g1")], summary="bad"),
        EvalResult(no_gaps=False, gaps=[_gap("g2")], summary="bad2"),
    ]
    evaluator = _ValidEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)
    assert evaluator.remediation_calls == 1
    assert not any("Degenerate remediation contract" in warning for warning in ledger.warnings)


async def test_valid_contract_leaves_carried_gaps_empty(tmp_path: Path) -> None:
    """Pin §H6.3 negative: a well-formed contract never parks a carried gap.

    Design: when validate_contract passes there is no degenerate synthesis, so
        ledger.carried_gaps stays empty and no "Degenerate remediation contract"
        token leaks into any iteration's fingerprint.
    Implementation: drive two iterations with an evaluator that writes a real
        contract body; assert the carry buffer is empty and no degenerate token
        appears in gap_fingerprints.
    Example: ledger.carried_gaps == [] after run_iteration_loop.
    """
    gap = _gap("real gap")

    class _ValidEvaluator(FakeEvaluator):
        """Evaluator fake that writes a well-formed contract.

        Design: a real contract body passes validate_contract, skipping §H6.
        Implementation: write a heading plus actionable acceptance criteria.
        Example: _ValidEvaluator(plan).
        """

        async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
            """Write a well-formed contract and count the call.

            Design: a contract with a heading and acceptance text passes §H6.
            Implementation: increment remediation_calls and write real markdown.
            Example: await ev.write_remediation(ctx, next_iteration_n=2, ...).
            """
            self.remediation_calls += 1
            iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
            iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (iter_dir / "contract.md").write_text(
                "# Remediation contract\n\n"
                "## Acceptance criteria\n\n"
                "- Implement the missing handler and add a test that asserts it.\n"
            )
            return None

    plan = [
        EvalResult(no_gaps=False, gaps=[gap], summary="bad"),
        EvalResult(no_gaps=False, gaps=[_gap("other gap")], summary="bad2"),
    ]
    evaluator = _ValidEvaluator(plan)
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")

    await run_iteration_loop(deps, sm, ledger, base_git=None)

    assert ledger.carried_gaps == []
    assert not any("Degenerate remediation contract|high" in fp for fp in ledger.gap_fingerprints)


async def test_resume_seeds_contract_from_prior_remediation_not_plan(tmp_path: Path) -> None:
    """Pin §H13/§H2.5: resume re-authors the start contract from prior eval.

    Design: on resume (start_iteration>1) the seeded contract must come from
        write_remediation reconstructed from iteration-(start-1)/eval.json, not
        from the original plan.md, so accumulated remediation direction survives.
    Implementation: pre-write a distinctive plan.md and a prior eval.json,
        recreate an empty iteration-2, run the loop with start_iteration=2, and
        assert the seeded contract is the remediation output, not the plan text.
    Example: pytest runs this test in the non-slow suite.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# ORIGINAL PLAN\n")
    prior = deps.run_dir / "iteration-1"
    prior.mkdir(parents=True, exist_ok=True)
    (prior / "eval.json").write_text(
        EvalResult(
            no_gaps=False, gaps=[_gap("carry me forward")], summary="prior"
        ).model_dump_json()
    )
    (deps.run_dir / "iteration-2").mkdir(parents=True, exist_ok=True)
    status, n = await run_iteration_loop(deps, sm, ledger, None, start_iteration=2)
    assert (status, n) == ("completed", 2)
    seeded = (deps.run_dir / "iteration-2" / "contract.md").read_text()
    assert "ORIGINAL PLAN" not in seeded
    assert evaluator.remediation_calls >= 1


async def test_fresh_start_seeds_contract_from_plan(tmp_path: Path) -> None:
    """Pin §H13: a fresh run (start_iteration==1) still seeds from plan.md.

    Design: only resume (start>1) re-authors; the first iteration's contract is
        the plan, unchanged by the resume-seeding branch.
    Implementation: write plan.md, run the loop from start_iteration=1 with a
        no-gap evaluator, and assert the seeded contract equals the plan text.
    Example: pytest runs this test in the non-slow suite.
    """
    evaluator = FakeEvaluator([EvalResult(no_gaps=True, gaps=[], summary="ok")])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=1)
    (deps.run_dir / "plan").mkdir(exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# PLAN BODY\n")
    status, n = await run_iteration_loop(deps, sm, ledger, None, start_iteration=1)
    assert (status, n) == ("completed", 1)
    assert (deps.run_dir / "iteration-1" / "contract.md").read_text() == "# PLAN BODY\n"
    assert evaluator.remediation_calls == 0


async def test_resume_reuses_wellformed_archived_contract(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §H2.2 — resume reuses the durable contract.md the prior run wrote
        instead of re-authoring it; the evaluator's write_remediation must not
        be called when the archived contract is well-formed.
    Implementation: stage an iteration-2.interrupted-*/contract.md, run
        _seed_start_contract for start=2, assert the contract is the archived
        text and remediation_calls stayed zero.
    Example: pytest runs this test in the non-slow suite.
    """
    evaluator = FakeEvaluator([])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(parents=True, exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    archive = deps.run_dir / "iteration-2.interrupted-20260101T000000000000Z"
    archive.mkdir(parents=True)
    durable = (
        "# Remediation Contract\n\nStep 1: implement the accepted fix and verify the test passes.\n"
    )
    (archive / "contract.md").write_text(durable)

    await _seed_start_contract(deps, ledger, 2)

    seeded = (deps.run_dir / "iteration-2" / "contract.md").read_text()
    assert seeded == durable
    assert evaluator.remediation_calls == 0
    assert any("reused durable contract" in w for w in ledger.warnings)


async def test_resume_reauthors_when_archived_contract_degenerate(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: §H2.5 — re-authoring is the fallback; a missing or degenerate
        archived contract must fall through to write_remediation.
    Implementation: stage an iteration-2.interrupted-*/contract.md that fails
        §H6 validate_contract (empty), run _seed_start_contract for start=2 with
        a prior eval.json present, assert remediation was invoked.
    Example: pytest runs this test in the non-slow suite.
    """
    er = EvalResult(no_gaps=False, gaps=[_gap("a")], summary="s")
    evaluator = FakeEvaluator([])
    deps, sm, ledger = _deps(tmp_path, evaluator, max_iterations=2)
    (deps.run_dir / "plan").mkdir(parents=True, exist_ok=True)
    (deps.run_dir / "plan" / "plan.md").write_text("# plan\n")
    # Prior eval.json so reconstruction succeeds and re-author runs.
    prior = deps.run_dir / "iteration-1"
    prior.mkdir(parents=True, exist_ok=True)
    (prior / "eval.json").write_text(er.model_dump_json())
    # Degenerate archived contract (empty) -> validate_contract != [].
    archive = deps.run_dir / "iteration-2.interrupted-20260101T000000000000Z"
    archive.mkdir(parents=True)
    (archive / "contract.md").write_text("")

    await _seed_start_contract(deps, ledger, 2)

    assert evaluator.remediation_calls == 1
