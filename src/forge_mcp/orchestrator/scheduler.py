"""DAG waves, bounded concurrency, and failure isolation (§7.1/§7.2/I8/I14).

The scheduler runs the dependency-DAG one wave at a time: ready_plans picks the
plans whose every transitive dependency is already merged, run_wave lazily
creates each ready plan's sandbox from the CURRENT target_dir (post-merge, I14)
and fans them out concurrently under a semaphore with strict failure isolation
(I8 — a raising plan resolves to a `failed` result and never cancels a sibling).
has_cycle and add_conflict_edge keep the depends_on graph acyclic when §7.4
conflict resolution adds a loser->winner ordering edge.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.artifacts import RunLayout
    from forge_mcp.models import Plan, PlanSet

# ---------------------------------------------------------------------------
# DAG analysis (§7.1)
# ---------------------------------------------------------------------------


def _deps_map(planset: PlanSet) -> dict[str, list[str]]:
    """Return a plan-id -> direct-depends_on mapping for *planset*.

    Design: §7.1 the DAG helpers (transitive deps, cycle detection) all walk the
        same direct-edge adjacency, so deriving it once keeps them consistent.
    Implementation: build a dict from each plan's id to its declared depends_on
        list; ids referenced as deps but absent from the planset are simply
        treated as having no further deps when walked.
    Example: _deps_map(ps_with_p2_on_p1) == {'p1': [], 'p2': ['p1']}.
    """
    return {p.id: list(p.depends_on) for p in planset.plans}


def _transitive_deps(plan_id: str, deps: dict[str, list[str]]) -> set[str]:
    """Return every transitive dependency of *plan_id* via DFS over *deps*.

    Design: §7.1 a plan is ready only when ALL its transitive dependencies are
        merged — a plan whose direct deps are merged but a deeper ancestor is not
        must NOT be scheduled, so readiness needs the full transitive closure.
    Implementation: iterative DFS over the direct-edge map, accumulating every
        reached id (excluding the start) into a set; a missing id contributes no
        further edges. Cycles are tolerated via the visited guard (cycle
        detection is has_cycle's job, not this helper's).
    Example: with {'p3': ['p2'], 'p2': ['p1'], 'p1': []},
        _transitive_deps('p3', ...) == {'p1', 'p2'}.
    """
    seen: set[str] = set()
    stack = list(deps.get(plan_id, []))
    while stack:
        dep = stack.pop()
        if dep in seen:
            continue
        seen.add(dep)
        stack.extend(deps.get(dep, []))
    return seen


def ready_plans(planset: PlanSet, merged: set[str], pending: set[str]) -> list[str]:
    """Return pending plan ids whose every transitive dependency is merged (§7.1).

    Design: §7.1 the next wave is exactly the still-pending plans all of whose
        transitive dependencies have already merged into target_dir; resolving
        the full transitive closure (not just direct deps) prevents scheduling a
        plan ahead of a deep, not-yet-merged ancestor.
    Implementation: build the direct-edge map once, then for each plan in
        planset order keep it when it is still pending and its _transitive_deps
        are a subset of merged. Iterating planset preserves a deterministic,
        declaration-stable wave order before the concurrency cap is applied.
    Example: for p2 depends_on p1, ready_plans(ps, merged=set(),
        pending={'p1','p2'}) == ['p1'].
    """
    deps = _deps_map(planset)
    out: list[str] = []
    for plan in planset.plans:
        if plan.id not in pending:
            continue
        if _transitive_deps(plan.id, deps) <= merged:
            out.append(plan.id)
    return out


def has_cycle(planset: PlanSet) -> bool:
    """Return True when the depends_on DAG of *planset* contains a cycle.

    Design: §7.4 conflict resolution adds ordering edges, so the graph must be
        re-checked for acyclicity; a cyclic depends_on graph can never converge
        to a valid wave order and must be rejected.
    Implementation: three-colour DFS — WHITE (unvisited), GREY (on the current
        recursion stack), BLACK (fully explored). Reaching a GREY node along a
        DFS path is a back edge and proves a cycle. Each plan id is used as a
        root once; edges to ids absent from the map are ignored.
    Example: with p1->p2 and p2->p1, has_cycle(ps) is True.
    """
    deps = _deps_map(planset)
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(deps, WHITE)

    def _visit(node: str) -> bool:
        """Return True if a cycle is reachable from *node* via DFS colouring.

        Design: §7.4 a back edge to a GREY (in-progress) node proves the
            depends_on graph cannot be linearised into waves.
        Implementation: mark GREY on entry, recurse into each dependency,
            report a cycle on hitting a GREY neighbour, mark BLACK on exit.
        Example: _visit('p1') returns True when p1 and p2 mutually depend.
        """
        color[node] = GREY
        for dep in deps.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GREY:
                return True
            if color[dep] == WHITE and _visit(dep):
                return True
        color[node] = BLACK
        return False

    return any(color[node] == WHITE and _visit(node) for node in deps)


def add_conflict_edge(planset: PlanSet, *, loser: str, winner: str) -> bool:
    """Add a depends_on(loser -> winner) edge unless it would create a cycle (§7.4).

    Design: §7.4 when two plans conflict, the loser must wait for the winner, so
        the loser gains the winner as a dependency. The edge is added ONLY if the
        graph stays acyclic; if it would close a cycle the existing order is kept
        (the opposing edge is NOT added) so resolution can never deadlock the DAG.
    Implementation: locate the loser plan; if it already depends on winner,
        return True (idempotent, no change). Otherwise tentatively append the
        edge, re-check has_cycle, and roll the append back returning False when a
        cycle appeared; return True when the edge is safely retained.
    Example: with p2 depends_on p1, add_conflict_edge(ps, loser='p1',
        winner='p2') is False (it would cycle) and leaves p1.depends_on empty.
    """
    target = next((p for p in planset.plans if p.id == loser), None)
    if target is None:
        return False
    if winner in target.depends_on:
        return True
    target.depends_on.append(winner)
    if has_cycle(planset):
        target.depends_on.remove(winner)
        return False
    return True


# ---------------------------------------------------------------------------
# Wave execution (§7.2 / I8 / I14)
# ---------------------------------------------------------------------------


def _resolve_plan(plan_id: str, planset: PlanSet | None) -> Plan:
    """Return the Plan for *plan_id*, resolving from *planset* when supplied.

    Design: §7.2 run_wave drives the per-plan loop, which needs the full Plan
        (surface, body, verification_command), but the scheduling/isolation path
        only needs an object carrying `.id`; supporting both keeps the unit test
        a pure isolation test while production threads real plans.
    Implementation: when planset is given, return the matching plan (raising if
        absent so a typo surfaces); when planset is None, return a minimal stand-in
        object exposing just `.id` so the wave can still be scheduled.
    Example: _resolve_plan('p1', None).id == 'p1'.
    """
    if planset is not None:
        for plan in planset.plans:
            if plan.id == plan_id:
                return plan
        raise KeyError(f"plan id not in planset: {plan_id}")

    class _IdOnly:
        """A stand-in plan carrying only the id (used when no planset is given).

        Design: §7.2 the isolation path needs nothing but `.id`; a tiny shim
            avoids forcing callers to build a full Plan for scheduling-only use.
        Implementation: store the id on construction and expose it as `.id`.
        Example: _IdOnly('p1').id == 'p1'.
        """

        def __init__(self, pid: str) -> None:
            """Store *pid* as the stand-in plan's id.

            Design: §7.2 the shim only needs to answer `.id`.
            Implementation: assign pid to self.id.
            Example: _IdOnly('p2').id == 'p2'.
            """
            self.id = pid

    return _IdOnly(plan_id)  # type: ignore[return-value]


def _sandbox_path(plan_id: str, layout: RunLayout | None, target_dir: Path) -> Path:
    """Return the lazy sandbox destination for *plan_id*, outside the copy source.

    Design: §7.2/§11 a plan's sandbox lives under the run layout's plans/<id>/
        directory (inside .harness, which copy_sandbox excludes from the source),
        so copying target_dir never recurses into a sandbox it is creating. When
        no layout is given (scheduling-only use), the sandbox must still sit
        OUTSIDE target_dir to avoid the same self-recursion.
    Implementation: with a layout, return layout.plan_dir(plan_id)/'sandbox';
        without one, return a sibling directory of target_dir keyed by the plan id.
    Example: _sandbox_path('p1', None, Path('/r/t')) is outside '/r/t'.
    """
    if layout is not None:
        return layout.plan_dir(plan_id) / "sandbox"
    return target_dir.parent / f"{target_dir.name}.forge-wave" / plan_id / "sandbox"


async def run_wave(
    plan_ids: list[str],
    *,
    runner_factory,
    target_dir: Path,
    layout: RunLayout | None,
    concurrency: int,
    planset: PlanSet | None = None,
    spec_text: str = "",
    schemas: dict | None = None,
    max_iterations: int = 10,
    base_git_state: str | None = None,
) -> dict[str, PlanLoopResult]:
    """Run one DAG wave concurrently with bounded fan-out and failure isolation.

    Design: §7.2/I14 each ready plan's sandbox is created lazily NOW — copied from
        the CURRENT target_dir, which already contains every merged predecessor —
        never eagerly at run start. §7.1/I8/D2 the plans fan out under an
        asyncio.Semaphore bounded to `concurrency` via gather(return_exceptions=
        True); each plan coroutine self-contains its exceptions and resolves to a
        terminal PlanLoopResult, so a raised error becomes a `failed` result and
        NEVER cancels a sibling. TaskGroup is deliberately avoided (it cancels
        siblings on first error). The §9 backstop diffs git from the same surface
        the engine captured: git_surface=target_dir, base_git_state passed through.
    Implementation: build a Semaphore(concurrency); for each plan id spawn a
        coroutine that, under the semaphore, resolves the Plan, lazily creates its
        sandbox (copy_sandbox + capture_manifest) from target_dir, then awaits the
        module-level run_plan_loop (patchable) with git_surface=target_dir; the
        coroutine wraps its body in try/except Exception (NOT BaseException, so
        CancelledError still propagates) mapping any error to a `failed`
        PlanLoopResult. gather(return_exceptions=True) is belt-and-suspenders.
        Returns dict[plan_id -> PlanLoopResult].
    Example: run_wave(['p1','p2'], ...) with run_plan_loop patched so p1 raises
        yields results['p1'].terminal_state == 'failed' and results['p2'] present.
    """
    from forge_mcp.sandbox import capture_manifest, copy_sandbox

    sem = asyncio.Semaphore(concurrency)
    use_schemas = schemas if schemas is not None else {}

    async def _run_one(plan_id: str) -> PlanLoopResult:
        """Run a single plan's loop under the semaphore with failure isolation.

        Design: §7.2/I8 one plan's whole lifecycle — lazy sandbox creation plus
            the iteration loop — is isolated so any failure resolves to a per-plan
            `failed` result instead of cancelling siblings; the semaphore bounds
            how many such lifecycles run at once (D2).
        Implementation: acquire the semaphore, then inside try/except Exception
            resolve the plan, lazily copy_sandbox + capture_manifest from the
            current target_dir (I14), and await run_plan_loop with
            git_surface=target_dir and the threaded base_git_state; map any
            Exception to a failed PlanLoopResult (CancelledError propagates).
        Example: _run_one('p1') returns a failed PlanLoopResult when its loop raises.
        """
        async with sem:
            try:
                plan = _resolve_plan(plan_id, planset)
                claude_runner, codex_runner = runner_factory()
                sandbox = _sandbox_path(plan_id, layout, target_dir)
                copy_sandbox(target_dir, sandbox)
                capture_manifest(sandbox)
                return await run_plan_loop(
                    # The engine always supplies a real layout in production; the
                    # None case exists only for the scheduling-only unit test,
                    # which monkeypatches run_plan_loop and never touches layout.
                    layout=cast("RunLayout", layout),
                    plan=plan,
                    sandbox=sandbox,
                    spec_text=spec_text,
                    claude_runner=claude_runner,
                    codex_runner=codex_runner,
                    schemas=use_schemas,
                    max_iterations=max_iterations,
                    base_git_state=base_git_state,
                    git_surface=target_dir,
                )
            except Exception as exc:  # noqa: BLE001 — I8: isolate per-plan failure.
                return PlanLoopResult(
                    terminal_state="failed",
                    iterations=0,
                    stop_reason=f"wave plan failed: {type(exc).__name__}: {exc}",
                )

    tasks = [_run_one(pid) for pid in plan_ids]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    results: dict[str, PlanLoopResult] = {}
    for plan_id, outcome in zip(plan_ids, gathered, strict=True):
        if isinstance(outcome, BaseException):
            # Belt-and-suspenders: gather caught an exception the inner guard
            # somehow missed; still resolve to a per-plan failed result (I8).
            results[plan_id] = PlanLoopResult(
                terminal_state="failed",
                iterations=0,
                stop_reason=f"wave plan failed: {type(outcome).__name__}: {outcome}",
            )
        else:
            results[plan_id] = outcome
    return results
