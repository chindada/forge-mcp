"""Reusable test doubles for forge-mcp tests.

This is the single home for fake ClaudeRunner/CodexRunner/driver doubles;
later tasks populate it as those seams land.
"""


class FakePlanner:
    """Fake planner that writes a minimal plan artifact.

    Design: phase tests need a deterministic planner without SDK calls.
    Implementation: create run_dir/plan/plan.md and return no warning.
    Example: await FakePlanner().write_plan(ctx).
    """

    async def write_plan(self, ctx):
        """Write a canned plan file.

        Design: §9.1 requires plan.md before iteration contracts are created.
        Implementation: mkdir plan directory and write markdown text.
        Example: await planner.write_plan(ctx) returns None.
        """
        (ctx.run_dir / "plan").mkdir(parents=True, exist_ok=True, mode=0o700)
        (ctx.run_dir / "plan" / "plan.md").write_text("# plan\n")
        return None


class FakeGenerator:
    """Fake generator that writes a summary artifact.

    Design: phase tests isolate orchestrator sequencing from Codex behavior.
    Implementation: write iteration-N/summary.md and perform no target edits.
    Example: await FakeGenerator().implement(ctx, codex_bin='codex', status_cb=cb).
    """

    async def implement(self, ctx, *, codex_bin, status_cb, env=None):
        """Write a canned summary for the current iteration.

        Design: §10.2 requires a generator summary artifact per iteration.
        Implementation: create the iteration directory and write markdown text.
        Example: await generator.implement(ctx, codex_bin='codex', status_cb=cb).
        """
        iter_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "summary.md").write_text("# summary\n")


class FakeEvaluator:
    """Fake evaluator returning a sequence of EvalResults.

    Design: phase tests need deterministic evaluation outcomes across multiple
        iterations without invoking Claude.
    Implementation: pop results from a supplied list and count remediation calls.
    Example: evaluator = FakeEvaluator([EvalResult(...)]).
    """

    def __init__(self, plan):
        """Store the canned EvalResult sequence.

        Design: each evaluate call consumes one planned result.
        Implementation: keep an index and a remediation counter.
        Example: FakeEvaluator([er]).remediation_calls == 0.
        """
        self._plan = plan
        self._i = 0
        self.remediation_calls = 0

    async def evaluate(self, ctx, *, retry=False):
        """Return the next canned EvalResult and write eval.json.

        Design: §9.2 expects evaluator artifacts to exist for result/lifecycle
            collection tests.
        Implementation: write model JSON to iteration-N/eval.json.
        Example: er = await evaluator.evaluate(ctx).
        """
        er = self._plan[self._i]
        self._i += 1
        iter_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "eval.json").write_text(er.model_dump_json())
        return er

    async def triage_design_flaws(self, ctx, *, eval_result, retry=False):
        """Return an empty TriageResult for all gaps.

        Design: phase tests focus on loop sequencing, not citation policy.
        Implementation: construct the production TriageResult model directly.
        Example: await evaluator.triage_design_flaws(ctx, eval_result=er).
        """
        from forge_mcp.models import TriageResult

        return TriageResult(triages=[], summary="ok")

    async def write_remediation(self, ctx, *, next_iteration_n, eval_result):
        """Write a canned next-iteration contract.

        Design: §9.2 remediation must create iteration-{N+1}/contract.md before
            the next generator pass.
        Implementation: increment remediation_calls and write markdown text.
        Example: await evaluator.write_remediation(ctx, next_iteration_n=2, eval_result=er).
        """
        self.remediation_calls += 1
        iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "contract.md").write_text("# next\n")
        return None


class FakeServerTaskContext:
    """Mutable ServerTaskContext double for cancellation/task-mode tests (§C1.6).

    Design: §C1.6 — poll_task_cancellation only reads is_cancelled; status
        fan-out only awaits update_status; orchestrator-side _extract_task_id
        only reads task_id/id. A bag-of-attributes double is sufficient and
        deliberately avoids importing the experimental mcp ServerTaskContext at
        test time (Risk 2: experimental API churn).
    Implementation: is_cancelled defaults False and is flipped by tests; task_id
        is a plain attribute; update_status records lines for inspection.
    Example: task = FakeServerTaskContext(task_id='tsk_abc'); task.is_cancelled = True.
    """

    def __init__(self, *, task_id: str | None = "tsk_fake") -> None:
        """Initialize the fake task with optional id and empty status log.

        Design: tests vary task_id and cancellation independently per case.
        Implementation: store task_id, set is_cancelled False, prepare a list.
        Example: FakeServerTaskContext(task_id=None).is_cancelled is False.
        """
        self.task_id = task_id
        self.is_cancelled = False
        self.status_lines: list[str] = []

    async def update_status(self, line: str) -> None:
        """Record one task-mode status line (§C1.5).

        Design: §C1.5 fan-out only requires an awaitable update_status sink.
        Implementation: append the incoming line for later assertions.
        Example: await task.update_status('phase started').
        """
        self.status_lines.append(line)


class FakeSessionPlanner(FakePlanner):
    """Planner double exposing a settable last_session_id (§C2.2).

    Design: §C2.5 plan/sessions.json composition tests read the planner's
        last_session_id after write_plan returns; this double lets each test
        pin the captured id without driving a real Claude session.
    Implementation: pin the value via the constructor; orchestrator reads it
        as an instance attribute (matches PlannerDriver.last_session_id shape).
    Example: FakeSessionPlanner(session_id='sess_plan').
    """

    def __init__(self, *, session_id: str | None = "sess_plan_fake") -> None:
        """Store the planner session id for orchestrator passthrough.

        Design: tests parameterize the captured id per scenario.
        Implementation: assign the attribute; no other state changes.
        Example: planner = FakeSessionPlanner(session_id=None).
        """
        self.last_session_id = session_id


class FakeSessionGenerator(FakeGenerator):
    """Generator double exposing a settable last_session_id (§C2.2).

    Design: §C2.5 iteration sessions.json tests need a deterministic Codex
        thread id surfaced through GeneratorDriver.last_session_id semantics.
    Implementation: assign the attribute at construction; tests use one
        instance per iteration (the attribute is a per-call value in production
        but a constant suffices for orchestrator-side composition tests).
    Example: FakeSessionGenerator(session_id='thr_codex').
    """

    def __init__(self, *, session_id: str | None = "thr_gen_fake") -> None:
        """Store the generator session id for orchestrator passthrough.

        Design: tests vary the id per scenario including a None fail-soft case.
        Implementation: assign and rely on inherited implement() behavior.
        Example: gen = FakeSessionGenerator(session_id=None).
        """
        self.last_session_id = session_id


class FakeSessionEvaluator(FakeEvaluator):
    """Evaluator double exposing a mutable last_session_id (§C2.2).

    Design: §C2.5 makes each evaluator phase (evaluate/triage/remediation)
        read last_session_id immediately after the call returns; this double
        rotates the exposed id from a configured list per call so the
        sessions.json matrix tests can pin per-phase ids and the iter_remediating
        regression test (Gap 8) can verify the LAST remediation attempt's id is
        the one recorded (not the first).
    Implementation: configure per-call ids per method via dicts of iter -> [ids];
        each method pops the next id (or stays None when exhausted); inherits
        plan-driven evaluate / triage / write_remediation bodies from FakeEvaluator.
    Example: FakeSessionEvaluator([er], evaluate_ids=['sess_eval_1']).
    """

    def __init__(
        self,
        plan: list,
        *,
        evaluate_ids: list[str | None] | None = None,
        triage_ids: list[str | None] | None = None,
        remediation_ids: list[str | None] | None = None,
    ) -> None:
        """Bind id rotations to each evaluator method.

        Design: §C2.5 reads last_session_id immediately after each call; the
            double mutates the exposed attribute as the call begins so the
            orchestrator sees the per-call id without coupling to plan order.
        Implementation: keep deque-like lists per method; default to all None
            so legacy tests behave like FakeEvaluator with last_session_id=None.
        Example: FakeSessionEvaluator([er], remediation_ids=['a', 'b']).
        """
        super().__init__(plan)
        self._evaluate_ids = list(evaluate_ids or [])
        self._triage_ids = list(triage_ids or [])
        self._remediation_ids = list(remediation_ids or [])
        self.last_session_id: str | None = None

    def _rotate(self, queue: list[str | None]) -> None:
        """Advance last_session_id to the next pinned id or None.

        Design: production runners overwrite last_session_id at the start of
            each SDK call; this helper mirrors that timing.
        Implementation: pop the head when present, else set None (fail-soft).
        Example: self._rotate(self._remediation_ids).
        """
        self.last_session_id = queue.pop(0) if queue else None

    async def evaluate(self, ctx, *, retry=False, changed_files=None):
        """Run evaluator.evaluate while rotating last_session_id.

        Design: parity with EvaluatorDriver.evaluate's per-call session reset.
        Implementation: rotate the id first, then delegate to FakeEvaluator.
        Example: er = await evaluator.evaluate(ctx).
        """
        self._rotate(self._evaluate_ids)
        return await super().evaluate(ctx, retry=retry)

    async def triage_design_flaws(self, ctx, *, eval_result, retry=False):
        """Run triage while rotating last_session_id.

        Design: parity with EvaluatorDriver.triage_design_flaws timing.
        Implementation: rotate the id first, then delegate to FakeEvaluator.
        Example: tr = await evaluator.triage_design_flaws(ctx, eval_result=er).
        """
        self._rotate(self._triage_ids)
        return await super().triage_design_flaws(ctx, eval_result=eval_result, retry=retry)

    async def write_remediation(self, ctx, *, next_iteration_n, eval_result, pivot=False):
        """Run remediation while rotating last_session_id per attempt.

        Design: §C2.2 requires the recorded id to be the LAST successful
            attempt's; the double exposes a fresh id per call so a re-author
            test (Gap 8) can verify the orchestrator captures the second id.
        Implementation: rotate first; then delegate to FakeEvaluator which
            accepts the pivot kwarg ONLY if the production seam does — the
            FakeEvaluator.write_remediation signature accepts (next_iteration_n,
            eval_result) but NOT pivot, so swallow pivot here for parity.
        Example: await evaluator.write_remediation(ctx, next_iteration_n=2, eval_result=er).
        """
        self._rotate(self._remediation_ids)
        self.remediation_calls += 1
        iter_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
        iter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (iter_dir / "contract.md").write_text(
            "# Remediation contract\n\n"
            "Implement the missing behavior and verify it with focused tests.\n"
            "## Acceptance criteria\n- [ ] Pass the sessions.json regression checks.\n"
        )
        return None
