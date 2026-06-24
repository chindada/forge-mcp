"""The §3.1 run conductor: wire every module into a wave-structured run.

The Orchestrator.run coroutine is the integration capstone. It acquires the
per-target lock, freezes the design into a run-local spec, plans, then drives
the DAG one wave at a time (schedule → execute → merge → amend → invalidate)
until every plan is terminal or a run-level non-progress signal fires; it then
runs the optional post-merge run-level verification and finalises an honest
RunResult. The engine performs NO git mutation — the merge is a directory copy —
and its terminal cleanup (mark state, close drivers, release lock, restore
umask) runs in a `finally` and re-raises an injected CancelledError.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from forge_mcp.artifacts import init_run_layout
from forge_mcp.config import CONCURRENCY_CAP, create_run_dir
from forge_mcp.convergence import detect_non_progress, fingerprint
from forge_mcp.drivers.planner import run_planner
from forge_mcp.gitguard import capture_state
from forge_mcp.lockfile import TargetLock
from forge_mcp.models import EvalResult, PlanSet, RunResult, TriageResult
from forge_mcp.orchestrator.amend import apply_amendments
from forge_mcp.orchestrator.lifecycle import PlanReport, build_run_result
from forge_mcp.orchestrator.scheduler import (
    _deps_map,
    _transitive_deps,
    add_conflict_edge,
    has_cycle,
    ready_plans,
    run_wave,
)
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.sandbox import (
    Change,
    Conflict,
    WriterMap,
    apply_merge,
    detect_conflicts,
    resolve_conflict_winner,
)
from forge_mcp.state import light_replace, write_json
from forge_mcp.verifier import run_verification

if TYPE_CHECKING:
    from forge_mcp.artifacts import RunLayout
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner
    from forge_mcp.models import GapTriage
    from forge_mcp.orchestrator.phases import PlanLoopResult

_RESTRICTIVE_UMASK = 0o077


@dataclass
class _RunState:
    """Mutable bookkeeping carried across the §3.1 wave loop.

    Design: §3.1 the wave loop accumulates cross-wave state — which plans have
        merged, which are still pending, the cumulative WriterMap, the evolving
        spec, and the two run-level non-progress histories — that finalisation
        and each subsequent wave must read; bundling it keeps run() readable.
    Implementation: plain mutable dataclass; the conflict/amendment histories are
        lists of frozenset fingerprints fed to detect_non_progress; reports holds
        the freshest PlanLoopResult per plan for honest gap projection.
    Example: _RunState(spec_text='s', spec_fingerprint='fp') starts a run.
    """

    spec_text: str
    spec_fingerprint: str
    merged: set[str] = field(default_factory=set)
    pending: set[str] = field(default_factory=set)
    writer_map: WriterMap = field(default_factory=WriterMap)
    conflict_history: list[frozenset[str]] = field(default_factory=list)
    amendment_history: list[frozenset[str]] = field(default_factory=list)
    reports: dict[str, PlanLoopResult] = field(default_factory=dict)
    iterations: int = 0
    stop_reason: str | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §3.3 the run-level state machine records a last_updated_at on every
        transition; the engine is the caller so it supplies the wall clock. This
        is the checkpoint clock only — the run-dir freshness clock (`when`) is
        injected by the caller (I12), never read here.
    Implementation: datetime.now in UTC serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


def _sandbox_of(layout: RunLayout, plan_id: str) -> Path:
    """Return the sandbox path run_wave created for *plan_id* under *layout*.

    Design: §7.2 run_wave lazily copies each ready plan's sandbox to
        plans/<id>/sandbox; the merge phase reads those same trees to apply each
        done plan's change_set, so both must derive the path identically.
    Implementation: layout.plan_dir(plan_id) / 'sandbox' — the exact location the
        scheduler's _sandbox_path uses when a real layout is supplied.
    Example: _sandbox_of(layout, 'p1') == layout.plan_dir('p1') / 'sandbox'.
    """
    return layout.plan_dir(plan_id) / "sandbox"


def _discard_sandbox(layout: RunLayout, plan_id: str) -> None:
    """Remove *plan_id*'s sandbox so a re-queued plan can be re-copied next wave.

    Design: §7.4/§5.3 a re-queued plan (a conflict loser or a plan invalidated by
        an amendment) re-runs from the CURRENT merged tree next wave; run_wave's
        copy_sandbox requires a non-existent destination, so the prior sandbox must
        be discarded first or the re-copy would raise and the plan would resolve
        `failed` instead of re-running.
    Implementation: rmtree the plan's sandbox dir, ignoring a missing path so the
        call is idempotent.
    Example: _discard_sandbox(layout, 'p1') deletes plans/p1/sandbox if present.
    """
    shutil.rmtree(_sandbox_of(layout, plan_id), ignore_errors=True)


def _make_is_dependent(planset: PlanSet):
    """Return is_dependent(a, b) == True iff b is a transitive dependency of a.

    Design: §7.4 conflict detection must not flag a plan that legitimately builds
        on a transitive dependency it already merged; the predicate reuses the
        scheduler's transitive-dep notion so engine and scheduler agree on the DAG.
    Implementation: build the direct-edge map once, then close over a function
        that tests membership of b in _transitive_deps(a). The closure re-reads the
        map each call so conflict-resolution edges added mid-run are honoured.
    Example: for p2 depends_on p1, is_dependent('p2', 'p1') is True.
    """

    def is_dependent(a: str, b: str) -> bool:
        """Return True when *b* is a transitive dependency of *a* in the planset.

        Design: §7.4 a later plan touching an earlier writer's path is allowed
            only when it depends on that writer; this answers exactly that.
        Implementation: recompute the direct-edge map (cheap) and test b against
            the transitive closure of a so freshly-added conflict edges count.
        Example: is_dependent('p2', 'p1') is True when p2 depends_on p1.
        """
        return b in _transitive_deps(a, _deps_map(planset))

    return is_dependent


def _deleted_dir_paths(change_set: list[Change]) -> list[str]:
    """Return the paths a change_set deletes as directories.

    Design: §7.4 (Task 13 review WATCH) a dir-prefix conflict flags the file
        UNDER a deleted directory; the engine must also block the deleted-dir
        path itself so apply_merge never recreates a directory one plan removed.
    Implementation: keep each Change whose kind is 'deleted' and entry_kind is
        'dir'; return their paths.
    Example: _deleted_dir_paths([Change('d', 'deleted', 'dir', None)]) == ['d'].
    """
    return [c.path for c in change_set if c.kind == "deleted" and c.entry_kind == "dir"]


def _conflicting_paths(
    conflicts: list[Conflict],
    changes_by_plan: dict[str, list[Change]],
) -> set[str]:
    """Build the set of paths apply_merge must skip, including blocked deleted dirs.

    Design: §7.4 every conflict's contested path is excluded from the merge; the
        Task 13 WATCH additionally requires that when a conflict sits UNDER a
        directory some plan deleted, the deleted-dir path is blocked too so the
        merge does not resurrect a directory that lost the conflict.
    Implementation: seed the set with each conflict path; for every deleted-dir
        path across all plans, block it as well when any conflict path lies under
        that directory prefix.
    Example: a conflict on 'd/f' plus a plan deleting dir 'd' blocks {'d/f', 'd'}.
    """
    paths = {c.path for c in conflicts}
    deleted_dirs: set[str] = set()
    for change_set in changes_by_plan.values():
        deleted_dirs.update(_deleted_dir_paths(change_set))
    for d in deleted_dirs:
        prefix = d + "/"
        if any(p.startswith(prefix) for p in paths):
            paths.add(d)
    return paths


def _write_merge_record(
    layout: RunLayout,
    plan_id: str,
    *,
    merge_status: str,
    applied_paths: list[str],
    now: str,
) -> None:
    """Write the §11 per-plan merge.json recording this plan's merge outcome.

    Design: §11 each plan owns a merge.json carrying its merge_status (merged /
        conflicted / skipped) and the paths it contributed, so the run is
        auditable after the fact; this file is orchestrator-owned (never the
        plan's state.json).
    Implementation: durably write a small dict via write_json to
        layout.plan_merge(plan_id).
    Example: _write_merge_record(lay, 'p1', merge_status='merged',
        applied_paths=['out.txt'], now=t) writes plans/p1/merge.json.
    """
    write_json(
        layout.plan_merge(plan_id),
        {"merge_status": merge_status, "applied_paths": applied_paths, "now": now},
        durable=True,
    )


class Orchestrator:
    """The §3.1 run conductor wiring every module into a wave-structured run.

    Design: §3.1 a forge run is a single async lifecycle — lock, freeze, plan,
        then repeat (schedule → execute → merge → amend → invalidate) until every
        plan is terminal or a run-level non-progress signal fires, then run the
        optional post-merge verification and finalise an honest RunResult. The
        engine performs NO git mutation (the merge is a directory copy) and its
        terminal cleanup runs in a `finally` that re-raises CancelledError.
    Implementation: a stateless class whose `run` coroutine owns all per-run
        state locally; the SDK runners are injected so the engine never imports
        the SDK at module scope, and `when` is injected so each call gets a fresh
        timestamped run dir (I12) without reading the wall clock for the dir name.
    Example: ``await Orchestrator().run(target_dir=..., design_text=..., ...)``.
    """

    async def run(
        self,
        *,
        target_dir: Path,
        design_text: str,
        design_fingerprint: str,
        max_iterations: int,
        max_runtime_minutes: int,
        claude_runner: ClaudeRunner,
        codex_runner: CodexRunner,
        when: time.struct_time,
    ) -> RunResult:
        """Drive one §3.1 forge run end-to-end and return an honest RunResult.

        Design: §3.1 the full flow — acquire the per-target lock, create a fresh
            timestamped run dir from the injected `when` (I12), freeze design→spec
            via init_run_layout, PLAN, then repeat WAVES (schedule/execute/merge/
            amend/invalidate) until all plans are terminal or run-level non-progress
            stops it incomplete, then VERIFY-RUN (post-merge run-level command) and
            finalise. `verified` is True only if every completed plan's per-plan
            gate AND the run-level gate passed; absent a run-level command it is an
            honest False. status is completed iff all plans done ∧ verify passed-or-
            absent, else incomplete; failed only for an orchestrator-internal error.
        Implementation: set a restrictive umask (restored in finally); acquire the
            lock keyed on the run-dir name; build the layout and RunStateMachine;
            run the planner; loop _run_waves; run the optional run-level verify;
            build the RunResult via lifecycle.build_run_result. The whole body is
            wrapped so the `finally` marks the terminal state, closes both drivers
            best-effort, and releases the lock with NO git ops; an injected
            CancelledError runs that cleanup and re-raises.
        Example: a one-plan run that writes out.txt finalises status 'completed'
            with out.txt present in target_dir and verified False.
        """
        prev_umask = os.umask(_RESTRICTIVE_UMASK)
        lock = TargetLock()
        sm: RunStateMachine | None = None
        run_dir: Path | None = None
        status = "failed"
        try:
            run_dir = create_run_dir(target_dir, when)
            run_id = run_dir.name
            lock.acquire(target_dir, run_id, _now())
            layout = init_run_layout(run_dir, design_text, design_fingerprint=design_fingerprint)
            sm = RunStateMachine(layout)

            state = _RunState(spec_text=design_text, spec_fingerprint=design_fingerprint)

            # --- PLAN ---
            sm.transition("planning", now=_now())
            planset = await run_planner(
                claude_runner,
                spec_text=layout.spec_md.read_text(),
                plan_schema=PlanSet.model_json_schema(),
                cwd=target_dir,
            )
            self._persist_planset(layout, planset)
            state.pending = {p.id for p in planset.plans}

            # --- GUARD: a cyclic planner DAG can never linearise into waves. ---
            # Finalise cleanly as 'incomplete' via the legal phase path rather than
            # stranding the run on an illegal state-machine transition.
            if has_cycle(planset):
                status, result = self._finalize_unschedulable(
                    run_dir=run_dir,
                    sm=sm,
                    planset=planset,
                    stop_reason="planner produced a cyclic plan set (unschedulable)",
                )
                return result

            # --- WAVES ---
            await self._run_waves(
                target_dir=target_dir,
                layout=layout,
                sm=sm,
                planset=planset,
                state=state,
                max_iterations=max_iterations,
                claude_runner=claude_runner,
                codex_runner=codex_runner,
            )

            # --- VERIFY-RUN (post-merge run-level gate) ---
            sm.transition("verifying", now=_now())
            run_verified, verify_note = self._run_level_verify(target_dir, planset)

            # --- FINALIZE ---
            sm.transition("finalizing", now=_now())
            status, result = self._finalize(
                run_dir=run_dir,
                planset=planset,
                state=state,
                run_verified=run_verified,
                verify_note=verify_note,
            )
            return result
        except asyncio.CancelledError:
            # Host disconnect / runtime-cap wait_for: run terminal cleanup, re-raise.
            status = "failed"
            raise
        except Exception as exc:  # noqa: BLE001 — orchestrator-internal error -> failed.
            # §6.4 an orchestrator-internal/unhandled error finalises a `failed`
            # RunResult with failure_kind (NOT a per-plan or convergence failure).
            status = "failed"
            return build_run_result(
                status="failed",
                run_dir=str(run_dir) if run_dir is not None else "",
                iterations=0,
                non_completed=[],
                stop_reason=None,
                verified=False,
                summary=f"Orchestrator failed: {type(exc).__name__}: {exc}",
                failure_kind=type(exc).__name__,
            )
        finally:
            await self._terminal_cleanup(
                sm=sm,
                status=status,
                claude_runner=claude_runner,
                codex_runner=codex_runner,
                lock=lock,
                prev_umask=prev_umask,
            )

    def _persist_planset(self, layout: RunLayout, planset: PlanSet) -> None:
        """Write planset.json and one plan-<id>.md per plan after planning (§11).

        Design: §11/§3.1 the planner output is durably recorded so the run is
            reconstructable: planset.json holds the structured set and each plan
            gets a human-readable plan-<id>.md carrying its body.
        Implementation: write_json the planset model durably, then light_replace
            each plan's body into layout.plan_md(id) (derived artifact, non-durable).
        Example: _persist_planset(layout, ps) writes planset.json and plan-p1.md.
        """
        write_json(layout.planset_json, planset, durable=True)
        for plan in planset.plans:
            light_replace(layout.plan_md(plan.id), plan.body)

    async def _run_waves(
        self,
        *,
        target_dir: Path,
        layout: RunLayout,
        sm: RunStateMachine,
        planset: PlanSet,
        state: _RunState,
        max_iterations: int,
        claude_runner: ClaudeRunner,
        codex_runner: CodexRunner,
    ) -> None:
        """Repeat the §3.1 wave loop until every plan is terminal or stops the run.

        Design: §3.1 each wave schedules the ready pending plans under the
            concurrency cap, executes them against the current merged tree, merges
            the done plans' change_sets (resolving conflicts by re-queuing losers),
            applies validated amendments, and invalidates plans whose scope a merge
            conflict or amended section touched. The loop ends when no plan is
            pending (all terminal) or a run-level non-progress signal fires.
        Implementation: while pending and no stop_reason, compute the ready wave
            FIRST (an empty wave means every remaining plan is blocked on an unmet
            dep, so set stop_reason and break WITHOUT entering scheduling — that
            keeps the run-state at the prior wave's `amending`/`planning`, from which
            verifying is reachable); otherwise transition through scheduling/
            executing/merging/amending, mutating `state`. Run-level non-progress over
            the conflict and amendment histories sets stop_reason and breaks.
        Example: a single clean plan runs one wave, merges, and leaves pending empty.
        """
        while state.pending and state.stop_reason is None:
            # Compute readiness BEFORE the scheduling transition so an empty wave
            # never strands the run-state at 'scheduling' (verifying is unreachable
            # from there); the first iteration always has a ready no-dep plan.
            wave = ready_plans(planset, state.merged, state.pending)[:CONCURRENCY_CAP]
            if not wave:
                # Nothing schedulable (remaining plans blocked on unmet deps): the
                # run cannot progress further — leave them as non-completed.
                state.stop_reason = "no schedulable plans (blocked dependencies)"
                break

            # --- scheduling ---
            sm.transition("scheduling", now=_now())

            # --- executing ---
            sm.transition("executing", now=_now())
            base_git_state = capture_state(target_dir)
            results = await run_wave(
                wave,
                runner_factory=lambda: (claude_runner, codex_runner),
                target_dir=target_dir,
                layout=layout,
                concurrency=CONCURRENCY_CAP,
                planset=planset,
                spec_text=state.spec_text,
                schemas={
                    "eval": EvalResult.model_json_schema(),
                    "triage": TriageResult.model_json_schema(),
                },
                max_iterations=max_iterations,
                base_git_state=base_git_state,
            )
            for plan_id, result in results.items():
                state.reports[plan_id] = result
                state.iterations += result.iterations

            # --- merging ---
            sm.transition("merging", now=_now())
            self._merge_wave(
                target_dir=target_dir,
                layout=layout,
                planset=planset,
                state=state,
                results=results,
            )

            # --- amending ---
            sm.transition("amending", now=_now())
            self._amend_wave(layout=layout, planset=planset, state=state, results=results)

            # --- run-level non-progress (conflict overlap + amendment thrash) ---
            if detect_non_progress(state.conflict_history) == "EARLY_STOP":
                state.stop_reason = "unresolvable cross-plan overlap"
                break
            if detect_non_progress(state.amendment_history) == "EARLY_STOP":
                state.stop_reason = "amendment thrash"
                break

    def _merge_wave(
        self,
        *,
        target_dir: Path,
        layout: RunLayout,
        planset: PlanSet,
        state: _RunState,
        results: dict[str, PlanLoopResult],
    ) -> None:
        """Merge the wave's done plans, resolving conflicts by re-queuing losers (§7.4).

        Design: §7.4 only plans whose terminal_state is 'done' contribute a
            change_set; sibling and cross-wave overlaps are detected against the
            cumulative WriterMap honouring transitive dependence; conflicting paths
            are skipped (incl. a blocked deleted-dir per the Task 13 WATCH). On a
            conflict the contributing plans are marked conflicted, a winner is
            chosen, a loser→winner edge is added (skipped if it would cycle), the
            losers are re-queued, and the conflict is recorded in the run-level
            history. Non-conflicted done plans are recorded in the WriterMap.
        Implementation: build changes_by_plan and the sandbox map from done plans;
            detect_conflicts; compute the skip set; apply_merge; per plan write its
            merge.json (merged/conflicted) and update merged/pending; resolve each
            conflict's winner/loser, add_conflict_edge, re-queue losers, append the
            wave's conflict fingerprints to the history.
        Example: one clean done plan writes merge.json merge_status='merged' and
            its files land in target_dir.
        """
        done_ids = [pid for pid, r in results.items() if r.terminal_state == "done"]
        changes_by_plan: dict[str, list[Change]] = {
            pid: (results[pid].change_set or []) for pid in done_ids
        }
        sandboxes = {pid: _sandbox_of(layout, pid) for pid in done_ids}

        is_dependent = _make_is_dependent(planset)
        conflicts = detect_conflicts(changes_by_plan, state.writer_map, is_dependent)
        conflicted_ids = {pid for c in conflicts for pid in c.plan_ids}
        skip = _conflicting_paths(conflicts, changes_by_plan)

        apply_merge(target_dir, sandboxes, changes_by_plan, skip)
        now = _now()

        for pid in done_ids:
            if pid in conflicted_ids:
                continue
            applied = [c.path for c in changes_by_plan[pid] if c.path not in skip]
            for path in applied:
                state.writer_map.record(path, pid)
            _write_merge_record(layout, pid, merge_status="merged", applied_paths=applied, now=now)
            state.merged.add(pid)
            state.pending.discard(pid)

        # Non-done terminal plans (failed / incomplete with no amendment) leave the
        # pending set so the run finalises with them as non-completed.
        for pid, r in results.items():
            if r.terminal_state in ("failed", "incomplete"):
                state.pending.discard(pid)

        if conflicts:
            self._resolve_conflicts(
                layout=layout,
                planset=planset,
                state=state,
                conflicts=conflicts,
                conflicted_ids=conflicted_ids,
                now=now,
            )
            state.conflict_history.append(fingerprint(c.fingerprint() for c in conflicts))
            # §3.1/§11 persist the cumulative conflict-fingerprint history so the
            # run's recurring-overlap record survives on disk, not just in memory.
            write_json(
                layout.conflict_fingerprint,
                [sorted(fp) for fp in state.conflict_history],
                durable=True,
            )

    def _resolve_conflicts(
        self,
        *,
        layout: RunLayout,
        planset: PlanSet,
        state: _RunState,
        conflicts: list[Conflict],
        conflicted_ids: set[str],
        now: str,
    ) -> None:
        """Mark conflicted plans, pick winners, add ordering edges, re-queue all (§7.4).

        Design: §7.4 every plan in a conflict loses its merge THIS wave — neither
            the winner nor the loser merged (both were skipped) — so BOTH must
            re-run: the loser gains a depends_on(winner) edge so it re-runs only
            AFTER the winner merged, while the winner re-runs unblocked. An edge that
            would cycle is skipped (existing order kept). Each conflicted plan stays
            pending and its never-merged sandbox is discarded so run_wave can
            re-copy a fresh one from the current target tree next wave (its
            unconditional copy_sandbox would otherwise raise on the stale dir and
            resolve the plan `failed`).
        Implementation: write each conflicted plan a merge.json merge_status=
            'conflicted' and discard its sandbox; for each conflict resolve
            winner/loser via the plans' file_scope and add_conflict_edge(loser→
            winner). All conflicted plans are already in pending (they were `done`
            but skipped, never discarded), so no explicit re-queue is needed beyond
            ensuring they are not left in merged. After adding edges, re-persist
            planset.json so the run-time DAG (with conflict edges) is auditable (§11).
        Example: two plans contesting 'f' both re-run; the loser gains a new edge so
            it follows the winner.
        """
        scope = {p.id: list(p.file_scope) for p in planset.plans}
        for pid in conflicted_ids:
            _write_merge_record(layout, pid, merge_status="conflicted", applied_paths=[], now=now)
            _discard_sandbox(layout, pid)
            state.merged.discard(pid)
            state.pending.add(pid)

        edge_added = False
        for conflict in conflicts:
            ids = conflict.plan_ids
            if len(ids) < 2:
                continue
            winner, loser = resolve_conflict_winner(ids[0], ids[1], scope)
            if add_conflict_edge(planset, loser=loser, winner=winner):
                edge_added = True

        if edge_added:
            # §11 persist the evolved DAG so the loser→winner ordering is auditable.
            write_json(layout.planset_json, planset, durable=True)

    def _amend_wave(
        self,
        *,
        layout: RunLayout,
        planset: PlanSet,
        state: _RunState,
        results: dict[str, PlanLoopResult],
    ) -> None:
        """Apply this wave's validated amendments and invalidate affected plans (§5.3).

        Design: §5.3 plans that stopped awaiting_amendment carry a validated
            design-fault proposal; the orchestrator applies them at the wave
            boundary against the CURRENT spec, advances the spec/fingerprint, and
            records the processed batch in the amendment-churn history (only when
            non-empty). When ≥1 amendment is APPLIED, every already-merged plan may
            now be stale against the amended spec and is invalidated so it re-runs
            as a DELTA; the proposing/awaiting plans (never merged) re-run too. This
            is the conservative-correct reading of §5.3's "plans whose file_scope/
            surface intersects an amended section": we have NO reliable
            section→plan mapping (touched sections are spec-citation strings, not
            surfaces or path globs), so targeting by intersection is unsound — a
            merged plan evaluated against the pre-amendment spec may silently no
            longer match. Invalidating all merged plans is safe and TERMINATES: the
            amendment-churn detect_non_progress bounds repeated amendments, and a
            re-run plan that now matches the amended spec will not re-propose.
        Implementation: drain (plan_id, proposed_amendment) from awaiting_amendment
            reports; if none, return. Call apply_amendments; update state.spec_text/
            spec_fingerprint; append churn_fingerprint to amendment_history. When any
            amendment applied, re-queue all merged plans as deltas (merge.json stays
            merged, sandbox re-copied from the merged tree which already holds their
            work — so no merged change is lost) and re-queue the awaiting plans with
            a fresh sandbox. When none applied, only re-queue the awaiting plans so
            they do not dangle.
        Example: one awaiting_amendment plan applies its amendment, the spec
            changes, and the plan re-runs against the amended spec.
        """
        proposed: list[tuple[str, GapTriage]] = [
            r.proposed_amendment
            for r in results.values()
            if r.terminal_state == "awaiting_amendment" and r.proposed_amendment is not None
        ]
        if not proposed:
            return

        outcome = apply_amendments(
            layout,
            spec_text=state.spec_text,
            spec_fingerprint=state.spec_fingerprint,
            proposed=proposed,
            now=_now(),
        )
        state.spec_text = outcome.new_spec
        state.spec_fingerprint = outcome.new_fingerprint
        state.amendment_history.append(outcome.churn_fingerprint)

        awaiting_ids = {plan_id for plan_id, _ in proposed}

        # Awaiting plans never merged: discard their sandbox and re-run from the
        # current merged tree against the (possibly amended) spec.
        for plan_id in awaiting_ids:
            _discard_sandbox(layout, plan_id)
            state.pending.add(plan_id)
            state.merged.discard(plan_id)

        if not outcome.applied:
            # No amendment landed (all rejected): only the awaiting plans re-run.
            return

        # ≥1 amendment applied → every already-merged plan may now be stale against
        # the amended spec. Invalidate them all (conservative-correct: no
        # section→plan map). Re-run as a DELTA — merge.json stays 'merged' and the
        # sandbox is re-copied from the merged target tree, which already contains
        # that plan's merged work, so nothing merged is lost.
        for plan_id in list(state.merged):
            _discard_sandbox(layout, plan_id)
            state.pending.add(plan_id)
            state.merged.discard(plan_id)

    def _run_level_verify(self, target_dir: Path, planset: PlanSet) -> tuple[bool, str | None]:
        """Run the optional post-merge run-level verification over the merged tree (§6.5).

        Design: §6.5/§3.1 after the final merge the PlanSet's declared
            run_verification_command (if any) gates the whole assembled tree in
            target_dir; absent a declared command there is no run-level gate, so the
            run cannot honestly claim it was verified.
        Implementation: when run_verification_command is None, return (False, an
            honest note that no run-level command was declared); otherwise run it via
            run_verification(cwd=target_dir) and return (outcome.passed, None).
        Example: a planset with run_verification_command=None returns (False, note).
        """
        command = planset.run_verification_command
        if command is None:
            return False, "no run-level verification command declared"
        outcome = run_verification(command, target_dir)
        return outcome.passed, None

    def _finalize(
        self,
        *,
        run_dir: Path,
        planset: PlanSet,
        state: _RunState,
        run_verified: bool,
        verify_note: str | None,
    ) -> tuple[str, RunResult]:
        """Project the terminal RunResult and its status from the run state (§6.4).

        Design: §6.4/§3.1 status is 'completed' iff every plan merged (done) AND the
            run-level gate passed-or-was-absent-but-honest; otherwise 'incomplete'
            with a stop_reason. `verified` is True ONLY when every completed plan's
            per-plan gate passed (a done terminal_state IS that gate) AND the
            run-level gate passed — absent a run-level command it is an honest False.
            Non-completed plans contribute their freshest gap set.
        Implementation: all_done = no pending and every report done; verified =
            all_done ∧ run_verified; status = completed iff all_done ∧ (run_verified
            or run-level absent) — i.e. all_done and no stop_reason; build the
            PlanReports for non-completed plans and call build_run_result.
        Example: one done plan, no run-level command → status 'completed',
            verified False, stop_reason None.
        """
        non_completed = [
            PlanReport(
                plan_id=pid,
                terminal_state=r.terminal_state,
                gaps=r.last_gaps,
                synthesized=r.synthesized,
                failure_reason=r.stop_reason,
            )
            for pid, r in state.reports.items()
            if r.terminal_state != "done"
        ]
        all_done = not state.pending and not non_completed and state.stop_reason is None
        verified = all_done and run_verified

        if all_done:
            status = "completed"
            summary = f"All {len(state.merged)} plan(s) completed and merged."
            if verify_note:
                summary = f"{summary} ({verify_note})"
        else:
            status = "incomplete"
            summary = "Run finished incomplete; see unresolved_gaps."

        result = build_run_result(
            status=status,
            run_dir=str(run_dir),
            iterations=state.iterations,
            non_completed=non_completed,
            stop_reason=state.stop_reason,
            verified=verified,
            summary=summary,
        )
        return status, result

    def _finalize_unschedulable(
        self,
        *,
        run_dir: Path,
        sm: RunStateMachine,
        planset: PlanSet,
        stop_reason: str,
    ) -> tuple[str, RunResult]:
        """Cleanly finalise a planset that can never be scheduled (e.g. a cycle).

        Design: §3.1/§7.1 a cyclic depends_on DAG can never linearise into waves, so
            no plan can run; the run must finalise gracefully rather than strand on
            an illegal state-machine edge. Every plan is reported non-completed
            (terminal_state 'incomplete') so the caller sees an honest gap per plan.
        Implementation: walk the LEGAL phase chain from 'planning' to 'finalizing'
            (scheduling→executing→merging→amending→verifying→finalizing) doing no
            work, then build an 'incomplete' RunResult with one PlanReport per plan
            and verified=False; the run() finally still marks the terminal state and
            releases the lock.
        Example: a planset with p1↔p2 finalises 'incomplete' with stop_reason naming
            the cyclic plan set.
        """
        for phase in ("scheduling", "executing", "merging", "amending", "verifying", "finalizing"):
            sm.transition(phase, now=_now())
        non_completed = [
            PlanReport(plan_id=p.id, terminal_state="incomplete", failure_reason=stop_reason)
            for p in planset.plans
        ]
        result = build_run_result(
            status="incomplete",
            run_dir=str(run_dir),
            iterations=0,
            non_completed=non_completed,
            stop_reason=stop_reason,
            verified=False,
            summary="Run finished incomplete; the plan set could not be scheduled.",
        )
        return "incomplete", result

    async def _terminal_cleanup(
        self,
        *,
        sm: RunStateMachine | None,
        status: str,
        claude_runner: ClaudeRunner,
        codex_runner: CodexRunner,
        lock: TargetLock,
        prev_umask: int,
    ) -> None:
        """Mark terminal state, close drivers, release the lock, restore umask — no git.

        Design: §3.1 the run's `finally` must always converge: record the terminal
            run state, close both SDK drivers best-effort, release the per-target
            lock so a later run can acquire it, and restore the umask. It performs
            NO git operations — the directory-copy merge needs none — and must never
            raise so it cannot mask an in-flight CancelledError being re-raised.
        Implementation: transition the state machine to the terminal `status` when
            it is not already terminal (best-effort); await aclose() on each runner
            inside a try/except; release the lock; os.umask(prev_umask). Every step
            is guarded so cleanup never raises.
        Example: after a completed run the lock file is gone and umask is restored.
        """
        if sm is not None:
            try:
                if sm.payload.state not in ("completed", "incomplete", "failed"):
                    sm.transition(status, now=_now())
            except Exception:  # noqa: BLE001 — best-effort terminal checkpoint.
                pass
        for runner in (claude_runner, codex_runner):
            await self._close_runner(runner)
        lock.release()
        os.umask(prev_umask)

    async def _close_runner(self, runner: object) -> None:
        """Best-effort await one SDK driver's aclose, swallowing every error (§3.1).

        Design: §3.1 terminal cleanup closes drivers but must never raise; a
            driver whose aclose fails (or that is already closed) cannot be allowed
            to mask the run's real outcome or a re-raised CancelledError.
        Implementation: if the runner exposes an aclose, await it inside a
            try/except that swallows Exception (NOT BaseException, so a propagating
            CancelledError still unwinds). The fakes and real drivers both expose an
            async aclose() no-op/coroutine.
        Example: await _close_runner(FakeClaudeRunner([])) returns without raising.
        """
        aclose = getattr(runner, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:  # noqa: BLE001 — best-effort driver close.
            pass
