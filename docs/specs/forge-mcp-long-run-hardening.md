# forge-mcp — Long-Run Hardening

**What:** A normative enhancement brief that adds **convergence and durability** to forge-mcp without eroding its context-anxiety north star. It is a companion to `forge-mcp-design.md` (the base design): the base doc wins on anything it already specifies; this doc adds new behavior in its own `§H*` sections, `H-Invariant N`, and `H-Decision N` namespaces so code comments can cite it unambiguously (e.g. `# §H1 verify gate`, `# §H-Inv 3 resume append-only`).

**Status:** Design complete. The base implementation is finished and CI-green (ruff + ruff-format + pyright + docstrings + 138 tests). This brief is the implementation contract for the next increment.

**Audience:** The implementing agent. Precision over prose; every interface sketch is normative and carries the Rule 21 three-section docstring (`Design:` / `Implementation:` / `Example:`).

**Scope (10 enhancements, chosen explicitly):** G1 verification gate · G2/G5 crash-resume + durable state · G3 oscillation detection · G4 observability · G6 transient-error retry · G7 handoff validation · G8 sandbox threat-model · G9 diff-scoped evaluation · G10 lock identity + retention · G11 SDK-native interrupt + `PreToolUse`. **Explicitly out of scope:** cost/billing accounting and any Playwright/browser verification (the latter is a definitional excision — §15 of the base doc).

---

## H0. Thesis, north star, and what does *not* change

### H0.1 The blind spot this brief closes

The base architecture is meticulously engineered to let an agent **work for ~10 hours without context anxiety** (fresh SDK session per phase, file-based handoff, hard agent boundaries, honest cap-hit — §1). It is comparatively **under-engineered to ensure those 10 hours converge on *verified-correct* code and *survive interruption***. Three base-doc decisions concentrate the gap:

- §6.3 / §9.2 step 7 — `status="completed"` rests **entirely** on the Evaluator LLM returning `no_gaps`; there is no objective build/test gate.
- Decision 5 / §21 — **no crash-resume**: a `kill -9`/OOM/reboot at hour 9 discards every completed iteration; the next call re-plans from scratch.
- §9.2 / §6.1 — the only stops are `no_gaps` or the hard caps; **no non-progress detection**, so an oscillating loop burns the entire budget.

The durable substrate to close these already exists: the single-writer atomic `state.json` (Invariant 1), append-only `iteration-N/` dirs (Invariant 4), machine-truth `eval.json`, and file handoffs. Every enhancement below is an **additive module or a localized wiring change** along the base doc's existing seams (thin-conductor `engine.py`, pure-policy modules, I/O `lifecycle`, top-level `gitguard`-style helpers, the `_claude`/`_codex` Protocol seam).

### H0.2 The binding constraint (the north star is preserved)

No enhancement threads **live context** across phases. Concretely:

- The verification gate is an **orchestrator-run subprocess** (no agent context).
- Resume reads **durable files**; resumed phases start with **fresh SDK sessions**.
- Oscillation detection reads **on-disk `eval.json`**; the pivot signal is delivered through the **contract file**.
- Transient retry re-invokes a driver call with a **fresh session per attempt** (the drivers already open a fresh `ClaudeSDKClient`/Codex thread per call — §10.1/§10.2).
- The watchdog only **emits signals**; it never transitions state or fails a run.

`H-Invariant 0 (north star):` every mechanism in this brief either runs deterministically in the orchestrator process or hands off through a file — none widens or persists an agent's in-context working set across a phase boundary.

### H0.3 Architectural stance: extend, do not restructure

> **Alternative considered & rejected.** Reshaping the loop into explicit "sprint" objects or a larger state machine. Rejected: it buys no new behavior, and it would put the 138 green tests and the 8 base invariants at risk. The right move (not the easy one) is minimal blast radius — every change here is additive or local.

---

## H1. Objective verification gate (G1)

### H1.1 Problem & best-practice basis

The base loop's terminal check is `effective_no_gaps = (not eval_for_loop.gaps) and (er.no_gaps or triage_ran)` (`phases.py:215`) — pure LLM judgment. The harness article's verification lever is **testable, executed** success criteria: *"explicit written contracts with testable acceptance criteria"* (principle 4), *"encode evaluation criteria as gradable principles"* (principle 5), and *active verification through environment interaction* (principle 6 — the evaluator runs the thing). The Codex generator already runs in a `workspaceWrite` sandbox with `networkAccess=True` (`_codex.py:73`), so the determinism is **absent by policy, not capability**.

### H1.2 Mechanism

A deterministic command, supplied by the caller, gates `completed`. The command is **executed by the orchestrator**, never delegated to an agent (`H-Invariant 1`).

**New top-level module `verifier.py`** (mirrors `gitguard.py`: subprocess-only, read-only of orchestrator state, imported only by `orchestrator/*`):

```python
@dataclass(frozen=True)
class VerificationOutcome:
    """One deterministic verification run against target_dir (§H1).

    Design: turns "did the build/tests pass" into a typed, on-disk fact so the
        terminal gate rests on a real exit code rather than an LLM's claim
        (§H-Inv 1). Mirrors gitguard's read-only, subprocess-only shape.
    Implementation: frozen dataclass; `passed` is exit_code == 0; output is the
        combined stdout+stderr tail already persisted to iteration-N/verify.txt.
    Example: VerificationOutcome(command="uv run pytest", exit_code=0,
        passed=True, timed_out=False, output_tail="... 138 passed").
    """
    command: str
    exit_code: int | None      # None iff timed_out
    passed: bool
    timed_out: bool
    output_tail: str


def run_verification(target_dir: Path, command: str, *, timeout_seconds: int) -> VerificationOutcome:
    """Run the caller's verification command in target_dir under a timeout.

    Design: §H1 — the gate is deterministic and orchestrator-owned; a non-zero
        exit (or timeout) must not raise (that would corrupt the §6.3 taxonomy)
        — it returns an outcome the loop turns into a synthesized gap.
    Implementation: subprocess.run(shell=True, cwd=target_dir, capture, text,
        timeout); on TimeoutExpired return timed_out=True, exit_code=None; tail
        the combined output to a bounded length for the artifact + evaluator.
    Example: run_verification(Path("/repo"), "npm test", timeout_seconds=600).
    """
```

**Rationale (Rule 4):** `shell=True` is deliberate — the caller's command is an arbitrary pipeline (`"uv run pytest && uv run mypy"`), and the generator already executes arbitrary commands in this same sandbox, so the shell adds no privilege the run doesn't already have. The command runs in `target_dir` (where the code lives), not the iteration dir.

> **Auto-detection rejected (Rule 8).** Inferring the command from `target_dir` (pyproject→pytest, package.json→npm test) is fragile and a silent fallback. If no `verify_command` is supplied the gate is **inert** and behavior is identical to today — fail-loud means the caller opts in explicitly.

### H1.3 Loop wiring (`phases.py`, `run_iteration_loop`)

Two insertions around the existing steps, plus one conjunct. Verify runs **before** evaluate so `verify.txt` enriches the evaluator (article principle 6); the **gap is synthesized after triage** so it is unreachable to classification (identical placement rationale to the git-violation synthesis at `phases.py:199`):

```
2.   iter_generating   — generator.implement(...)              (existing)
2.5  iter_verifying     — if inputs.verify_command:            (NEW, §H1)
        outcome = run_verification(target_dir, cmd, timeout=inputs.verify_timeout_seconds)
        atomic_write_text(iteration_dir / "verify.txt", render(outcome))
        ledger.last_verification = outcome
3.   iter_evaluating    — evaluate(ctx, changed_files=…)        (verify.txt is in cwd; see §H1.4)
4.   iter_triaging      — triage + classify_gaps               (existing)
5.   git-violation synth (existing)
5.5  verify-gap synth   — if cmd and not outcome.passed:        (NEW, §H1)
        eval_for_loop.gaps.append(EvalGap(
            title="Verification command failed",
            severity="high", design_doc_section="§H1",
            current_state=f"`{cmd}` exited {outcome.exit_code} (timed_out={outcome.timed_out})",
            expected_state="verification command exits 0",
            suggested_fix="make the verification command pass; see iteration-N/verify.txt"))
6.   iter_done          (existing)
7.   terminal check     — effective_no_gaps AND verify_passed   (CHANGED, §H1)
        where verify_passed = inputs.verify_command is None or bool(ledger.last_verification and ledger.last_verification.passed)  # fail-closed if missing
```

**New `StateLiteral` member `iter_verifying`** (added to both `state.py` and the base doc's §7 list; `status._PHASE_LABEL` gains `"iter_verifying": "verify"`). This is the only new live phase (and is the *positive* mirror of the excised `iter_pw_probing` — it adds deterministic verification rather than browser probing).

### H1.4 Feeding `verify.txt` to the evaluator

`evaluate` runs with `cwd=iteration_dir` (`evaluator.py:68`), so `verify.txt` is already in the evaluator's working directory. `evaluator_system.md` gains one line: *"If `verify.txt` is present, its failures are authoritative — surface each as a gap."* This converts the deterministic gate into **grounded** evaluator gaps (real test failures cited), not just a boolean.

### H1.5 Surface changes

- `RunForgeInput`: `verify_command: str | None = None`; `verify_timeout_seconds: Annotated[int, Field(ge=1, le=24*60*60)] = 1800`.
- `RunResult`: `verification: VerificationSummary | None` (`{command, exit_code, passed, timed_out}`; the *last* iteration's outcome).
- `IterationArtifacts`: `verify_path: str | None = None` (set by `result._artifact_index` when `iteration-N/verify.txt` exists).
- `RunLedger`: `last_verification: VerificationOutcome | None = None`.

---

## H2. Crash-resume + durable state (G2 / G5)

### H2.1 Problem & best-practice basis

Decision 5 / §21 defer resume; §7 deliberately drops `fsync` from `write_state`. Over a 10-hour budget, an interrupted run discarding all completed iterations is the **costliest** failure, and the un-fsync'd `state.json` is least trustworthy for exactly the infra-level crashes (power loss, OOM, hypervisor reset) a long run is most exposed to. The article's durability story is artifact-based (principles 1 & 7: context reset works *because* state lives in files; "version control for recovery"). context7 confirms the v1 "no-resume" rationale has **gone stale**: the Claude SDK now ships `session_store` + `resume` (`/anthropics/claude-agent-sdk-python`) and Codex `thread/start` returns a durable resumable `thread.id` (`/openai/codex`).

### H2.2 Granularity: iteration-boundary resume (chosen)

> **Alternative considered & rejected.** Full mid-phase SDK-session resume (record per-phase `session_id`/`thread.id`, resume *inside* the interrupted phase). Rejected for v1: it couples to still-maturing SDK resume semantics and must reconcile partial generator writes to `target_dir`, for the marginal benefit of saving at most one iteration. Iteration-boundary resume leverages the **existing** durable artifacts, is low-risk, and is independently testable. (Recording session IDs for a future mid-phase layer is noted in §H18.)

Resume re-enters at `last_completed_iteration + 1`, reusing the durable `contract.md` that the prior run's `write_remediation` already wrote, and **redoing at most the interrupted iteration**.

### H2.3 Durable state (`state.py`, closes G5)

```python
def write_state(path: Path, state: RunState) -> None:
    """Atomically and DURABLY persist RunState (§7, §H2 closing G5).

    Design: resume (§H2) and crash-forensics (§8.2) both require the resume
        point to survive power-loss/OOM, not just process exit — so the write
        is fsync'd (file then directory) before/after os.replace. The base
        doc's "no fsync for speed" is superseded: transitions happen a handful
        of times per multi-minute phase, so the durability cost is negligible.
    Implementation: write temp, flush + os.fsync(fd), chmod 0600, os.replace,
        then best-effort fsync the parent directory (POSIX) so the rename is
        durable. Non-POSIX skips the dir fsync.
    Example: write_state(Path("state.json"), RunState(state="iter_done", ...)).
    """
```

`RunState` gains `last_completed_iteration: int = Field(default=0, ge=0)`. Because `RunStateMachine.transition` preserves prior fields via `model_dump` (§8.2), setting it once on `iter_done` (`sm.transition("iter_done", iteration=n, last_completed_iteration=n)`) carries it forward across `iter_remediating` and the next `iter_generating` until the next `iter_done` overwrites it. This is the fsync-durable resume anchor.

### H2.4 Resume discovery (`orchestrator/resume.py`, new)

Policy + read-only state recovery (mirrors `result.py`'s "reads the run dir, no SDK"):

```python
@dataclass(frozen=True)
class ResumePoint:
    """A located resumable run and its restart anchor (§H2).

    Design: bundles exactly what the engine needs to re-enter the loop without
        re-planning — the existing run dir/id and the fsync-durable last
        completed iteration.
    Implementation: frozen; start_iteration is last_completed_iteration + 1.
    Example: ResumePoint(run_id="abcd1234", run_dir=Path(...),
        last_completed_iteration=7, start_iteration=8).
    """
    run_id: str
    run_dir: Path
    last_completed_iteration: int
    start_iteration: int


def find_resumable_run(harness_dir: Path) -> ResumePoint | None:
    """Find the most recent interrupted run under harness_dir, or None.

    Design: §H2 resume is explicit; this only *locates* a candidate — a run
        whose state.json is non-terminal (not completed/incomplete/failed),
        i.e. one that died mid-flight. The newest such run wins.
    Implementation: glob <harness>/<run-id>/state.json, read_state each, keep
        those whose `state` is non-terminal, pick the max by started_at; derive
        start_iteration from last_completed_iteration + 1.
    Example: rp = find_resumable_run(Path("/repo/.harness")).
    """


def prepare_resume(run_dir: Path, point: ResumePoint) -> None:
    """Archive the partial interrupted iteration so the redo is append-only.

    Design: §H-Inv 3 forbids rewriting a completed iteration dir; the single
        in-flight iteration (start_iteration) that the crash left partial is
        archived for forensics, never overwritten, then recreated fresh.
    Implementation: if iteration-<start>/ exists, rename it to
        iteration-<start>.interrupted-<utc-ts>/; recreate iteration-<start>/
        (0700). Leave iterations 1..last_completed untouched.
    Example: prepare_resume(run_dir, point)  # iteration-8/ -> iteration-8.interrupted-...
    """
```

### H2.5 Lock & engine wiring

- `RunForgeInput`: `resume: bool = False` (explicit; **never auto-resume** — a fresh-run caller must not be surprised).
- `TargetLock.acquire(adopt_run_id: str | None = None)` — when resuming, the lock **adopts** the located run's id instead of minting a fresh one, so the lockfile, run dir, `state.json`, and `RunResult` keep referring to the *same* run (continuity; refines §6.5 without breaking it). The crashed run's lock is stale (dead PID, and PID-reuse-safe via §H9) so it is stolen cleanly.
- `prepare_run` (preflight): when `inputs.resume`, call `find_resumable_run(harness_dir)` **before** acquiring; if None → `McpError(SERVER_ERROR, "no resumable run for target")` (pre-run failure, §6.3); else `lock.acquire(adopt_run_id=point.run_id)` and carry `point` on `PreparedRun`.
- `Orchestrator.run`: when resuming, **skip** `run_plan_phase`, call `prepare_resume`, and pass `start_iteration=point.start_iteration` to `run_iteration_loop`. `create_run_dir` is idempotent (`exist_ok=True`, `artifacts.py:62`) so pointing at the existing dir is safe.
- `run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration: int = 1)` — the existing `range(1, max+1)` becomes `range(start_iteration, max+1)`; the "contract for the first iteration" bootstrap generalizes: `contract-<start_iteration>` must exist (it does — prior remediation wrote it, or it is `plan.md` for `start=1`); if it is missing/degenerate, re-author via `write_remediation` with `eval_result` reconstructed from `iteration-<L>/eval.json` (the eval that drove that contract; ties to §H6).
- **Resume `base_git`:** the engine reloads the baseline from the original `inputs/git-state.txt` (do **not** re-capture at resume time) and passes it to `run_iteration_loop` — so a git mutation made in a pre-crash iteration is still flagged by the first post-resume violation check, rather than being baked into a fresh baseline. (If `inputs/git-state.txt` is absent — target was not a git repo — `base_git` is `None`, identical to a fresh run.)
- `RunResult`: `resumed_from_iteration: int | None = None` (= `point.last_completed_iteration` on a resumed run, else None).
- Oscillation history (§H3) on resume is **reconstructed from the durable `eval.json` files** of completed iterations, so non-progress detection survives the resume.

`H-Invariant 2 (durable resume anchor):` resume re-enters only from a fsync-durable `iter_done` boundary read out of `state.json`; the in-flight iteration is redone, never assumed complete.
`H-Invariant 3 (append-only under resume):` resume continues the same `run_id`; iterations `1..last_completed` are immutable; the single partial iteration is archived to `iteration-N.interrupted-<ts>/`, never overwritten. This **refines** Invariant 4 (which mints a new `run_id` for a *re-run*) — resume is a *continuation*, not a re-run.

---

## H3. Oscillation / non-progress detection (G3)

### H3.1 Problem & best-practice basis

Stops are only `no_gaps` or the hard caps (`phases.py:216-222`). A loop that fixes A→breaks B→fixes B→breaks A burns all 100 iterations / 24h, then returns `incomplete`. The git-violation synthetic-gap path (§11.3) can even *sustain* such a loop. Article principle 15 ("allow strategic pivoting between iterations") names the local-maximum failure mode and prescribes the fix: detect when the approach isn't working and **allow a complete direction change** rather than incremental refinement; also recognize **plateaus**. This is pure orchestrator policy (no SDK angle); the per-iteration gap sets are already on disk.

### H3.2 Mechanism: pivot-then-break (chosen)

> **Alternatives considered & rejected.** *Circuit-breaker only* (stop early) — throws away the article's "give it a chance to pivot" insight. *Pivot directive only* (no early stop) — a genuinely stuck loop still burns the full budget. The escalating synthesis honors principle 15 **and** caps wasted budget.

**New pure module `orchestrator/convergence.py`** (sibling of `triage`/`caps`; no I/O, unit-testable):

```python
def fingerprint_gaps(gaps: list[EvalGap]) -> frozenset[str]:
    """Reduce a gap set to an order-independent identity (§H3).

    Design: non-progress is "the same gaps keep coming back", so the identity
        must ignore ordering and transient prose. Title is already whitespace-
        canonicalized (models.EvalGap), so (title, severity) is a stable key.
    Implementation: frozenset of f"{gap.title}|{gap.severity}" over gaps; empty
        set for no gaps.
    Example: fingerprint_gaps([g1, g2]) == fingerprint_gaps([g2, g1]).
    """


@dataclass(frozen=True)
class NonProgressSignal:
    """The convergence policy's verdict for one iteration (§H3).

    Design: three-valued so the loop can escalate — none (progressing), pivot
        (stuck for `window`; nudge a direction change), break (still stuck
        after the pivot window; stop honestly).
    Implementation: frozen; `reason` is a human string surfaced via stop_reason.
    Example: NonProgressSignal(kind="break", reason="gap-set unchanged for 4 iters").
    """
    kind: Literal["none", "pivot", "break"]
    reason: str


def detect_non_progress(history: list[frozenset[str]], *, window: int = 2) -> NonProgressSignal:
    """Classify progress from the fingerprint history (§H3, article principle 15).

    Design: deterministic and conservative — "stuck" means the latest non-empty
        fingerprint is identical across the trailing `window` iterations (no net
        gap reduction). One stuck window arms a pivot; a second consecutive
        stuck window (2*window total) trips the break.
    Implementation: ignore until len >= window; compare the trailing `window`
        fingerprints for equality -> pivot; if the trailing 2*window are all
        equal -> break; else none. Equality (not Jaccard) keeps it provably
        testable; a similarity threshold is a documented future tunable.
    Example: detect_non_progress([{a}, {a}, {a}], window=2).kind == "pivot".
    """
```

### H3.3 Loop wiring

After `iter_done` (`phases.py:213`): `ledger.gap_fingerprints.append(fingerprint_gaps(eval_for_loop.gaps))`; `signal = detect_non_progress(ledger.gap_fingerprints, window=NON_PROGRESS_WINDOW)` (`NON_PROGRESS_WINDOW = 2`, a module constant in `convergence.py` feeding the `detect_non_progress(window=2)` default).

- `signal.kind == "break"` → set `ledger.stop_reason = signal.reason`; return `("incomplete", n)` early (flows through the engine's existing incomplete branch → `collect_unresolved_gaps` → `finalizing` → `incomplete`; **no new terminal state**).
- `signal.kind == "pivot"` → set `pivot_armed = True`; the next `write_remediation` is called with `pivot=True`.
- `write_remediation(ctx, *, next_iteration_n, eval_result, pivot: bool = False)` — when `pivot`, the prompt is prefixed with a directive (a constant in `evaluator_remediation` handling, also reflected in `evaluator_remediation.md`): *"Prior iterations repeatedly produced the same gaps. Do NOT refine the current approach — choose a different implementation strategy and state it explicitly in the contract."*

`RunResult.message` is extended by `build_result` when `ledger.stop_reason` is set (e.g. `"forge-mcp run stopped early: non-progress detected (gap-set unchanged for 4 iterations)"`). `RunLedger` gains `gap_fingerprints: list[frozenset[str]]` and `stop_reason: str | None`.

`H-Invariant 4 (honest early stop):` a non-progress break is `finalizing → incomplete` with `unresolved_gaps` populated and a `stop_reason` — never `completed`, never `failed`. It is real work that did not converge (§6.3 incomplete semantics).

---

## H4. Observability over a 10-hour run (G4)

### H4.1 Problem & best-practice basis

`Status.update` reports `ctx.report_progress(progress=self._progress, total=None)` (`status.py:90`) — a monotonic event counter with no denominator — plus one `ctx.info` line and the NDJSON sidelog. Over 10 hours: no progress bar, and a silently-wedged generator turn is indistinguishable from deep work (the only per-phase bound is the *global* `asyncio.wait_for`, `engine.py:179`). Article principle 11 ("instrument for observability"). context7 confirms `await ctx.report_progress(progress, total=None, message=None)` accepts a **`total`** and a **`message`** (`/modelcontextprotocol/python-sdk`).

### H4.2 Real progress denominator

> **Experimental task API rejected for v1.** The MCP SDK's `call_tool_as_task`/`poll_task` is built for long calls but is **experimental**; betting a production harness's core protocol on it is the "sweet" option, not the "right" one. It is documented as forward-looking in §H18.

`Status` tracks the current iteration and reports it as the progress value with `total = max_iterations`:

```python
async def update(self, *, phase, agent, message, kind="phase", iteration=None) -> None:
    """Emit one status event with a real iteration progress bar (§H4).

    Design: max_iterations IS known (1..100), so the iteration dimension gets a
        truthful denominator — superseding the base doc's total=None for the
        phase dimension (Decision 11). progress = current iteration (monotonic,
        flat within an iteration); the NDJSON keeps the fine-grained events.
        The human line is also passed as the MCP `message` so hosts surface it.
    Implementation: track self._iteration (updated when iteration is not None);
        report_progress(progress=float(self._iteration), total=float(max_iters),
        message=line) when both known, else the prior monotonic counter with
        total=None; ctx sink failures stay suppressed; record self._last_update_ts.
    Example: await status.update(phase="iter_generating", agent="generator",
        message="implementing", iteration=3)  # -> progress 3 / total 10.
    """
```

`progress` flat-within-iteration is honest (the bar advances once per iteration); strict monotonicity is not required by the SDK.

### H4.3 Per-phase watchdog + heartbeat

**New I/O module `orchestrator/watchdog.py`** (advisory-only; sibling of `lifecycle`):

```python
async def with_phase_watchdog(
    coro: Awaitable[T], *, status, phase: str, iteration: int,
    heartbeat_interval: float = 60.0, hang_after: float = 300.0,
) -> T:
    """Run a phase coroutine while emitting liveness + a hang flag (§H4).

    Design: distinguishes "stuck" from "deep work" over a 10-hour run WITHOUT
        new failure authority — the global runtime cap (engine.py) remains the
        sole termination authority (H-Inv 5). Heartbeats prove liveness; a
        hang flag (no status activity for hang_after) warns a supervisor.
    Implementation: run coro as a task; a sidecar task loops every
        heartbeat_interval emitting status.update(kind="heartbeat"); if
        now - status.last_update_ts > hang_after, emit one kind="warning"
        ("possible hang") then back off. On coro completion/exception, cancel
        the sidecar and return/propagate. Never raises on its own.
    Example: await with_phase_watchdog(gen.implement(...), status=s,
        phase="iter_generating", iteration=3).
    """
```

`Status` exposes `last_update_ts: float` (set in `update`). The generator turn (the longest, most hang-prone phase) is wrapped: `await with_phase_watchdog(deps.drivers.generator.implement(...), status=deps.status, phase="iter_generating", iteration=n)`. `status._PHASE_LABEL` is unchanged; `kind` gains `"heartbeat"` (NDJSON-only; it does not advance the progress bar).

`H-Invariant 5 (advisory watchdog):` the watchdog only emits status events; it never transitions state, never closes drivers, and never fails a run. Termination authority stays with the global `max_runtime_minutes` cap.

---

## H5. Per-phase transient-error retry (G6)

### H5.1 Problem & best-practice basis

The only in-phase resilience is `with_schema_retry` (one retry on `OutputSchemaError`, `retry.py`). Every *other* error — a transient SDK disconnect, a momentary network timeout — is terminal `failed` for the whole run, discarding all prior iterations (and pre-G2, with no resume). Over 10 hours the probability of ≥1 transient hiccup is high. Article ethos: "identify common failure modes, then design a component to address each."

### H5.2 Mechanism (taxonomy-invisible)

**Extend `orchestrator/retry.py`** with a transient-retry that is invisible to the §6.3 bright line and never swallows cancellation:

```python
async def with_transient_retry(
    call: Callable[[], Awaitable[T]], *,
    is_transient: Callable[[BaseException], bool],
    max_attempts: int = 3, base_delay: float = 1.0,
) -> T:
    """Retry a driver call on transient transport errors, with backoff (§H5).

    Design: a transient blip must not nuke a multi-iteration run. CancelledError
        and the global TimeoutError are NEVER retried — they belong to the §8.5
        cancellation / runtime-cap paths and must propagate untouched
        (H-Inv 6). On exhaustion the last exception propagates into the existing
        handle_failure path (so the error taxonomy is unchanged). Each attempt
        re-invokes `call`, which opens a FRESH SDK session (§10.1/§10.2) — the
        north star is preserved (H-Inv 0).
    Implementation: loop up to max_attempts; re-raise immediately on
        CancelledError; re-raise non-transient exceptions; on a transient one,
        sleep base_delay * 2**(attempt-1) and retry; re-raise after the last.
    Example: er = await with_transient_retry(lambda: with_schema_retry(eval_call),
        is_transient=is_transient_claude).
    """
```

**Predicate ownership.** The seams know their SDK's transient exception types. `_claude.py` and `_codex.py` each export `is_transient_error(exc) -> bool` (connection reset / timeout / broken pipe / SDK transport errors → True; schema/validation/logic → False). `phases.py` (which may import `drivers/*`) passes the right predicate; `retry.py` stays import-pure (it only receives the callable — no `drivers/*` edge, preserving §5.2).

### H5.3 Composition & wiring

Transient-retry wraps the **outermost** driver invocation, so a blip retries the whole call while `OutputSchemaError` is still handled inside by `with_schema_retry`:

- evaluate: `with_transient_retry(lambda: with_schema_retry(_make_eval_call(deps, n)), is_transient=is_transient_claude)`
- triage: `with_transient_retry(lambda: with_schema_retry(_make_triage_call(deps, n, er)), is_transient=is_transient_claude)`
- generator: `with_transient_retry(lambda: deps.drivers.generator.implement(...), is_transient=is_transient_codex)` (inside the `with_phase_watchdog`)
- plan / remediation: wrapped similarly with the Claude predicate.

`H-Invariant 6 (cancellation is never transient):` `with_transient_retry` re-raises `asyncio.CancelledError` (and the run-level `TimeoutError`) immediately and uses a fresh session per attempt; only true transport faults are retried, and exhaustion falls through to the existing `failed` path.

---

## H6. Prose handoff validation (G7)

### H6.1 Problem & best-practice basis

The machine handoffs (`eval.json`/`triage.json`) get pydantic + schema + one retry, but the **prose** handoffs that drive each phase — `plan.md`, `contract.md`, `summary.md` — are checked for **existence only** (the §11.4 off-cwd recovery; the `evaluator.py:144` `contract_path.exists()` check). A truncated or empty-of-meaning `contract.md` propagates silently and burns a full generate→evaluate cycle. Article principle 4 (contracts with testable criteria) wants meaningful contracts; principle 16 warns against over-structuring — so validate **well-formedness**, not a rigid schema.

### H6.2 Mechanism

**New pure module `orchestrator/handoff.py`** (sibling of `triage`):

```python
def validate_contract(text: str) -> list[str]:
    """Return a list of well-formedness problems for a contract (§H6, empty=ok).

    Design: catches the silent-propagation failure (degenerate contract drives a
        wasted iteration) without imposing a machine schema on a deliberately
        prose artifact (article principle 16). Conservative — flags only clearly
        broken contracts.
    Implementation: problems if stripped text is shorter than MIN_CONTRACT_CHARS,
        has no markdown heading, or lacks any actionable/acceptance signal
        (heuristic keyword/heading presence). Returns [] when acceptable.
    Example: validate_contract("") == ["contract is empty"].
    """
```

`validate_plan` / `validate_summary` are lighter (non-empty + a heading).

### H6.3 Wiring (one re-author retry, then synthesize)

After `write_remediation` writes `contract.md`: `problems = validate_contract(text)`; if non-empty → re-call `write_remediation` once; if still non-empty → **synthesize a high-severity gap** (`title="Degenerate remediation contract"`, `design_doc_section="§H6"`) into the next iteration's seed and append a `ledger.warnings` entry. After `run_plan_phase` writes `plan.md`: validate once, re-author once, then **warn and proceed** (a weak plan is caught by iteration-1's evaluation; there is no prior iteration to carry a synthesized gap — the asymmetry is intentional, Rule 4).

---

## H7. Generator sandbox & threat model (G8)

### H7.1 Problem & best-practice basis

The generator runs arbitrary commands for hours under `workspaceWrite` + `networkAccess=True` (`_codex.py:73`) + the SDK default approval handler that auto-accepts command/file ops (§10.2). `writable_roots` bounds *writes* but not *reads/egress*. context7 confirms per-turn `sandbox_policy` with `networkAccess` and a custom approval handler exist (`/openai/codex`).

### H7.2 Mechanism (caller toggle + honest docs)

> **Alternatives considered & rejected.** *Command allow/deny-list* via a custom approval handler — largely **security theater** for an agent that can author and execute a script around any shell denylist, while generating false-positive denials that burn budget. *Deny-network-by-default* — breaks the common "install deps then run tests/`verify_command`" flow unless every caller opts in. The honest, best-practice position: expose the knob, set a pragmatic default, and **document that the real isolation boundary is the deployer's OS/container sandbox**, which no in-process policy can substitute for.

- `RunForgeInput`: `network_access: bool = True` (deps/tests need it).
- `sandbox_policy_for(*, target_dir, iteration_dir, network_access: bool)` — `network_access=network_access` instead of the hardcoded `True`.
- `GeneratorDriver.implement(ctx, *, codex_bin, status_cb, network_access, env=None)` — threads `deps.inputs.network_access` (no `RunContext` change; it is a per-run input, not a per-call value).
- **README + this section** carry the threat model: *the generator executes arbitrary commands in `target_dir`; `network_access` is a convenience knob, not a security boundary; run forge-mcp against a disposable/scratch checkout inside an OS/container sandbox you control.*

`Decision §H7 (G8):` isolation is the deployer's responsibility (OS/container); the harness provides a network toggle and a documented threat model, and deliberately does **not** ship an in-process command denylist (theater) or a deny-by-default that breaks legitimate runs.

---

## H8. Diff-scoped evaluation (G9)

### H8.1 Problem & best-practice basis

The fresh-session design stops *transcript* growth, but `evaluate` reads all of `target_dir` (`add_dirs=[ctx.target_dir, …]`, `evaluator.py:66`) every iteration, so the gatekeeper phase's effective context grows with the codebase — reintroducing context pressure on the one phase that decides "done." `classify_gaps` also substring-searches the whole design doc per citation per triaging iteration (`triage.py:52`).

### H8.2 Mechanism (starting context, not a hard cap)

- `gitguard.py` gains `changed_files(target_dir) -> list[str]`:

```python
def changed_files(target_dir: Path) -> list[str]:
    """Return paths the generator changed (uncommitted), for diff-scoped eval (§H8).

    Design: the generator never commits (Rule 11), so `git status --porcelain`
        is exactly the set of files it touched this run — the natural starting
        focus for the evaluator, keeping the "done" gatekeeper off whole-tree
        context pressure as the codebase grows.
    Implementation: reuse the read-only _run_git porcelain; parse the path
        column (handle rename "A -> B"); return [] outside a git repo / on error.
    Example: changed_files(Path("/repo")) == ["src/app.py", "tests/test_app.py"].
    """
```

- `evaluate(ctx, *, retry=False, changed_files: list[str] | None = None)` keeps full `add_dirs` (the evaluator retains Read tools — diff-scope is **starting context, not a restriction**), and the prompt gains a changed-files manifest: *"The generator changed these files this iteration; review them first, then anything they affect."* `phases.py` computes `changed_files(target_dir)` and passes it.
- `triage.py`: a `_MAX_CITATION_SCAN_CHARS` guard documents and bounds the substring scan; if `canon` exceeds it the check still runs but emits a one-time warning (correctness preserved; pathological cost flagged) — consistent with the base doc's >1 MB design-doc warn philosophy.

---

## H9. Lock identity & run-dir retention (G10)

### H9.1 Problem & best-practice basis

`_is_stale` trusts `psutil.pid_exists(pid)` (`lockfile.py:141`): a crashed run whose PID was recycled to an unrelated live process is **not** detected as stale, locking the target out until the 36h mtime arm. And every run leaves a full `<run-id>/` tree forever (Invariant 4, append-only), including 0600 `run.log` with possibly-sensitive stderr — unbounded sensitive-data accretion.

### H9.2 Mechanism

- **PID-reuse-safe staleness.** The lock payload gains `create_time: float` (`psutil.Process(os.getpid()).create_time()` at `acquire`). `_is_stale` becomes: unreadable → stale; mtime > 36h → stale; `not psutil.pid_exists(pid)` → stale; **else if the live process's `create_time()` ≠ the stored value → stale (recycled PID).** Only a same-pid, same-create_time, within-36h holder is considered live. This makes the 36h arm a backstop, not the primary detector.

```python
def _is_stale(self) -> bool:
    """Decide whether an existing lockfile may be stolen, PID-reuse-safe (§H9).

    Design: pid_exists alone misclassifies a recycled PID as a live holder,
        locking the target out for up to 36h after a crash. Comparing the live
        process create_time to the stored one detects reuse precisely.
    Implementation: unreadable payload -> stale; mtime > STALE_AFTER_SECONDS ->
        stale; pid dead -> stale; pid alive but psutil.Process(pid).create_time()
        != stored create_time -> stale; else live. Missing psutil fields fail
        safe to stale.
    Example: a dead run whose PID was reused returns True (steal allowed).
    """
```

- **Retention.** New `prune_old_runs(harness_dir, *, keep_last: int)` (in `artifacts.py`): list `<run-id>/` dirs, sort by `state.json` `started_at` (fallback mtime), keep the newest `keep_last`, recursively delete the rest. Called at run start (after lock, before phases) so a fresh run reaps its predecessors; **never** prunes the current/resumed run. `keep_last` is `RunConfig.keep_runs` (env `FORGE_KEEP_RUNS`, default 10).

`H-Invariant 7 (lock liveness is identity-checked):` a lock is "live" only for a process whose pid **and** create_time match the payload within the staleness window; PID reuse never blocks a new or resuming run.

---

## H10. SDK-native interrupt + `PreToolUse` hardening (G11)

### H10.1 Graceful interrupt before terminate

context7 confirms Codex exposes `await turn.interrupt()` (emits `turn/completed status:"interrupted"`) and Claude `ClaudeSDKClient` supports interrupt. Today `close_drivers` does `aclose()` (5s) then `terminate()` (`lifecycle.py:31-37`). Insert a graceful interrupt as the first sub-step so a cancelled/timed-out turn can flush partial state before the hard kill:

```python
async def close_drivers(deps: Any) -> None:
    """Gracefully interrupt, then close, then terminate each driver (§8.5, §H10).

    Design: §H10 adds an SDK-native interrupt() before the existing aclose/
        terminate so an in-flight turn flushes partial summary/state, improving
        cancellation forensics. Ordering and the 5s aclose grace are otherwise
        the base doc's §8.5 step 2 — unchanged; final-state-after-lock-release
        (steps 3-4) is untouched.
    Implementation: per active runner — best-effort await wait_for(runner.interrupt(),
        short grace) when present; then wait_for(runner.aclose(), 5); on timeout
        runner.terminate(). All wrapped so cleanup never masks the outcome.
    Example: await close_drivers(deps).
    """
```

`CodexRunner`/`ClaudeRunner` Protocols gain an optional `async def interrupt(self) -> None`. **Codex (verify against the pinned SDK):** interrupt is an `AsyncTurnHandle.interrupt()` method obtained from `thread.turn(...)` (context7 `/openai/codex` api-reference: `TurnHandle` exposes `steer/interrupt/stream/run`), but the current seam instead holds `self._session = await thread.runs.create(developer_instructions=…)` (`_codex.py:209`) — a different surface. Wiring interrupt therefore requires either confirming an equivalent cancel on that run object **or** migrating the seam to the `thread.turn()` `TurnHandle` surface at the pinned commit; if neither is available, `interrupt` is a no-op and `terminate()` alone handles teardown (degrade silently, never fail a run). **Claude:** `ClaudeRunnerImpl.interrupt` best-effort `await self._client.interrupt()` (the client is held in `self._client` during a turn, `_claude.py:234`). `terminate()` remains the hard backstop (Codex `interrupt` notably does **not** kill background terminals).

### H10.2 `PreToolUse` git-mutation deny hook (Rule 11, layer 3)

The Claude phases (planner/evaluator) run with `add_dirs=[target_dir, …]` and could in principle `Bash`-run a git mutation. Add a deterministic third layer (atop the system-prompt forbid and the post-iteration multi-ref diff):

```python
async def _deny_git_mutation_hook(input_data: dict, tool_use_id: str, context: Any) -> dict:
    """PreToolUse hook denying git-mutating Bash commands (Rule 11, §H10).

    Design: a deterministic in-band third layer for the Claude phases, atop the
        system-prompt forbid and the post-iteration gitguard diff. The Codex
        generator is gated by its own sandbox; this protects the planner/
        evaluator Bash surface.
    Implementation: pass through non-Bash tools ({}); for Bash, deny when the
        command matches git commit/branch/tag/push/worktree/rebase/reset --hard,
        returning hookSpecificOutput permissionDecision="deny" with a reason.
    Example: _deny_git_mutation_hook({"tool_name":"Bash","tool_input":
        {"command":"git commit -m x"}}, "id", ctx)  # -> deny.
    """
```

`build_options` gains `hooks: dict | None = None` → `ClaudeAgentOptions(hooks=hooks)`; the planner/evaluator pass `{"PreToolUse": [HookMatcher(matcher="Bash", hooks=[_deny_git_mutation_hook])]}`. **Dependency note:** hooks require `ClaudeSDKClient` (not one-shot `query()`); the seam already uses `ClaudeSDKClient` (`_claude.py:233`), so this is compatible. **Floor note:** confirm `HookMatcher`/`hooks` and `interrupt()` exist at the pinned `claude-agent-sdk >=0.1.20`; if not, bump the floor deliberately and re-pin `uv.lock` (this is the article's "stress-test aging assumptions" discipline — §H19).

---

## H11. Public API & model changes (consolidated)

All additions are **optional scalars / new optional fields**, so the input schema stays **object-root with no top-level combinators** (the §18 pin holds) and existing callers are unaffected.

**`RunForgeInput`** (`models.py`):

```python
verify_command: str | None = None
verify_timeout_seconds: Annotated[int, Field(ge=1, le=24 * 60 * 60)] = 1800
resume: bool = False
network_access: bool = True
```

**`RunResult`** (`models.py`): `verification: VerificationSummary | None = None`, `resumed_from_iteration: int | None = None`. `message` gains the `stop_reason` suffix when set (built in `result.build_result`).

**`IterationArtifacts`**: `verify_path: str | None = None`.

**`RunState`** (`state.py`): `last_completed_iteration: int = Field(default=0, ge=0)`.

**`StateLiteral`** (`state.py` + §7 base list): add `"iter_verifying"`.

**`RunConfig`** (`config.py`): `keep_runs: int = 10` (env `FORGE_KEEP_RUNS`).

**`RunLedger`** (`orchestrator/ledger.py`): `last_verification: VerificationOutcome | None`, `gap_fingerprints: list[frozenset[str]]`, `stop_reason: str | None`.

`VerificationSummary` is a small public pydantic model (`{command: str, exit_code: int | None, passed: bool, timed_out: bool}`); `VerificationOutcome` (ledger-internal, §H1) carries the extra `output_tail`.

---

## H12. Module map & dependency-graph compliance

**New modules:**

| Module | Kind | Mirrors | Section |
|---|---|---|---|
| `verifier.py` (top-level) | I/O subprocess; imported only by `orchestrator/*` | `gitguard.py` | §H1 |
| `orchestrator/convergence.py` | **pure** | `triage.py`/`caps.py` | §H3 |
| `orchestrator/handoff.py` | **pure** | `triage.py` | §H6 |
| `orchestrator/resume.py` | policy + read-only state | `result.py` | §H2 |
| `orchestrator/watchdog.py` | I/O (status only) | `lifecycle.py` | §H4 |

**Changed modules:** `state.py` (fsync + field), `lockfile.py` (create_time + retention + `adopt_run_id`), `orchestrator/retry.py` (`with_transient_retry`), `drivers/_codex.py` (`network_access` param + `interrupt`), `drivers/_claude.py` (`hooks` + `interrupt` + `is_transient_error`), `drivers/_codex.py` (`is_transient_error`), `drivers/evaluator.py` (changed-files prompt) + `drivers/generator.py` (`network_access`), `orchestrator/triage.py` (citation-scan guard), `orchestrator/phases.py` + `orchestrator/engine.py` + `orchestrator/ledger.py` + `orchestrator/result.py` (wiring), `status.py` (total + heartbeat + `last_update_ts`), `models.py`/`config.py`/`preflight.py` (surface), `prompts/evaluator_system.md` + `prompts/evaluator_remediation.md` (verify line + pivot directive), `artifacts.py` (`prune_old_runs`).

**Graph stays acyclic (§5.2 preserved):** `verifier.py` is a top-level leaf imported only by `orchestrator/*` (like `gitguard`); `convergence`/`handoff` are pure (`-> models` only); `resume` reads state (`-> state, models, artifacts`); `watchdog -> status`; `retry` still imports only `errors` (the `is_transient` predicate is **passed in** from `phases`, which is allowed to import `drivers/*` — no new `retry -> drivers` or `drivers -> orchestrator` edge). Only `orchestrator/*` writes `state.json` (resume **reads** via `read_state`).

---

## H13. Enhanced control flow (integrated)

`run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration=1)`:

```
seed contract for start_iteration (plan.md if start==1 else prior remediation; validate §H6)
for n in start_iteration .. max_iterations:
  1  build/ensure iteration-n/                                              (existing)
  2  iter_generating  : watchdog(with_transient_retry(generator.implement(..., network_access)))   §H5/§H4/§H7
  2.5 iter_verifying  : if verify_command -> run_verification -> verify.txt; ledger.last_verification  §H1
  3  iter_evaluating  : with_transient_retry(with_schema_retry(evaluate(..., changed_files)))       §H5/§H8 (reads verify.txt §H1.4)
  4  iter_triaging    : with_transient_retry(with_schema_retry(triage)) -> classify_gaps             (existing)
  5  git-violation synth                                                    (existing)
  5.5 verify-gap synth: if verify_command and not passed -> high-sev gap     §H1
  6  iter_done        : sm.transition(iter_done, iteration=n, last_completed_iteration=n)            §H2
     ledger.gap_fingerprints.append(fingerprint_gaps(eval_for_loop.gaps)); signal = detect_non_progress(...)  §H3
  7  terminal check   : completed iff effective_no_gaps AND verify_passed                            §H1
                        elif signal.break -> stop_reason=..., return ("incomplete", n)               §H3
                        elif n == max -> return ("incomplete", n)
  8  iter_remediating : write_remediation(..., pivot=signal.pivot); validate §H6
```

**Resume entry:** `prepare_run` (resume) → `find_resumable_run` → `lock.acquire(adopt_run_id=…)` → engine skips `run_plan_phase`, calls `prepare_resume`, runs the loop with `start_iteration = last_completed_iteration + 1` and `base_git` reloaded from `inputs/git-state.txt`; oscillation history reconstructed from durable `eval.json`.

---

## H14. Invariant & taxonomy preservation (summary)

The §6.3 bright line and §8.5 ordering are **unchanged**; every new path slots into an existing terminal:

- verify-fail / degenerate-contract / git-violation → a **synthesized gap** (never raised); never-resolved-by-cap → `incomplete`.
- transient-retry exhaustion → the **existing** `failed` path (`handle_failure`); retry is taxonomy-invisible.
- non-progress break → `finalizing → incomplete` (timeout-style), never `cancelling → failed`.
- bad/absent resume target → pre-run `McpError(SERVER_ERROR)` (§6.3).
- §8.5 five-step cancellation ordering preserved; `close_drivers` interrupt is a sub-step *inside* step 2; final state still written after lock release.

New invariants introduced: **H-Inv 0** (north star: no live cross-phase context), **H-Inv 1** (verify gate is orchestrator-run; `completed ⇒ verify_command is None OR exit 0`), **H-Inv 2** (durable resume anchor), **H-Inv 3** (append-only under resume; partials archived), **H-Inv 4** (honest early stop on non-progress), **H-Inv 5** (advisory watchdog), **H-Inv 6** (cancellation never transient; fresh session per retry), **H-Inv 7** (lock liveness identity-checked).

---

## H15. Best-practice grounding (article + context7)

| Enhancement | Article principle | context7 evidence |
|---|---|---|
| H1 verify gate | 4 (testable criteria), 5 (gradable), 6 (active verification) | Codex `workspaceWrite`+`networkAccess` can run tests (`/openai/codex`) |
| H2 resume + fsync | 1 (context reset via durable files), 7 (file handoff) | Claude `session_store`/`resume`; Codex resumable `thread.id` (`/anthropics/claude-agent-sdk-python`, `/openai/codex`) — v1 no-resume rationale gone stale |
| H3 oscillation | 15 (strategic pivoting), plateaus | pure policy (no SDK) |
| H4 observability | 11 (instrument) | `ctx.report_progress(progress, total, message)` (`/modelcontextprotocol/python-sdk`); experimental task API noted, not adopted |
| H5 transient retry | "design for common failure modes" | fresh `ClaudeSDKClient`/Codex thread per call |
| H6 handoff validation | 4 (meaningful contracts), 16 (don't over-structure) | pure policy |
| H7 sandbox | "components encode assumptions; stress-test them" | per-turn `sandboxPolicy.networkAccess`; custom approval handler (`/openai/codex`) — denylist judged theater |
| H10 interrupt + hook | tool restrictions enforce boundaries | `turn.interrupt()`; `HookMatcher`+`PreToolUse` deny (`/openai/codex`, `/anthropics/claude-agent-sdk-python`) |

The base architecture already faithfully implements the article's core (fresh-session + file handoff = context-reset-over-compaction; separate Evaluator from Generator; constrained Planner; honest `incomplete`). This brief closes the complementary gap the article also warns about: verification, durability, and plateau-handling.

---

## H16. Testing strategy (TDD; extends §18)

Write the failing test first for each behavior; all 138 existing tests stay green; Rule 21 docstrings on every new `def`; `scripts/ci.sh` green at the end.

**Pure units (no SDK/disk):**
- `convergence.fingerprint_gaps` — order-independence; differs on changed gap set.
- `convergence.detect_non_progress` — `window` stable → pivot; `2*window` stable → break; a reduction → none (counter resets).
- `handoff.validate_contract` — empty/whitespace/too-short/no-heading → problems; well-formed → `[]`.
- `retry.with_transient_retry` — transient → retry-then-succeed; exhaustion → propagate; **`CancelledError` → immediate re-raise (not retried)**; non-transient → immediate propagate; fresh call per attempt.

**I/O units:**
- `verifier.run_verification` — exit 0 → `passed`; non-zero → not passed; timeout → `timed_out`, `exit_code is None`; `verify.txt` written; output tail bounded.
- `state.write_state` — `os.fsync` invoked (monkeypatch/spy); `last_completed_iteration` persists across transitions.
- `lockfile` — dead PID → stale; **recycled PID (create_time mismatch) → stale**; live matching PID → held; `acquire(adopt_run_id=…)` reuses the id; `prune_old_runs` keeps last N incl. `run.log`.

**Loop / driver (mocked runners):**
- verify gate: `completed` requires verify pass; failed verify synthesizes a high-sev gap; `verify.txt` is in the evaluator cwd; **no `verify_command` → gate inert (today's behavior, regression pin)**.
- oscillation: stuck fingerprints → pivot directive in next contract → early `incomplete` with `stop_reason`; progress resets.
- resume: `find_resumable_run` picks the newest non-terminal run; loop resumes at `L+1`, reuses the durable contract, archives the partial dir, skips planning; degenerate next-contract → re-authored; terminal-only target / no candidate → `McpError`.
- diff-scope: `changed_files` parsed from porcelain; evaluator prompt carries the manifest; full `add_dirs` retained.
- G11: `close_drivers` calls `interrupt()` before `terminate()`; §8.5 order intact; `_deny_git_mutation_hook` denies `git commit`, passes non-Bash.
- sandbox: `network_access=False` flips Codex `SandboxPolicy.network_access`; default `True`.
- observability (G4): `report_progress` carries `total=max_iterations` once the iteration is known; `with_phase_watchdog` emits ≥1 heartbeat and is **advisory** — a slow/raising wrapped coro still returns/propagates unchanged and the watchdog never transitions state or fails the run; a hang flag is emitted after `hang_after` with no status activity.
- handoff (G7): a degenerate `contract.md` → one `write_remediation` re-author → still degenerate → synthesized high-sev gap + warning; a valid contract → no retry.

**Schema/transport pins (extend §18):** new inputs keep input schema object-root, no top-level combinators; `verify_timeout_seconds` exposes `minimum`/`maximum`; `RunResult` with new fields stays object-root; the existing xor/out-of-range/preflight transport tests stay green.

---

## H17. Implementation sequencing

Each lands behind its optional input/default, so partial rollout keeps behavior identical when flags are unset.

1. **Foundations:** `state.py` fsync + `last_completed_iteration`; `models.py`/`config.py` surface fields; schema pins.
2. **H1 verify gate** (highest value): `verifier.py`, `iter_verifying`, gate conjunct, synthesis, evaluator line.
3. **H6 handoff validation** (cheap, protects H2/H3).
4. **H2 resume + lock adopt** (depends on foundations); **H9 lock create_time + retention**.
5. **H3 convergence** (pure) + pivot wiring.
6. **H4 observability** (status total + watchdog) ; **H5 transient retry** (+ seam predicates).
7. **H8 diff-scope**, **H7 sandbox toggle + README**, **H10 interrupt + PreToolUse**.
8. Full `scripts/ci.sh` green; update `CLAUDE.md` to list this doc as a second normative source and note the new `§H*` citation namespace.

---

## H18. Decisions log (forge-mcp long-run hardening)

| § | Decision | Rationale |
|---|---|---|
| §H1 | `completed` requires a caller-provided `verify_command` to exit 0 (when supplied) | Determinism over LLM self-assessment (article 4/5/6); inert when absent (back-compat). |
| §H2 | Iteration-boundary resume (explicit `resume`), not mid-phase SDK resume | Leverages existing durable artifacts; low risk; loses ≤1 iteration. |
| §H2 (G5) | `write_state` is now fsync-durable | Resume + forensics must survive power-loss/OOM; cost negligible vs phase length. |
| §H3 | Pivot-then-break on non-progress | Honors article 15 (pivot) and caps wasted budget. |
| §H4 | `report_progress(total=max_iterations)` for the iteration dimension; advisory watchdog/heartbeat | Real progress bar without faking; supersedes Decision 11 for iterations only. |
| §H5 | Bounded transient retry, taxonomy-invisible, fresh session per attempt | Survives transient blips without muddying §6.3 or the north star. |
| §H6 | Validate prose handoffs (well-formedness + one re-author), not a rigid schema | Catches silent propagation without over-structuring (article 16). |
| §H7 | Network is a caller toggle + documented OS/container boundary; no in-process denylist | Avoids security theater; deny-by-default would break legitimate runs. |
| §H9 | Lock staleness uses pid+create_time; run-dir retention `keep_last` | PID-reuse safety; bounds sensitive-data accretion. |
| §H10 | SDK-native `interrupt()` before hard `terminate()`; `PreToolUse` git-deny hook | Better cancellation forensics; deterministic Rule-11 third layer. |

**Forward-looking (deferred, with stale-assumption notes):** record per-phase Claude `session_id`/Codex `thread.id` for a future *mid-phase* resume layer; adopt the MCP **experimental** task-augmented tool API (`call_tool_as_task`) when it stabilizes, for a pollable long call surviving client idle-timeout; consider a Jaccard-similarity non-progress threshold once exact-equality proves too strict in practice.

---

## H19. Risks & verification notes for the implementer

1. **SDK feature floor.** `HookMatcher`/`hooks`, `ClaudeSDKClient.interrupt()`, and Codex `interrupt()` must exist at the pinned `claude-agent-sdk >=0.1.20` / the pinned `openai-codex @main` commit. Verify; if absent, **bump the floor deliberately** and re-pin `uv.lock` (the article's stress-test-aging-assumptions discipline). The interrupt and hook are best-effort/optional — degrade silently if a seam lacks them, never fail a run.
2. **`report_progress` flat-within-iteration.** progress = iteration is non-strictly-increasing within an iteration; this is honest and SDK-accepted. Do not fake sub-iteration percentages.
3. **`shell=True` in `verifier`.** Deliberate (arbitrary caller pipeline; same privilege the generator already has). Run only in `target_dir`; never interpolate untrusted data beyond the caller's own command string.
4. **Resume + lock continuity.** `adopt_run_id` must keep the lockfile, run dir, `state.json`, and `RunResult` referring to one id. The crashed run's lock must be steal-able (dead PID or create_time mismatch, §H9) — if a *live* same-identity process still holds it, resume correctly returns `LockBusy → SERVER_ERROR`.
5. **Transient predicate scope.** `is_transient_error` must classify only true transport faults; mis-classifying a logic error as transient would retry a doomed call N times. `CancelledError`/`TimeoutError` are never transient (H-Inv 6) — pin this.
6. **Two normative docs now.** Update `CLAUDE.md`'s "Source of truth" to name this companion and the `§H*` / `H-Inv` / `H-Decision` citation namespace; base-doc sections still win on anything they specify.
