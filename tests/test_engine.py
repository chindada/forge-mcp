# tests/test_engine.py
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.orchestrator.engine import Orchestrator
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured

WHEN = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))


def _one_plan(verification_command: str | None = None, *, body: str = "create out.txt") -> dict:
    """Build a single-Plan structured-output dict for the new collapsed model.

    Design: §3/§15 the Planner now emits exactly one Plan whose only fields are
        surface, verification_command, and body; the tests script that shape
        directly (no PlanSet wrapper, no id/depends_on/file_scope).
    Implementation: return a backend-surface plan dict with the supplied
        verification_command (default None) and body.
    Example: _one_plan()['surface'] == 'backend'.
    """
    return {
        "surface": "backend",
        "verification_command": verification_command,
        "body": body,
    }


@pytest.mark.driver
async def test_single_plan_flow_completes(tmp_path: Path):
    """Design: §8 a one-plan run plans once, runs the loop directly on target_dir,
        and finalizes 'completed' with the file present.
    Implementation: planner returns one Plan that creates a file; the fake
        generator writes it into target_dir (config.cwd); evaluator returns no_gaps.
    Example: RunResult.status == 'completed' and out.txt lands in target_dir.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [structured(_one_plan()), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=2,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )
    assert result.status == "completed"
    assert result.stop_reason is None
    # The Generator edited target_dir IN PLACE — no sandbox, no merge.
    assert (target / "out.txt").read_text() == "done"
    # No verification_command declared -> verified is an honest False.
    assert result.verified is False


@pytest.mark.driver
async def test_persists_plan_artifacts(tmp_path: Path):
    """Design: §10 the single plan is durably recorded as plan.json + plan.md at
        the run root (flattened — no planset.json, no plans/<id>/ nesting).
    Implementation: run one clean plan; read plan.json (structured Plan) and
        plan.md (the body) from the run dir.
    Example: plan.json parses to the new collapsed Plan; plan.md holds the body.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(body="# the contract")),
            structured({"no_gaps": True, "summary": "ok", "gaps": []}),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=2,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )
    run_dir = Path(result.run_dir)
    plan_json = json.loads((run_dir / "plan.json").read_text())
    assert plan_json == {
        "surface": "backend",
        "verification_command": None,
        "body": "# the contract",
    }
    assert (run_dir / "plan.md").read_text() == "# the contract"
    # The flattened layout drops the old PlanSet artifact entirely.
    assert not (run_dir / "planset.json").exists()


@pytest.mark.driver
async def test_verified_true_when_done_and_command_declared(tmp_path: Path):
    """Design: §8 verified == (terminal_state == 'done') AND a verification_command
        was declared; a done plan with a passing per-iteration command is verified.
    Implementation: the plan declares a passing verification_command; the loop's
        in-target verify passes and the evaluator returns no_gaps -> done.
    Example: status 'completed', verified True.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(verification_command="exit 0")),
            structured({"no_gaps": True, "summary": "ok", "gaps": []}),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=2,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )
    assert result.status == "completed"
    assert result.verified is True


@pytest.mark.driver
async def test_verified_false_when_no_command(tmp_path: Path):
    """Design: §8 absent a declared verification_command, verified is an honest
        False even when the plan completes.
    Implementation: a one-plan run with verification_command=None that completes.
    Example: status 'completed', verified False.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [structured(_one_plan()), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=2,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )
    assert result.status == "completed"
    assert result.verified is False


@pytest.mark.driver
async def test_incomplete_on_iteration_cap_sets_stop_reason(tmp_path: Path):
    """Design: §4/§8 a plan that never closes its gaps hits the iteration cap and
        finalizes 'incomplete' with a non-None stop_reason and unresolved gaps.
    Implementation: max_iterations=1; the eval finds a code-bug gap and triage
        keeps it a code bug (not a design fault), so the loop returns 'incomplete'
        with stop_reason 'iteration cap'; the engine surfaces it honestly.
    Example: status 'incomplete', stop_reason is not None, unresolved_gaps non-empty.
    """
    target = tmp_path / "repo"
    target.mkdir()
    gap_eval = {
        "no_gaps": False,
        "summary": "found a gap",
        "gaps": [
            {
                "title": "missing thing",
                "severity": "high",
                "design_doc_section": "§X",
                "current_state": "absent",
                "expected_state": "present",
                "suggested_fix": "add it",
            }
        ],
    }
    code_bug_triage = {
        "triages": [
            {
                "gap_title": "missing thing",
                "design_fault": False,
                "fault_kind": None,
                "cited_sections": [],
                "explanation": "just a code bug",
                "proposed_amendment": None,
            }
        ]
    }
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(body="implement thing")),
            structured(gap_eval),
            structured(code_bug_triage),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("partial"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=1,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )
    assert result.status == "incomplete"
    assert result.stop_reason is not None
    assert result.unresolved_gaps
    assert result.verified is False


@pytest.mark.driver
async def test_run_is_fresh_new_dir_each_call(tmp_path: Path):
    """Design: §6.6/I12 every call is a fresh timestamped run (no resume).
    Implementation: two runs produce two distinct run dirs.
    Example: run_dir paths differ.
    """
    target = tmp_path / "repo"
    target.mkdir()

    def mk():
        """Build a fresh (claude, codex) fake pair scripted for one clean plan.

        Design: §6.6/I12 each run must get its own runners (the fakes consume
            scripted results FIFO), so the two runs cannot share one pair.
        Implementation: return a new FakeClaudeRunner (planner + no-gaps eval) and
            a new FakeCodexRunner (one turn.completed event) on each call.
        Example: c, x = mk() yields two unconsumed fakes for one run.
        """
        return (
            FakeClaudeRunner(
                [
                    structured(_one_plan(body="noop")),
                    structured({"no_gaps": True, "summary": "ok", "gaps": []}),
                ]
            ),
            FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})]),
        )

    orch = Orchestrator()
    c1, x1 = mk()
    r1 = await orch.run(
        target_dir=target,
        design_text="d",
        design_fingerprint="fp",
        max_iterations=1,
        max_runtime_minutes=600,
        claude_runner=c1,
        codex_runner=x1,
        when=WHEN,
    )
    c2, x2 = mk()
    r2 = await orch.run(
        target_dir=target,
        design_text="d",
        design_fingerprint="fp",
        max_iterations=1,
        max_runtime_minutes=600,
        claude_runner=c2,
        codex_runner=x2,
        when=WHEN,
    )
    assert r1.run_dir != r2.run_dir


@pytest.mark.driver
async def test_planner_raises_finalizes_failed(tmp_path):
    """Design: §8 an orchestrator-internal error finalizes 'failed' with a
        failure_kind, and the terminal cleanup still releases the per-target lock.
    Implementation: the planner's first claude.run raises; assert failed status,
        a set failure_kind, and that the lock is released (re-acquirable).
    Example: status 'failed', failure_kind set, a fresh TargetLock acquires cleanly.
    """
    from forge_mcp.lockfile import TargetLock

    target = tmp_path / "repo"
    target.mkdir()

    class _RaisingClaude:
        """A claude runner whose run() raises on the planner call.

        Design: §8 forces an orchestrator-internal failure during planning so the
            engine's failed-finalization and lock-release cleanup are exercised.
        Implementation: run() raises RuntimeError; interrupt/aclose are no-op
            coroutines so terminal cleanup can await aclose without error.
        Example: await _RaisingClaude().run(prompt='x', options=None) raises.
        """

        last_session_id: str | None = None

        async def run(self, *, prompt, options):
            """Raise to simulate a planner failure.

            Design: §8 the planner call must fail to reach the failed path.
            Implementation: unconditionally raise RuntimeError.
            Example: awaiting run(...) raises RuntimeError('planner exploded').
            """
            raise RuntimeError("planner exploded")

        async def interrupt(self):
            """No-op interrupt to satisfy the runner surface.

            Design: §8 cleanup may signal interrupt; the fake ignores it.
            Implementation: return immediately.
            Example: await _RaisingClaude().interrupt() returns None.
            """

        async def aclose(self):
            """No-op close so terminal cleanup can await aclose cleanly.

            Design: §8 terminal cleanup awaits aclose on each driver.
            Implementation: return immediately.
            Example: await _RaisingClaude().aclose() returns None.
            """

    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text="# design",
        design_fingerprint="fp",
        max_iterations=1,
        max_runtime_minutes=600,
        claude_runner=_RaisingClaude(),
        codex_runner=codex,
        when=WHEN,
    )

    assert result.status == "failed"
    assert result.failure_kind is not None
    # The lock was released by terminal cleanup: a fresh acquire succeeds.
    lock = TargetLock()
    lock.acquire(target, "later-run", "t")
    lock.release()
