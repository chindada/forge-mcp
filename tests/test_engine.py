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


@pytest.mark.driver
async def test_single_plan_run_completes_and_merges(tmp_path: Path):
    """Design: §3.1 a one-plan run plans, executes, merges, verifies, finalizes 'completed'.
    Implementation: planner returns one plan that creates a file; evaluator no_gaps.
    Example: RunResult.status == 'completed' and the file lands in target_dir.
    """
    target = tmp_path / "repo"
    target.mkdir()
    planset = {
        "plans": [
            {
                "id": "p1",
                "depends_on": [],
                "surface": "backend",
                "file_scope": ["**"],
                "verification_command": None,
                "body": "create out.txt",
            }
        ],
        "run_verification_command": None,
    }
    # planner result, then evaluator no_gaps for the single iteration:
    claude = FakeClaudeRunner(
        [structured(planset), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
    )

    # the fake generator "implements" by writing into the sandbox via a hook:
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda sandbox: (Path(sandbox) / "out.txt").write_text("done"),
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
    assert (target / "out.txt").read_text() == "done"
    assert result.verified is False  # no run-level command declared -> honest False


@pytest.mark.driver
async def test_run_is_fresh_new_dir_each_call(tmp_path: Path):
    """Design: §6.6/I12 every call is a fresh timestamped run (no resume).
    Implementation: two runs produce two distinct run dirs.
    Example: run_dir paths differ.
    """
    target = tmp_path / "repo"
    target.mkdir()
    planset = {
        "plans": [
            {
                "id": "p1",
                "depends_on": [],
                "surface": "backend",
                "file_scope": ["**"],
                "verification_command": None,
                "body": "noop",
            }
        ],
        "run_verification_command": None,
    }

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
                [structured(planset), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
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


# ---------------------------------------------------------------------------
# Wave-boundary integration tests (conflict / amendment / failure machinery)
# ---------------------------------------------------------------------------

_NO_GAPS = {"no_gaps": True, "summary": "ok", "gaps": []}


def _two_plan_planset(scope1: list[str], scope2: list[str]) -> dict:
    """Build a two-independent-plan planset with the given file scopes.

    Design: §7.4 the conflict tests need two plans with no dependency edge that
        both touch the same path; their file_scope decides the conflict winner so
        the helper exposes both scopes.
    Implementation: return a planset dict with plans p1/p2, no depends_on, the
        given scopes, and no verification commands.
    Example: _two_plan_planset(['a/*'], ['**'])['plans'][0]['id'] == 'p1'.
    """
    return {
        "plans": [
            {
                "id": "p1",
                "depends_on": [],
                "surface": "backend",
                "file_scope": scope1,
                "verification_command": None,
                "body": "write f.txt",
            },
            {
                "id": "p2",
                "depends_on": [],
                "surface": "backend",
                "file_scope": scope2,
                "verification_command": None,
                "body": "write f.txt",
            },
        ],
        "run_verification_command": None,
    }


def _write_ft_by_plan(sandbox: str) -> None:
    """Write f.txt into *sandbox* with content keyed by the owning plan id.

    Design: §7.4 the conflict test needs each plan to write the SAME path with
        DISTINCT content so the assertion can prove exactly one plan's content
        survived the merge; the plan id is recoverable from the sandbox path
        (plans/<id>/sandbox).
    Implementation: derive the plan id from the sandbox path's parent dir name and
        write 'from-<id>' into f.txt.
    Example: _write_ft_by_plan('/r/plans/p1/sandbox') writes 'from-p1' to f.txt.
    """
    plan_id = Path(sandbox).parent.name
    (Path(sandbox) / "f.txt").write_text(f"from-{plan_id}")


@pytest.mark.driver
async def test_two_plans_conflict_loser_requeues_and_converges(tmp_path: Path):
    """Design: §7.4 two plans writing the same path conflict; the loser gets an
        ordering edge, re-runs in a later wave, and the run converges (no loop).
    Implementation: planner returns p1 and p2 both writing f.txt with tied scopes
        (one glob each) so the conflict winner is the lower id p1; p2 loses, gains a
        depends_on(p1) edge, and re-runs as a delta after p1 merges.
    Example: the run finalizes 'completed' with f.txt holding the loser's content.
    """
    target = tmp_path / "repo"
    target.mkdir()
    # Both scopes have ONE glob -> scope specificity ties -> the lower id (p1) wins;
    # p2 is the loser that gains an edge and re-runs after p1 merges.
    planset = _two_plan_planset(scope1=["**"], scope2=["f.txt"])
    # 1 planner + plenty of no_gaps evals (wave1: p1+p2, wave2: winner, wave3: loser).
    claude = FakeClaudeRunner([structured(planset)] + [structured(_NO_GAPS)] * 10)
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})], on_generate=_write_ft_by_plan
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

    # The scripted fakes always allow convergence when the engine is correct, so a
    # correct engine MUST finalize 'completed' — anything else (e.g. a winner whose
    # stale sandbox was not discarded -> 'incomplete') is a regression we must catch.
    assert result.status == "completed", result.stop_reason
    fpath = target / "f.txt"
    # Both plans merged; exactly one content survived (the last delta to merge).
    assert fpath.read_text() in ("from-p1", "from-p2")
    run_dir = Path(result.run_dir)
    # One plan (the loser) gained a depends_on edge recorded in planset.json,
    # proving the conflict added a loser->winner ordering edge.
    plans = json.loads((run_dir / "planset.json").read_text())["plans"]
    deps_by_id = {p["id"]: p["depends_on"] for p in plans}
    assert any(deps for deps in deps_by_id.values()), deps_by_id
    loser = next(pid for pid, deps in deps_by_id.items() if deps)
    winner = deps_by_id[loser][0]
    # The loser re-ran in a LATER wave than the winner: its delta content is the
    # one that survived (last to merge), proving it re-ran after the winner.
    assert fpath.read_text() == f"from-{loser}"
    # Both plans have a merge.json -> both participated across waves.
    assert (run_dir / "plans" / loser / "merge.json").exists()
    assert (run_dir / "plans" / winner / "merge.json").exists()


@pytest.mark.driver
async def test_conflict_records_fingerprint_file(tmp_path: Path):
    """Design: §3.1/§11 a conflicting wave must persist conflict_fingerprint.json.
    Implementation: force a two-plan f.txt conflict; assert the run's
        conflict_fingerprint.json exists on disk and is non-empty (FIX 1).
    Example: conflict_fingerprint.json contains the recorded conflict fingerprints.
    """
    target = tmp_path / "repo"
    target.mkdir()
    planset = _two_plan_planset(scope1=["**"], scope2=["f.txt"])
    claude = FakeClaudeRunner([structured(planset)] + [structured(_NO_GAPS)] * 10)
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})], on_generate=_write_ft_by_plan
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

    cfp = Path(result.run_dir) / "conflict_fingerprint.json"
    assert cfp.exists()
    assert cfp.read_text().strip() not in ("", "[]")


@pytest.mark.driver
async def test_unresolvable_overlap_stops_incomplete(tmp_path, monkeypatch):
    """Design: §6.7/§3.1 a perpetually-recurring conflict trips run-level EARLY_STOP.
    Implementation: two tied independent plans repeatedly write the same path; with
        no ordering edge ever established (the cycle-rejected-edge case — simulated
        by patching add_conflict_edge to always refuse), the identical conflict
        fingerprint recurs each wave until detect_non_progress fires.
    Example: status 'incomplete' with stop_reason naming the cross-plan overlap.
    """
    import forge_mcp.orchestrator.engine as engine_mod

    # Model the cycle-rejected-edge case: no loser->winner ordering is ever added,
    # so the same f.txt conflict recurs every wave (perpetual overlap).
    monkeypatch.setattr(engine_mod, "add_conflict_edge", lambda planset, *, loser, winner: False)

    target = tmp_path / "repo"
    target.mkdir()
    planset = _two_plan_planset(scope1=["f.txt"], scope2=["f.txt"])
    claude = FakeClaudeRunner([structured(planset)] + [structured(_NO_GAPS)] * 40)
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda sb: (Path(sb) / "f.txt").write_text("x"),
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
    assert "overlap" in result.stop_reason


_DESIGN_WITH_TARGET = "Alpha section. The widget must flush before close. Omega section."


def _amendment_triage() -> dict:
    """Build a TriageResult dict carrying one VALIDATED design-fault amendment.

    Design: §5.3 a triage row stops the wave only when it is a design fault with a
        verbatim ≥20-char citation and a proposed_amendment whose 'before' is a
        verbatim spec substring; the test needs exactly such a row to drive the
        engine's amend path.
    Implementation: cite the long phrase from _DESIGN_WITH_TARGET and amend the
        'flush before close' substring to insert 'and fsync'.
    Example: _amendment_triage()['triages'][0]['design_fault'] is True.
    """
    cite = "The widget must flush before close"
    return {
        "triages": [
            {
                "gap_title": "spec contradiction",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": [cite],
                "explanation": "the spec contradicts itself",
                "proposed_amendment": {
                    "cited_sections": [cite],
                    "before": "flush before close",
                    "after": "flush and fsync before close",
                    "rationale": "durability requires fsync",
                },
            }
        ]
    }


@pytest.mark.driver
async def test_awaiting_amendment_applies_and_requeues(tmp_path):
    """Design: §5.3 a validated design-fault amendment is applied at the wave
        boundary, the spec evolves, and the proposing plan re-runs against it.
    Implementation: wave 1 eval finds a gap, triage validates a design fault →
        awaiting_amendment; the amendment applies; wave 2 eval returns no_gaps so
        the plan completes against the amended spec.
    Example: spec.md content changes, spec_amendments.md is non-empty, status
        'completed'.
    """
    target = tmp_path / "repo"
    target.mkdir()
    planset = {
        "plans": [
            {
                "id": "p1",
                "depends_on": [],
                "surface": "backend",
                "file_scope": ["**"],
                "verification_command": None,
                "body": "implement widget",
            }
        ],
        "run_verification_command": None,
    }
    gap_eval = {
        "no_gaps": False,
        "summary": "found a gap",
        "gaps": [
            {
                "title": "spec contradiction",
                "severity": "high",
                "design_doc_section": "Alpha section",
                "current_state": "ambiguous",
                "expected_state": "deterministic",
                "suggested_fix": "amend the spec",
            }
        ],
    }
    # planner, then wave1: eval(gap) -> triage(design fault) -> awaiting_amendment;
    # wave2 (post-amendment): eval(no_gaps) -> done.
    claude = FakeClaudeRunner(
        [
            structured(planset),
            structured(gap_eval),
            structured(_amendment_triage()),
            structured(_NO_GAPS),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda sb: (Path(sb) / "out.txt").write_text("widget"),
    )

    orch = Orchestrator()
    result = await orch.run(
        target_dir=target,
        design_text=_DESIGN_WITH_TARGET,
        design_fingerprint="fp",
        max_iterations=1,
        max_runtime_minutes=600,
        claude_runner=claude,
        codex_runner=codex,
        when=WHEN,
    )

    run_dir = Path(result.run_dir)
    spec_after = (run_dir / "spec.md").read_text()
    # The spec was amended (the 'before' phrase replaced with the 'after' phrase).
    assert "fsync" in spec_after
    assert spec_after != _DESIGN_WITH_TARGET
    # The amendment was logged.
    assert (run_dir / "spec_amendments.md").read_text().strip() != ""
    # The proposing plan re-ran against the amended spec and the run progressed.
    assert result.status == "completed"


@pytest.mark.driver
async def test_planner_raises_finalizes_failed(tmp_path):
    """Design: §6.4/§3.1 an orchestrator-internal error finalizes 'failed' with a
        failure_kind, and the terminal cleanup still releases the per-target lock.
    Implementation: the planner's first claude.run raises; assert failed status,
        a set failure_kind, and that the lock is released (run.lock gone / re-acquirable).
    Example: status 'failed', failure_kind set, a fresh TargetLock acquires cleanly.
    """
    from forge_mcp.lockfile import TargetLock

    target = tmp_path / "repo"
    target.mkdir()

    class _RaisingClaude:
        """A claude runner whose run() raises on the planner call.

        Design: §6.4 forces an orchestrator-internal failure during planning so the
            engine's failed-finalization and lock-release cleanup are exercised.
        Implementation: run() raises RuntimeError; interrupt/aclose are no-op
            coroutines so terminal cleanup can await aclose without error.
        Example: await _RaisingClaude().run(prompt='x', options=None) raises.
        """

        last_session_id: str | None = None

        async def run(self, *, prompt, options):
            """Raise to simulate a planner failure.

            Design: §6.4 the planner call must fail to reach the failed path.
            Implementation: unconditionally raise RuntimeError.
            Example: awaiting run(...) raises RuntimeError('planner exploded').
            """
            raise RuntimeError("planner exploded")

        async def interrupt(self):
            """No-op interrupt to satisfy the runner surface.

            Design: §3.1 cleanup may signal interrupt; the fake ignores it.
            Implementation: return immediately.
            Example: await _RaisingClaude().interrupt() returns None.
            """

        async def aclose(self):
            """No-op close so terminal cleanup can await aclose cleanly.

            Design: §3.1 terminal cleanup awaits aclose on each driver.
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


@pytest.mark.driver
async def test_cyclic_planset_finalizes_incomplete(tmp_path: Path):
    """Design: §7.1/§3.1 a cyclic planner DAG can never schedule; the engine must
        finalize cleanly (not strand on an illegal state-machine transition).
    Implementation: the planner returns p1 depends_on p2 and p2 depends_on p1; the
        has_cycle guard routes a graceful 'incomplete' finalize through the legal
        phase path.
    Example: status 'incomplete', stop_reason mentions the cyclic/unschedulable
        plan set, and the per-target lock is released afterwards.
    """
    from forge_mcp.lockfile import TargetLock

    target = tmp_path / "repo"
    target.mkdir()
    cyclic_planset = {
        "plans": [
            {
                "id": "p1",
                "depends_on": ["p2"],
                "surface": "backend",
                "file_scope": ["**"],
                "verification_command": None,
                "body": "a",
            },
            {
                "id": "p2",
                "depends_on": ["p1"],
                "surface": "backend",
                "file_scope": ["**"],
                "verification_command": None,
                "body": "b",
            },
        ],
        "run_verification_command": None,
    }
    claude = FakeClaudeRunner([structured(cyclic_planset)])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])

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
    assert "cycl" in result.stop_reason or "unschedulable" in result.stop_reason
    # The lock was released: a fresh acquire on the same target succeeds.
    lock = TargetLock()
    lock.acquire(target, "later-run", "t")
    lock.release()
