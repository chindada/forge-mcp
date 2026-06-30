# 0003 — Single-Plan, Direct-Edit Harness

## §0. Status & relationship to 0001

This spec **supersedes** the following sections of `0001-forge-mcp-harness.md` and is the
authoritative description of the harness after this change:

- §3.1 (the run) — the wave-structured DAG run is replaced by a single-plan loop.
- §5.1 (Planner) — emits exactly **one** `Plan`, not a `PlanSet`.
- §5.2 (Generator) — edits `target_dir` **in place** under Codex `workspace-write` with
  network access and no human approval; no copy-sandbox.
- §7 (Concurrency & merge) — removed in full: no DAG, no waves, no copy-sandbox, no manifest
  diff, no merge engine, no conflict resolution.
- §9 (Git-commit denial) — git-commit **prevention** is kept; the git-state **backstop**
  (snapshot/diff as a completion gate) is removed.

It **reuses unchanged** from 0001: §6 durability mechanisms N1–N4/D1–D3 (fresh session per
phase, file-based handoff, hard boundaries, honest non-convergence), §8 SDK seams (with the
Codex change in §5 below), §10 skills/`forge check`, §12 run-dir naming, §13 durable atomic
writes, §14 schemas (with the model change in §3), and §16 code conventions.

**Verified-against-code baseline.** Every "current state" reference in this document was
verified against the working tree (file:line). Installed SDK versions at authorship:
`claude-agent-sdk == 0.2.108`, `openai_codex == 0.1.0b2`.

## §1. Premise & north star

**The premise — stated explicitly, because everything else follows from it: the harness runs
exactly ONE plan per run, and the Generator edits the project repository directly.** This is a
deliberate scope reduction away from 0001's multi-plan concurrency. Because there is one plan
and one tree, the entire DAG / wave / per-plan-sandbox / merge / conflict-resolution
subsystem has no reason to exist and is deleted.

A run is now a single async lifecycle:

> acquire lock → freeze design into `spec.md` → **plan once** → **iterate one plan loop
> directly on `target_dir`** (generate → verify → evaluate → triage → converge, applying
> design-fault amendments in-loop) until the plan is `done` or stops honestly → finalize an
> honest `RunResult`.

The Evaluator (a separate Claude session) plus the optional verification command are the only
completion gates. Per the project owner's direction, **git-state does not gate completion**
("if the evaluator can pass, it passes"); the Generator simply leaves its edits uncommitted in
`target_dir` for the human to review and commit with their own git.

Consistent with that, `verification_command` is scoped to **implementation correctness over the
uncommitted tree** (build / codegen / format / lint / test). A clean-tree or committed-baseline
conjunct — `test -z "$(git status --porcelain)"`, `git diff --exit-code`, `git diff --quiet` — is
**out of contract**: the tree is uncommitted by design, so such a gate can never pass in-loop and
would burn the whole iteration cap (it measures "not yet committed", never "correct"). The planner
contract (`planner_system.md` Rule 7) forbids emitting one and instructs the Planner to *decompose*
a spec acceptance block (keep the correctness conjuncts, drop the committed-baseline one);
`run_planner` additionally scopes any inline clean-tree gate that slips through out of the command
and records the drop in `run.log`. Committed-baseline acceptance (e.g. a "generate produces no diff"
codegen-idempotency check) is the project's own **post-commit CI** responsibility, run on a clean
checkout where a committed baseline actually exists — not an in-loop completion gate.

**Non-goals (unchanged intent, now structurally enforced):** no concurrency, no DAG, no
cross-plan conflict handling, no copy-isolation, no git mutation by any stage, no resume.

## §2. Target architecture

```
BEFORE (0001)                              AFTER (this spec)
─────────────                              ─────────────────
run                                        run
 ├ lock                                     ├ lock
 ├ freeze spec                              ├ freeze spec
 ├ PLAN → PlanSet[N] (+ depends_on DAG)     ├ PLAN → one Plan
 ├ while pending:                           ├ run_plan_loop(plan, target_dir)   ← direct edit
 │   ready_plans(DAG)[:CAP]                 │    generate (Codex workspace-write, network on)
 │   run_wave(copy_sandbox ×N, semaphore)   │    verify (in target_dir)
 │     per-plan loop in sandbox copy        │    evaluate (Claude, target_dir vs spec)
 │   detect_conflicts / WriterMap           │    triage → amend spec IN-LOOP → continue
 │   apply_merge(target_dir)                │    converge / cap / honest stop
 │   amend (re-queue all merged)            │
 ├ run-level verify (post-merge)            ├ finalize (verified = done ∧ command passed)
 └ finalize                                 └ cleanup (no git ops)
```

The `Orchestrator.run` conductor shrinks to: `lock → init layout → planning → executing(one
loop) → finalizing`. There is no `scheduling`, `merging`, `amending`, or `verifying`
run-level phase, no `_RunState` cross-wave bookkeeping, and no run-level verification pass.

## §3. Data model (`models.py`)

`Plan` collapses to the minimum a single unit of work needs; `PlanSet` is deleted.

| Field | Disposition | Reason |
|---|---|---|
| `Plan.id` | **drop** | one plan; artifacts flatten to `iteration-<n>/` (§10) |
| `Plan.depends_on` | **drop** | the DAG substrate (`scheduler._deps_map`); no DAG |
| `Plan.file_scope` | **drop** | only drove §7.4 conflict-winner ranking; no conflicts |
| `Plan.surface` | **keep** | still selects the Generator capability preface |
| `Plan.verification_command` | **keep** | the per-iteration completion gate (§4) |
| `Plan.body` | **keep** | the contract handed to the Generator |
| `PlanSet` (whole model) | **delete** | one plan, no collection |
| `PlanSet.run_verification_command` | **delete** | the run-level/post-merge gate disappears with the merge; the single `Plan.verification_command` is the only gate (§4, §8) |

New `Plan`: `{ surface: Literal["backend","frontend"], verification_command: str | None,
body: str }`, `extra="forbid"`. The Planner's structured output schema becomes
`Plan.model_json_schema()` (was `PlanSet.model_json_schema()` at `engine.py:289`).

**Output-contract coupling (`extra="forbid"`):** dropping fields is a breaking change to the
LLM output contract, so the planner prompt's JSON skeleton MUST be regenerated from the new
schema in the same change (§12), or `run_planner` raises `ValidationError` at `planner.py:44`.

## §4. The iteration loop (`phases.py` → `run_plan_loop`)

The loop is the surviving heart of the harness, now operating on `target_dir` directly.

**New signature** (was `(*, layout, plan, sandbox, spec_text, claude_runner, codex_runner,
schemas, max_iterations, base_git_state, git_surface=None)`):

```
run_plan_loop(*, layout, plan, target_dir, spec_text, spec_fingerprint,
              claude_runner, codex_runner, schemas, max_iterations) -> PlanLoopResult
```

Removed params: `sandbox` (→ `target_dir`), `base_git_state`, `git_surface` (git backstop
gone). Added: `spec_fingerprint` (in-loop amendment advances it).

**Per-iteration steps** (n = 1..max_iterations):

1. **generate** — `run_generator(codex_runner, contract_text, target_dir, surface, …)`. Codex
   edits files in `target_dir` (§5). Iteration 1's contract is `plan.body`; later iterations
   use a remediation contract — a Claude-authored, repo-grounded plan that targets only the
   still-open code-bug gaps (`run_remediation`, §6; triage-filtered; + a NUDGE note when
   signalled), not a restatement of the full plan body.
2. **verify** — when `plan.verification_command` is set, `run_verification(command,
   target_dir)`; cache the outcome; write `verify.txt`.
3. **evaluate** — `run_evaluator(claude_runner, spec_text=<current>, cwd=target_dir, …)`
   against the **current** (possibly amended) spec; write `eval.json`.
4. **triage** — only when the eval found gaps; write `triage.json`.
5. **synthesize blocking gaps** — `_synthesize_blocking_gaps` keeps **only** the §6.5
   verify-failure gap. The §9 git gap is **removed** (no `git_diff` input).
6. **fingerprint** — over `eval.gaps ∪ synthesized`; append to the gap history; write
   `gap_fingerprint.json`.
7. **record completed iteration** in the plan state.
8. **amendment? (in-loop)** — if `_validated_amendment_row(triages, spec_text)` is non-None
   (a cited, design-fault row carrying a `proposed_amendment`):
   - `outcome = apply_amendments(layout, spec_text=spec_text,
     spec_fingerprint=spec_fingerprint, proposed=[row], now=…)` — durably rewrites `spec.md`.
   - **Rebind** `spec_text = outcome.new_spec`, `spec_fingerprint = outcome.new_fingerprint`
     so the next iteration's evaluator/triage run against the amended spec.
   - Append `outcome.churn_fingerprint` to the amendment history; if
     `detect_non_progress(amendment_history) == "EARLY_STOP"` → return `incomplete`
     ("amendment thrash").
   - Otherwise honor the cap (if `n == max_iterations` → `incomplete` "iteration cap") and
     **continue** to the next iteration (the gap cannot close until the generator builds to
     the amended spec).
9. **completion gate** — `effective_no_gaps ∧ verify_passed` → `done`. The former
   `no_git_violation` and `no_pending_amend` conjuncts are **removed** (git is not a gate;
   amendments are resolved in step 8).
10. **non-progress (gap history)** — `EARLY_STOP` → `incomplete`; `NUDGE` → set `nudge_next`.
11. **cap** — `n == max_iterations` → `incomplete` ("iteration cap").
12. **remediate** — fall through to iteration n+1.

**Terminal states reduce to `{done, incomplete, failed}`.** `awaiting_amendment` is removed
(it was the wave-boundary hand-off; amendments are now applied in-loop). `PlanLoopResult`
drops the `change_set` and `proposed_amendment` fields.

**Termination guarantee.** The iteration cap is the hard bound — the loop cannot exceed
`max_iterations` regardless of amendment activity. The amendment-churn detector
(`detect_non_progress`, which needs ≥ 4 equal fingerprints) is an early-stop optimization that
may be inert at low caps; the cap still guarantees termination. This is stated so the absence
of a run-level churn detector is not read as a non-termination risk.

## §5. The Generator runtime (`drivers/_codex.py`, `drivers/generator.py`)

The Generator edits `target_dir` directly, with internet access, fully autonomously, but
**bounded to the repo** (no full-machine access). Per the Codex docs (verified via Context7):
`workspace-write` permits reading all files and writing within `cwd` + writable roots;
`ApprovalMode.deny_all` is the autonomous mode — the pinned SDK maps it to the never-ask
policy `AskForApprovalValue.never`, so it suppresses all approval prompts and fails closed on
anything the sandbox forbids (there is **no** `ApprovalMode.never` member; `deny_all` IS that
behavior — verified in `.venv/.../openai_codex/_approval_mode.py`); network access for
`workspace-write` is **not** an SDK keyword and must come from Codex config
(`[sandbox_workspace_write] network_access = true`), which the SDK accepts as an inline
`thread_start(config=…)` override — the `config` param is typed `JsonObject | None` (an
arbitrary dict; TS parity: `sandbox_workspace_write: { network_access: true }`).

**Corrected `CodexDriver._generate_impl` thread start.** The only change vs the current
`_codex.py:383-393` is `Sandbox.full_access`→`Sandbox.workspace_write` plus the network config;
`ApprovalMode.deny_all` is correct and **stays**:

```python
thread = await codex_ctx.thread_start(
    sandbox=Sandbox.workspace_write,                 # was full_access — bound to the repo
    approval_mode=ApprovalMode.deny_all,             # unchanged — maps to the never-ask policy, fails closed
    cwd=str(config.cwd) if config.cwd is not None else None,        # = target_dir (None-guard unchanged)
    config={"sandbox_workspace_write": {"network_access": True}},   # internet ON
)
turn_handle = await thread.turn(
    TextInput(text=instructions),
    cwd=str(config.cwd) if config.cwd is not None else None,
    approval_mode=ApprovalMode.deny_all,
)
```

Implementation note: the `config` param is typed `JsonObject | None` (an arbitrary dict — there
is no `ThreadConfig` schema validating it in `openai_codex == 0.1.0b2`), so the inline
`sandbox_workspace_write` override is passed through as-is. Should a runtime ever not honor the
inline key, the same setting is supplied via a managed `CODEX_HOME/config.toml` threaded through
`build_codex_config(env={"CODEX_HOME": …})`; the inline override is the primary mechanism.

`drivers/generator.py::run_generator` drops the `sandbox: Path` parameter in favor of
`target_dir: Path`, and `build_codex_config(codex_bin=codex_bin(), cwd=target_dir)` (the
signature is keyword-only) roots Codex at the repo. Note `tests/test_sdk_contract.py:39-40` are
SDK *symbol-existence* probes (`assert hasattr(Sandbox, "full_access")` /
`hasattr(ApprovalMode, "deny_all")`, in `test_codex_symbols_exist`), **not** behavioral
assertions — add `hasattr(Sandbox, "workspace_write")` there. The driver's actual
`workspace_write` + `deny_all` choice is pinned behaviorally in `tests/test_codex_seam.py`, which
has no sandbox/approval assertions today and gains them.

**Git-commit prevention on the Codex side** remains best-effort: the `generator_system.md`
rule forbidding git mutations stays, and `ApprovalMode.deny_all` plus `workspace-write` give no
human to approve an escalation. The Codex SDK exposes no public Python-callback PreToolUse seam
(only config/wire-level command hooks, not a registrable deny-callback like Claude's
`HookMatcher`), so a git-deny callback is not available; this is the same posture as 0001 and is
acceptable because git-state does not gate completion (§7).

## §6. The Claude stages (`drivers/planner.py`, `drivers/evaluator.py`, `drivers/remediator.py`)

The Planner and Evaluator are **unchanged in permission posture**: `build_options` still uses
`permission_mode="bypassPermissions"` with `git_deny_hooks()` (a PreToolUse Bash matcher that
denies git-mutating verbs). Per Context7, PreToolUse hooks are evaluated *after*
`permission_mode`, so the autonomy-plus-guardrail composition is the SDK-documented pattern.

**Wording correction (accuracy, not behavior):** these stages are **"git-mutation-denied,"
not "read-only."** Under `bypassPermissions` the git-deny hook only blocks git verbs on Bash;
`Write`/`Edit`/non-git Bash are not blocked. The harness does not rely on them being
filesystem-read-only — the Planner produces a `Plan` and the Evaluator produces structured
`EvalResult`/`TriageResult`; neither needs to write the repo, and the design does not claim
they cannot. (Making them filesystem-read-only via
`disallowed_tools=("Write","Edit","NotebookEdit")` is an explicit **non-goal** of this change,
by decision — the git-deny hook is the chosen guardrail.)

The only Planner change is the schema value the caller passes — `Plan.model_json_schema()` (was
`PlanSet.model_json_schema()`) — and `run_planner`'s return type/construction becoming `Plan`
(was `PlanSet` at `planner.py:21,44`); `planner.py` keeps `output_format=envelope(plan_schema)`
unchanged. `run_evaluator` drops the now-unused `sandbox: Path` parameter (its signature takes
both `sandbox` and `cwd` today; `run_triage` already takes only `cwd`, so it is unchanged); the
two call sites — `run_evaluator` at `phases.py:274-281` and `run_triage` at `phases.py:289-296`,
each passing `cwd=sandbox` today — pass `cwd=target_dir`.

**The remediation stage (`run_remediation`, `drivers/remediator.py`)** is a third Claude stage,
invoked by the iteration loop (§4 step 1) when an iteration does not complete: it authors the
*next* iteration's contract as a focused, repo-grounded plan over the still-open gaps, rather
than re-emitting the plan body with a gaps footnote. The gaps it receives are **triage-filtered**
to the effective code bugs (`effective_code_bug_titles`): a validly-demoted design fault is the
spec's problem, not a code fix, so it is excluded from what remediation tells the Generator to
close — and when it carries a cited `proposed_amendment`, the §4 step 8 amendment resolves it
(rather than the Generator). That triage-filtered set stays identical to the completion gate's
code-bug set; `last_gaps` stays raw for the engine's honest unresolved-gaps report. It shares the Planner/Evaluator permission
posture — `bypassPermissions` + `git_deny_hooks()`, rooted at `target_dir` so it reads the repo
but cannot commit. Critically it returns **structured** output —
`output_format=envelope(RemediationResult.model_json_schema())` and
`RemediationResult(**structured_output).contract` — **not** the raw turn `result.text`. A
remediation turn inspects the repo across many tool calls and narrates between them;
`result.text` concatenates that interstitial narration, which would leak into the contract handed
to the Generator. The structured `contract` field isolates the clean plan, exactly as `Plan.body`
does for the Planner. The composed prompt carries the frozen spec, the project path, the
still-open gaps (`title (severity): suggested_fix`), any synthesized verify blockers, the
original plan as reference-only, and a NUDGE note when the convergence detector signals a stall.

## §7. Git & safety model

| Concern | Decision | Mechanism |
|---|---|---|
| Direct edit, bounded to repo | yes | Codex `workspace-write`, `cwd=target_dir`, `approval_mode=deny_all` (the never-ask policy; writes outside repo fail closed) |
| Internet access | yes | inline `config={"sandbox_workspace_write": {"network_access": True}}` |
| No git commits | prevent (best-effort) | Claude: `git_deny_hooks`; Codex: prompt rule + `deny_all` approvals |
| Git-state as completion gate | **removed** | delete the snapshot/diff backstop and the §9 synthesized gap |

`gitguard.py` reduces to the deny matcher: **remove** `_run_git`, `capture_state`,
`diff_state` (and the now-unused `import subprocess` / `from pathlib import Path`); **keep**
`git_deny_matches`, `_MUTATING_VERBS`, `_DENY_PATTERN`, and `import re`. Update the module
docstring to "deny matcher" only.

**Removed git backstop, in full:** `capture_state`/`diff_state` are LIVE today
(`engine.py:421`, `phases.py:267-268`), so removing them is a coordinated live-code deletion:
the `phases.py` git steps (`base_git_state`/`git_surface` params, `capture_state`/`diff_state`
calls, the `_synthesize_blocking_gaps` git branch, `_GIT_SENTINEL_SECTION`, the
`layout.git_violation` write), plus `engine.py:421` capture and `:435` pass-through, plus the
`scheduler.py:228,290-291` threading (deleted with the file). **Self-diff trap avoided:** a
naive "set `git_surface=target_dir`" would make the backstop diff the tree against itself and
flag every direct edit as a violation — removal is the correct resolution, not rewiring.

## §8. Run-level engine (`orchestrator/engine.py`)

**New `Orchestrator.run` flow:** `umask → lock → create_run_dir → init_run_layout →
RunStateMachine → planning → run_planner(→ Plan) → _persist_plan → executing →
run_plan_loop(plan, target_dir) → finalizing → _finalize → cleanup`. The whole body stays
wrapped so the `finally` runs `_terminal_cleanup` (mark terminal state, close both drivers
best-effort, release lock, restore umask — **no git ops**) and re-raises an injected
`CancelledError`. `_run_level_verify` and the run-level verification pass are **removed**
(§4 makes the in-loop verify the gate).

**New import:** `from forge_mcp.orchestrator.phases import run_plan_loop`, and move
`PlanLoopResult` out of `TYPE_CHECKING` (referenced at runtime). The single Plan is obtained
directly from `run_planner` (now returns one `Plan`); there is no `planset.plans[0]` and no
cycle guard.

**Methods removed** (all verified present): `_run_waves` (:376), `_merge_wave` (:463),
`_resolve_conflicts` (:538), `_amend_wave` (:589), `_finalize_unschedulable` (:763),
`_sandbox_of` (:102), `_discard_sandbox` (:115), `_make_is_dependent` (:130),
`_deleted_dir_paths` (:156), `_conflicting_paths` (:169), `_write_merge_record` (:195); the
`has_cycle` guard (:299). The current 10-field `_RunState` loses `spec_text`, `spec_fingerprint`,
`writer_map`, `conflict_history`, `amendment_history` (read only inside the removed wave/amend
methods), plus `merged` and `pending` (read in today's `_finalize` too, but the rewritten
single-plan `_finalize` no longer needs them), and renames `reports`→`report` — reducing to
`{ iterations, stop_reason, report }` (the single `PlanLoopResult`). `_persist_planset` becomes
`_persist_plan` (writes `plan.json` + `plan.md`).

**`_finalize` (simplified, same honesty):** with one plan, `status = "completed"` iff the
plan is `done` (no `stop_reason`), else `"incomplete"` with a `stop_reason` synthesized from
the plan's own reason; `"failed"` only on an orchestrator-internal error.
**`verified = (terminal_state == "done") and (plan.verification_command is not None)`** — an
honest `False` when no command was declared. `non_completed` carries the single plan's freshest
gap set when it did not complete.

**Imports to prune after the method deletions** (or Ruff F401 fails CI): `shutil`;
`capture_state` (gitguard); `detect_non_progress`, `fingerprint` (convergence); `apply_amendments`;
`run_verification` (verifier — its sole use is the removed `_run_level_verify`); all six
scheduler symbols; all six sandbox symbols (`Change`, `Conflict`, `WriterMap`, `apply_merge`,
`detect_conflicts`, `resolve_conflict_winner`); from the `engine.py:30` models import drop
**`PlanSet`** only (replaced by `Plan` for the new `run_planner` return / `plan_schema`), while
**keeping `RunResult`, `EvalResult`, and `TriageResult`** — the latter two are NOT F401-removable:
they are still consumed at runtime to build the `schemas` dict passed into `run_plan_loop`
(`engine.py:160-161`); and separately drop the
`TYPE_CHECKING` `GapTriage` import at `engine.py:57` (its sole use is `engine.py:625` in the
deleted `_amend_wave`). **Keep** `write_json`, `light_replace`. Rewrite the
stale module/`Orchestrator`/`run` docstrings (`:1-11`, `:222-234`, `:249-269`) to the
single-plan flow.

## §9. State machine & per-plan state

**`statemachine.py`** — collapse `RunState` and `_LEGAL_EDGES` to:

```
init → planning → executing → finalizing → {completed, incomplete, failed}
('failed' reachable from any non-terminal, as today)
```

Remove the `scheduling`, `merging`, `amending`, `verifying` states/edges. The engine's
transition sites are rewritten to match in the same change (this is why the edge collapse is
safe — the only caller is the engine). Remove the dead `RunStatePayload.wave` field
(write-default-only, never read). Update the docstrings describing the old graph.

**`plan_state.py`** — `PlanStateValue` drops `awaiting_amendment` (live today at
`phases.py:324` and `engine.py:628`; removed coordinately with §4 and §8). `PlanStatePayload`
drops `sandbox_path` (a required field set at `phases.py:221`) and `plan_id`; `PlanState`
loses both constructor params and writes a single run-level `plan_state.json` (§10).
`orchestrator/lifecycle.py::PlanReport.terminal_state` stays `str`; ensure no
`"awaiting_amendment"` value is ever produced.

## §10. Artifacts layout (`artifacts.py`)

Flattened to run-level (one plan, so no `plans/<id>/` nesting):

```
<run_dir>/
  state.json              # run-level RunStateMachine
  plan_state.json         # plan-level PlanState   (was plans/<id>/state.json)
  run.log
  spec.md  spec.fingerprint  spec_amendments.md
  plan.json               # the structured Plan    (was planset.json)
  plan.md                 # the plan body          (was plan-<id>.md)
  inputs/ design.md  design.fingerprint
  iteration-<n>/ contract.md  summary.md  eval.json  triage.json  gap_fingerprint.json  verify.txt
```

(The on-disk dir keeps `iteration_dir`'s existing `iteration-<n>` name — only the `plans/<id>/`
nesting is dropped; no rename.)

**Remove** the path methods `planset_json`, `plan_md(id)`→`plan_md` (no arg), `plan_dir`,
`plan_state(id)`→`plan_state_json` (no arg), `plan_manifest`, `plan_merge`,
`conflict_fingerprint`, `git_violation`, and the `plan_id` parameter from every
`iteration_*`/`contract`/`summary`/`eval`/`triage`/`gap_fingerprint`/`verify_txt` method and
`ensure_iteration_dir`. No per-iteration diff artifact is added (the working tree + the user's
own git are the change record; this keeps the harness free of any git-read dependency).
`init_run_layout` is unchanged.

## §11. Exhaustive removal & edit manifest

**Delete files:** `src/forge_mcp/orchestrator/scheduler.py`, `src/forge_mcp/sandbox.py`,
`tests/test_scheduler.py`, `tests/test_sandbox_merge.py`, `tests/test_sandbox_manifest.py`.

**`sandbox.py` is fully orphaned** only after its three production importers are rewritten:
`engine.py` (merge logic), `phases.py` (manifest/`change_set`), `scheduler.py` (deleted). The
`Change` type lives only in `sandbox.py` and leaks via `PlanLoopResult.change_set`
(`phases.py:56`) into `engine.py:491-492` (the `Change` annotation at `:491`, the
`results[pid].change_set` read at `:492`); both go away together (the `change_set` field is removed
from `PlanLoopResult` and the engine merge code that read it is deleted) so no `Change`
reference dangles.

**Rewrite (production):** `models.py`, `drivers/planner.py`, `drivers/generator.py`,
`drivers/_codex.py`, `drivers/evaluator.py` (cwd), `orchestrator/engine.py`,
`orchestrator/phases.py`, `orchestrator/plan_state.py`, `orchestrator/statemachine.py`,
`orchestrator/amend.py` (signature, below), `artifacts.py`, `gitguard.py`, `config.py`
(remove `CONCURRENCY_CAP` and its three `engine.py` uses at `:25,409,427`),
`orchestrator/lifecycle.py` (single-plan projection).

**Create (new files):** `src/forge_mcp/drivers/remediator.py` — the `run_remediation` Claude
stage that authors each non-completing iteration's contract as a structured `RemediationResult`
(§6/§15). `models.py` (in the Rewrite list above) additionally gains the `RemediationResult`
model, and the matching new test is `tests/test_remediator.py` (§13).

**`amend.py`** — `apply_amendments` is called only in-loop now; change `proposed:
list[tuple[str, GapTriage]]` → `proposed: list[GapTriage]` and drop the `plan_id` field from
the `spec_amendments.md` entry (there is no plan id). Behavior (serial citation re-validation,
durable `spec.md` rewrite, churn fingerprint) is otherwise unchanged.

**Sequencing (mandatory).** The engine/phases/models rewrites land **before** the file
deletions, because `engine.py:33-49` imports `run_wave` and the merge symbols today — deleting
`scheduler.py`/`sandbox.py` first breaks the engine import immediately. Order: (1) rewrite
`models.py` + `phases.py` + `engine.py` + the drivers + state/artifacts to stop importing the
doomed symbols; (2) delete `scheduler.py`/`sandbox.py` + their tests; (3) prune orphaned
imports; (4) green.

**Stale prose to update after deletion:** `phases.py:4` ("consumed by the scheduler"),
`gitguard.py:1` (drop "snapshot/diff"), `engine.py` and `statemachine.py` docstrings (the
wave/scheduling/merging/amending graph).

## §12. Prompts

**`planner_system.md`** — regenerate the **Output Format** JSON skeleton from the new `Plan`
schema (`{surface, verification_command, body}`). This also fixes a pre-existing latent defect:
the current skeleton references `plan_id`, `request_summary`, `tasks`, `open_questions`,
`title`, `contract` — none of which exist on the model, all of which `extra="forbid"` would
reject. Reinforce "exactly one plan" (the existing Rule 5 already says "one plan per request").

**`generator_system.md`** — keep Rule 7 (no git mutations); state that the Generator edits the
project repository directly under `workspace-write`. The broader generator-prompt content
overhaul (its stale "emit JSON files" Output Format) is a **separate roadmap item** and is
deliberately **not** pulled in here.

## §13. Testing strategy

Success gate (`scripts/ci.sh`): `ruff check` + `ruff format --check` + `pyright` +
`scripts/check_docstrings.py` + the full `pytest` suite green.

- **Delete:** `test_scheduler.py`, `test_sandbox_merge.py`, `test_sandbox_manifest.py`.
- **Rewrite — `test_engine.py`:** remove the `add_conflict_edge` monkeypatch (`:459`) and the
  five conflict/amendment/cycle tests in the "Wave-boundary integration tests" block
  (`test_two_plans_conflict_loser_requeues_and_converges`,
  `test_conflict_records_fingerprint_file`, `test_unresolvable_overlap_stops_incomplete`,
  `test_awaiting_amendment_applies_and_requeues`, `test_cyclic_planset_finalizes_incomplete`).
  **Exception:** the same block also contains `test_planner_raises_finalizes_failed` (`:599`),
  which exercises the orchestrator-internal-error → `failed` finalization + lock-release path the
  new design **keeps** (§8) — **port it, do not delete it.** Add single-plan flow tests (plan →
  loop → finalize; `verified` honesty; incomplete on cap).
- **Rewrite:** `test_phases.py` (direct-edit loop, in-loop amendment, no git backstop, no
  `change_set`), `test_amend.py` (in-loop single-row signature), `test_models.py` (collapsed
  `Plan`, no `PlanSet`), `test_planner.py` (single-`Plan` output), `test_plan_state.py` (no
  `plan_id`/`sandbox_path`), `test_statemachine.py` (new edges, no `wave`), `test_gitguard.py`
  (only `git_deny_matches` survives), `test_codex_seam.py` + `test_sdk_contract.py`
  (`workspace_write` + `deny_all`), `test_artifacts.py` (flat layout), `test_generator.py`
  (`target_dir`), `test_evaluator.py` (cwd), `test_e2e_real_clis.py` (single-plan run).
- **New:** `test_remediator.py` — the `run_remediation` stage: asserts the structured
  `contract` field is parsed (not `result.text`, so turn narration cannot leak) and that the
  prompt carries the open gaps + synthesized blockers + NUDGE.
- **Unchanged (verify still green):** `test_convergence.py`, `test_verifier.py`,
  `test_triage.py`, `test_ids.py`, `test_lockfile.py`, `test_state.py`, `test_check.py`,
  `test_skills.py`, `test_cli.py`, `test_server.py`, `test_claude_seam.py`, `test_config.py`,
  `test_prompts.py`, `test_lifecycle.py` (light), `test_scaffold.py`.

## §14. Conventions

- **Three-section docstrings (0001 §16 / Rule 21).** Every non-trivial new or changed `def`
  carries a docstring whose body contains `Design:`, `Implementation:`, and `Example:`, each
  with ≥ 5 non-whitespace characters — enforced by `scripts/check_docstrings.py`. §15 models
  this for the load-bearing interfaces.
- **Pinned SDK versions:** `claude-agent-sdk == 0.2.108`, `openai_codex == 0.1.0b2`. (0001's
  `0.2.106` pin is stale; the shapes still hold.)
- **No out-of-scope work.** Every change above traces to the single-plan premise, the
  direct-edit Generator, or the autonomous-networked Generator requirement. The broader
  generator-prompt overhaul and the optional `disallowed_tools` Planner hardening are
  explicitly excluded.

## §15. Interface sketches (normative)

```python
# models.py
class Plan(BaseModel, extra="forbid"):
    """One actionable unit of work produced by the Planner (single-plan model).

    Design: §1/§3 the run executes exactly one plan, so the model carries only what
        the Generator and the completion gate need — the work contract, the surface
        that selects the Generator's capability preface, and the optional command
        that gates completion. The id/depends_on/file_scope fields of the multi-plan
        DAG are gone because there is no DAG and no cross-plan conflict.
    Implementation: a frozen-by-convention Pydantic model with extra='forbid' so the
        Planner's structured output cannot smuggle extra keys; surface is a closed
        Literal; verification_command is optional (None ⇒ no gate, verified stays an
        honest False).
    Example: Plan(surface='backend', verification_command='pytest -q', body='# contract').
    """
    surface: Literal["backend", "frontend"]
    verification_command: str | None = None
    body: str


class RemediationResult(BaseModel, extra="forbid"):
    """The remediation contract authored by one Remediation turn (§6).

    Design: §6 a non-completing iteration's contract is a focused plan over the
        still-open gaps; the stage returns it as structured output (this single
        field), not the raw turn text, so the agent's interstitial narration never
        leaks into the contract handed to the Generator.
    Implementation: a one-field Pydantic model, extra='forbid'; contract holds the
        Markdown plan; run_remediation returns RemediationResult(**structured_output).contract.
    Example: RemediationResult(contract='# Remediation plan for gap X').
    """
    contract: str


# drivers/planner.py
async def run_planner(runner, *, spec_text, plan_schema, cwd, run_log_path=None) -> Plan:
    """Run the Planner stage and return a single validated Plan (§5.1 superseded).

    Design: §3 the Planner reads the frozen spec and returns exactly one Plan; it is
        git-mutation-denied (PreToolUse git-deny hook) and runs under bypassPermissions
        so it can read the repo to ground the plan but cannot commit.
    Implementation: build_options(system=planner_system, output_format=envelope(plan_schema),
        hooks=git_deny_hooks(), cwd, cli_path) — the caller passes plan_schema=
        Plan.model_json_schema(); runner.run(prompt=spec_text); validate structured_output
        into a Plan.
    Example: await run_planner(r, spec_text='# spec', plan_schema=Plan.model_json_schema(),
        cwd=target_dir) returns one Plan.
    """


# drivers/remediator.py
async def run_remediation(runner, *, spec_text, plan_body, gaps, synthesized, nudge,
                          cwd, run_log_path=None) -> str:
    """Author the next iteration's remediation contract as a Claude plan (§6).

    Design: §4/§6 a non-completing iteration produces a focused, repo-grounded plan
        over the still-open gaps; git-mutation-denied under bypassPermissions, rooted
        at cwd=target_dir so it reads the repo but cannot commit.
    Implementation: build_options(system=remediation, output_format=
        envelope(RemediationResult.model_json_schema()), hooks=git_deny_hooks(), cwd,
        cli_path); compose a prompt from the frozen spec, project path, open gaps,
        synthesized blockers, the original plan (reference only), and a NUDGE note when
        set; runner.run; return RemediationResult(**structured_output).contract — the
        structured field, NOT result.text, so turn narration cannot leak in.
    Example: await run_remediation(r, spec_text='# spec', plan_body='# plan', gaps=[g],
        synthesized=[], nudge=False, cwd=target_dir) returns the contract Markdown.
    """


# drivers/generator.py
async def run_generator(runner, *, contract_text, target_dir, surface, run_log_path=None):
    """Run one autonomous Codex turn that edits target_dir directly (§5.2 superseded).

    Design: §5 the Generator edits the repository in place under workspace-write with
        network access and no human approval, bounded to target_dir (writes outside it
        fail closed). There is no copy-sandbox and no change_set — the edits ARE the
        output, left in target_dir for the human's git to review.
    Implementation: build_codex_config(codex_bin=codex_bin(), cwd=target_dir); compose
        generator_system + surface preface + contract_text; stream the turn to
        exhaustion; return the collected events. The thread runs sandbox=workspace_write,
        approval_mode=deny_all (the never-ask policy), config={'sandbox_workspace_write':
        {'network_access': True}}.
    Example: await run_generator(r, contract_text='do X', target_dir=p, surface='backend').
    """


# orchestrator/phases.py
async def run_plan_loop(*, layout, plan, target_dir, spec_text, spec_fingerprint,
                        claude_runner, codex_runner, schemas, max_iterations) -> PlanLoopResult:
    """Iterate one plan directly on target_dir to an honest terminal report (§4).

    Design: §4 drives generate→verify→evaluate→triage→synthesize→converge each
        iteration; a validated design-fault triage is applied to spec.md IN-LOOP
        (spec_text/fingerprint rebound) and the loop continues; completion is the
        two-conjunct gate (no remaining code bugs incl. the synthesized verify gap,
        AND verify passed). The iteration cap is the hard termination bound.
    Implementation: per iteration write the contract, run_generator on target_dir,
        optional run_verification(target_dir), run_evaluator(cwd=target_dir) against the
        current spec_text, optional triage, synthesize the verify gap only, fingerprint,
        apply any amendment via apply_amendments([row]) and rebind spec_text, then test
        completion / gap-non-progress / cap. No sandbox, no manifest, no git backstop.
    Example: a clean plan with no verify command and no gaps returns
        PlanLoopResult(terminal_state='done', iterations=1).
    """


# orchestrator/amend.py
def apply_amendments(layout, *, spec_text, spec_fingerprint, proposed: list[GapTriage],
                     now) -> AmendOutcome:
    """Apply validated design-fault amendments to spec.md in-loop (§4).

    Design: §4 the per-plan loop calls this with the single validated row for the
        current iteration; each amendment is re-validated against the then-current spec
        and applied serially, durably rewriting spec.md so the next iteration evaluates
        against it. Rejected proposals still feed the churn fingerprint.
    Implementation: for each triage carrying a proposed_amendment, re-check citations +
        target presence against current_spec; on pass replace before→after, recompute
        the SHA-256 fingerprint, durably rewrite spec.md (durable_replace) and atomically
        rewrite spec.fingerprint (light_replace), append a structured
        entry (no plan_id field) to spec_amendments.md; return the new spec/fingerprint
        and the churn fingerprint over all proposals.
    Example: apply_amendments(lay, spec_text='old', spec_fingerprint='fp', proposed=[row],
        now=t) returns AmendOutcome whose new_spec contains the amended text.
    """


# orchestrator/engine.py
async def run(self, *, target_dir, design_text, design_fingerprint, max_iterations,
              max_runtime_minutes, claude_runner, codex_runner, when) -> RunResult:
    """Drive one single-plan, direct-edit run end-to-end (§3.1 superseded).

    Design: §8 the conductor locks the target, freezes design→spec, plans once, runs the
        single plan loop directly on target_dir, and finalizes an honest RunResult. It
        performs NO git mutation; terminal cleanup (mark state, close drivers, release
        lock, restore umask) runs in a finally that re-raises an injected CancelledError.
    Implementation: umask; lock on the run-dir name; init_run_layout; RunStateMachine;
        transition planning→run_planner(→Plan)→_persist_plan→executing→run_plan_loop→
        finalizing→_finalize. status=completed iff the plan is done; verified iff done AND
        a verification_command was declared; failed only on an orchestrator-internal error.
    Example: a one-plan run that writes out.txt finalizes status 'completed', verified
        False (no command), with out.txt present in target_dir.
    """
```
