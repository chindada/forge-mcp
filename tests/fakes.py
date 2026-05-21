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
