# tests/test_scheduler.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.models import Plan, PlanSet
from forge_mcp.orchestrator.scheduler import add_conflict_edge, has_cycle, ready_plans, run_wave


def _ps(*edges):
    """Build a PlanSet from (id, deps) edge tuples for the DAG helper tests.

    Design: §7.1 the scheduler tests need small, explicit dependency graphs; a
        helper keeps each test to its edges without Plan-construction boilerplate.
    Implementation: for each (id, deps) tuple build a trivial backend Plan with
        the given depends_on, then wrap them in a PlanSet.
    Example: _ps(('p1', []), ('p2', ['p1'])) yields a two-plan chain.
    """
    plans = {}
    for pid, deps in edges:
        plans[pid] = Plan(
            id=pid,
            depends_on=list(deps),
            surface="backend",
            file_scope=["**"],
            verification_command=None,
            body="x",
        )
    return PlanSet(plans=list(plans.values()), run_verification_command=None)


def test_ready_plans_respects_transitive_deps():
    """Design: §7.1 a wave is ready plans whose all transitive deps are merged.
    Implementation: p2 depends on p1; ready before merge is [p1].
    Example: ready_plans == ['p1'].
    """
    ps = _ps(("p1", []), ("p2", ["p1"]))
    assert ready_plans(ps, merged=set(), pending={"p1", "p2"}) == ["p1"]
    assert ready_plans(ps, merged={"p1"}, pending={"p2"}) == ["p2"]


def test_conflict_edge_never_creates_cycle():
    """Design: §7.4 a conflict edge that would cycle is refused (keep existing order).
    Implementation: p1->p2 exists; adding p2->p1 is refused.
    Example: add_conflict_edge returns False and the graph stays acyclic.
    """
    ps = _ps(("p1", []), ("p2", ["p1"]))  # p2 depends on p1
    assert add_conflict_edge(ps, loser="p1", winner="p2") is False
    assert not has_cycle(ps)


@pytest.mark.driver
async def test_failure_isolation_one_plan_raises(monkeypatch, tmp_path: Path):
    """Design: §7.1/I8 a raising plan does not cancel siblings (gather return_exceptions).
    Implementation: patch run_plan_loop so p1 raises, p2 returns done.
    Example: p2's result is present and terminal.
    """
    from forge_mcp.orchestrator import scheduler

    async def fake_loop(*, plan, **kw):
        """Stand in for run_plan_loop: raise for p1, return a done result for p2.

        Design: §7.1/I8 the isolation test must observe that p1 raising does not
            cancel p2; faking the loop keeps the test to pure scheduling behavior.
        Implementation: raise RuntimeError when plan.id is p1; otherwise return a
            lightweight object whose terminal_state is 'done'.
        Example: await fake_loop(plan=<p2>) has terminal_state == 'done'.
        """
        if plan.id == "p1":
            raise RuntimeError("boom")

        class R:
            terminal_state = "done"
            iterations = 1
            last_gaps = []
            synthesized = []
            proposed_amendment = None
            stop_reason = None
            change_set = []

        return R()

    monkeypatch.setattr(scheduler, "run_plan_loop", fake_loop)
    target = tmp_path / "t"
    target.mkdir()
    results = await run_wave(
        ["p1", "p2"],
        runner_factory=lambda: (None, None),
        target_dir=target,
        layout=None,
        concurrency=2,
    )
    assert results["p2"].terminal_state == "done"
    assert results["p1"].terminal_state == "failed"
