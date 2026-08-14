# 0001 — forge-mcp Harness

**What:** A clean-room design for **forge-mcp**, a stdio MCP server exposing a single
tool, `run_forge`, that drives a **Planner (Claude) → Generator (Codex) → Evaluator
(Claude)** loop to autonomously implement a feature described in a design document,
writing code into a caller-specified `target_dir`. It is the architecture from
Anthropic's *"Harness design for long-running application development"*, reduced to a
**Core + durability** scope and modernized against the pinned SDK betas.

**Status:** Design complete. This document is the normative implementation brief for an
agent building forge-mcp from scratch. Every interface sketch is normative. The existing
`develop` branch is a **reference only** (it was verified against the same SDK betas);
this design deliberately diverges from it (no cross-run resume, concurrent isolated
generation, a leaner module set) and must not be produced by copy-paste.

**Audience:** The implementing agent. Precision over prose.

**Correctness contract:** This document is read by the Evaluator stage to diff against the
produced code. Two disciplines therefore apply throughout:

1. **Verified facts are stated as normative.** Every SDK/MCP/git fact below was verified
   against the installed claude-agent-sdk `0.2.137`, the historical openai-codex `0.1.0b2`
   cached-wheel baseline, mcp `1.x`, and git `2.54.0`, or empirically reproduced.
2. **Version-volatile facts are marked `[verify-against-installed]`.** Where a fact can
   drift across SDK/tool versions, the marker tells the Evaluator to treat a *reasonable
   equivalent in the installed version* as conformant, not a gap. The marker also covers
   **internal labels** (state strings, gap titles, signal names) and **tunable policy
   constants** (thresholds, windows, defaults): any equivalent that preserves the stated
   behavior is conformant. The exact class/field/kwarg/enum names of the Python SDKs are
   **beta-volatile** and MUST be re-introspected at implementation time (§8.4).

---

## §1. Purpose & north star

forge-mcp orchestrates a Planner → Generator → Evaluator loop to implement a feature from
a design document into `target_dir`, iterating until the Evaluator confirms the code
matches the design or a hard budget is exhausted.

**The single most important design concern is mitigating *context anxiety*** — the
tendency of an agent to wrap up work prematurely (declare done, summarize instead of
execute, cut corners) as it perceives itself nearing its context-window limit. Over a
600-minute autonomous budget this is fatal. The architecture denies any single agent that
pressure through four mechanisms (**N1–N4**), all preserved here:

- **N1 — Fresh SDK session per phase.** Each Planner / Generator / Evaluator turn begins
  with a clean context. A session persists *within* a phase; nothing persists *across*
  phases except files on disk.
- **N2 — Structured handoff artifacts.** Phases never hand off live context — they hand
  off files (`spec.md`, `plan-*.md`, `contract.md`, `eval.json`, …). Each new session
  reads only the artifacts it needs, never a bloated transcript.
- **N3 — Hard agent boundaries.** Three driver subprocesses isolated by the SDKs; the
  orchestrator owns all durable state, so no agent has to.
- **N4 — Honest non-convergence.** The loop is explicitly bounded (`max_iterations`,
  `max_runtime_minutes`) and stops early on detected non-progress; a stop returns
  `status="incomplete"` with `unresolved_gaps` populated, removing any incentive to fake
  completion.

The **Core + durability** scope adds three guarantees that make the budgeted run converge
on *verified* code and survive a crash, **without** eroding N1–N4:

- **D1 — Verification gate.** `completed` requires the Evaluator's `no_gaps` and, **when a
  verification command is declared**, that command passing; the `verified` flag records
  whether a verification command actually ran and passed (§6.5).
- **D2 — Crash-safe durable state.** Atomic, single-writer state files and append-only
  per-iteration artifacts mean a `kill -9` at hour 9 leaves a complete, uncorrupted
  forensic record. **There is no cross-run resume** — every `run_forge` call is a fresh
  run (§6.6); durable state exists for forensics and live observability, not re-entry.
- **D3 — Oscillation / non-progress detection.** A pure policy stops the loop when the
  open-gap set (per plan) or the conflict / amendment-churn set (run level) stops shrinking
  (§6.7).

Every structural decision serves N1–N4 first. The 600-minute budget is the load-bearing
budget anchor; other numeric defaults are tunable.

---

## §2. Scope & non-goals

**In scope (all fully implemented in this design — no deferred/"future" work):**

- The single MCP tool `run_forge` over stdio (FastMCP), and a `forge` CLI with `serve`
  and `check` subcommands.
- The Planner → Generator → Evaluator loop with N1–N4 and D1–D3.
- **Concurrent plan execution via isolated directory-copy sandboxes**, structured as
  **waves** with a dependency DAG, where the **orchestrator** is the sole authority for
  all shared-file and cross-plan mutations (merge, spec amendment, conflict resolution),
  performed only at wave boundaries (§7).
- A change-kind-aware file-union merge (files, symlinks, directories) with cumulative
  cross-wave overlap detection, an explicit conflict-resolution state machine, and honest
  non-convergence (§7).
- The Evaluator gap → triage (spec-issue vs implementation-gap) cycle, including the
  orchestrator-applied **spec-amendment** flow (§5.3).
- Full **skills wiring for both engines** and a per-engine `forge check` probe (§10).
- Git-commit denial across all stages (§9).
- Per-run timestamped artifacts under `target_dir/.harness/<YYYYMMDDHHMMSS>/` (§11).

**Non-goals (definitional exclusions, not deferrals):**

- **Cost / token / billing accounting.** Excluded by definition.
- **Playwright / browser verification.** Excluded by definition; the verification gate
  (§6.5) is a deterministic shell command only.
- **Cross-run resume.** Removed by decision (§6.6). No `resume` input, no resumable-run
  scan, no interrupted-iteration archival, no lock adoption.
- **MCP resource surface & subscriptions** (`list_resources`/`read_resource`/`subscribe`).
  Removed: a host reads artifacts from disk directly.
- **MCP task-augmented tool API** (`call_tool_as_task`, `ServerTaskContext`). Removed: the
  host holds the stdio session for the run's duration (§4.1). A dropped session ends the
  run; durable state still survives on disk.
- **Cross-run learning / lineage / watchdog.** Removed: out of the Core + durability scope.
- **Filesystem metadata beyond content + permission bits.** The sandbox manifest (§7.2)
  captures regular-file content, symlink targets, directory entries, and `st_mode`
  permission bits only; ownership/uid-gid, xattrs, ACLs, and mtime are intentional
  non-goals.

---

## §3. Process model & state machine

### §3.1 The run (wave-structured)

```
run_forge(target_dir, design_doc_path | design_doc_content, max_iterations=10, max_runtime_minutes=600)
└─ acquire per-target lock · create run dir · copy design → inputs/design.md (IMMUTABLE) +
   design.fingerprint, and → spec.md (orchestrator-owned working copy) + spec.fingerprint
   (= design.fingerprint at init) + an empty spec_amendments.md
   PLAN   Planner (Claude, fresh session) reads the frozen spec.md, invokes the plan-writing
          skill, emits a PlanSet: 1..N plans, each {id, depends_on[], surface, file_scope,
          verification_command} + an optional run-level verification_command → plan-<id>.md
   repeat WAVES until every plan is terminal (done/incomplete/failed), or the runtime cap,
   or run-level non-progress (§6.7) — re-planning from conflicts/amendments adds plans/edges:
     SCHEDULE  pick the next wave = ready plans (all transitive deps merged) under a
               concurrency cap; lazily create each plan's sandbox + manifest NOW, post-merge
     EXECUTE   run each wave plan's per-plan loop (§3.2) CONCURRENTLY, isolated; during a
               wave spec.md and target_dir are FROZEN (read-only to plans)
     ── wave boundary; the orchestrator alone, single-threaded, in order: ──
     MERGE     union completed plans' change-sets into target_dir with cumulative
               cross-wave overlap detection + conflict-resolution state machine (§7.4)
     AMEND     drain plans' PROPOSED spec amendments, re-run the citation gate, apply them
               serially to spec.md, append to spec_amendments.md, bump the spec fingerprint
     INVALIDATE plans whose file_scope/surface intersects an amended section or a merge
               conflict are queued to re-run next wave — never-merged plans discard their
               sandbox; already-merged plans keep merge_status=merged + re-run as a delta (§5.3)
   VERIFY-RUN (after the final merge) run the run-level verification_command over target_dir
   finalize → RunResult{status, run_dir, iterations, unresolved_gaps, failure_kind,
              stop_reason, verified, summary}
```

The orchestrator owns every mutation of a *shared* artifact (`spec.md`,
`spec_amendments.md`, `target_dir`) and, **after run-init**, performs them **only at wave
boundaries**, single-threaded (run-init's one-time creation of `spec.md` and the empty
`spec_amendments.md` precedes any wave). Plans never write shared artifacts (Invariant I1, I13).

### §3.2 Per-plan iteration loop

Each plan runs the reference loop, isolated in its own sandbox, reading the wave's frozen
`spec.md` snapshot. The per-iteration phase order is **load-bearing** (it determines what
blocks a false `completed`):

```
iter_generating   Generator (Codex, full access) implements contract.md in the sandbox,
                  then writes a brief iteration-N/summary.md (self-eval of what it did)
iter_verifying    (if verification_command) run it in the sandbox → verify.txt
iter_evaluating   Evaluator (Claude) diffs sandbox code vs the wave's frozen spec.md → eval.json
iter_triaging     (only if gaps) Evaluator classifies each gap → triage.json; a confirmed
                  design fault is recorded as a PROPOSED amendment in triage.json (the plan
                  never writes spec.md)
synthesize        append a high-severity gap for (a) any git-mutation violation and
                  (b) any verification failure, BEFORE fingerprinting (both are always
                  code-bugs, never demotable; each carries a fixed sentinel
                  design_doc_section — "§9" for git, "§6.5" for verify), so both block
                  completion and feed remediation
fingerprint       write gap_fingerprint.json = sorted list of "title|severity"
iter_done         record last_completed_iteration = n (forensic only)
amendment-needed? if this iteration recorded a validated design-fault PROPOSED amendment,
                  the plan STOPS the wave (state: awaiting_amendment) — NOT 'completed'; the
                  orchestrator applies it at the boundary (AMEND) and the plan re-runs next
                  wave against the amended spec; else
completion?       this plan is COMPLETE if §6.5 holds; else
non-progress?     this plan stops 'incomplete' if its gap-set is stuck (§6.7); else
cap?              this plan stops 'incomplete' if its iteration cap hit; else
iter_remediating  write the next remediation contract (implementation gaps) → next iteration
```

The orchestrator seeds iteration-1 `contract.md` from the plan's `plan-<id>.md` body at
SCHEDULE (the sole producer of the first contract; later contracts come from
`iter_remediating`). A plan that needs a spec amendment to proceed emits the proposed
amendment and stops the current wave for that plan; the orchestrator applies it at the wave
boundary (AMEND) and the plan re-runs next wave against the amended spec.

### §3.3 State shape

State is split so a single flat enum need not express N concurrent plans, and so each file
has exactly one writer (Invariant I1):

- **Run-level `state.json`** — written only by the orchestrator's `RunStateMachine`.
  States: `init → planning → (scheduling → executing → merging → amending)* → verifying →
  finalizing → {completed | incomplete | failed}`, where `scheduling → executing → merging →
  amending` is a **per-wave cycle** repeated until every plan is terminal (§3.1 "repeat
  WAVES"); INVALIDATE is part of the `amending` boundary phase (reflected by
  `merge_status="invalidated"` in `merge.json`). Terminal states `{completed, incomplete,
  failed}` do not advance `last_phase` (used for failure attribution).
- **Per-plan `plans/<id>/state.json`** — written only by that plan's driver context.
  Minimum fields: `plan_id`, `sandbox_path`, `state ∈ {generating, verifying, evaluating,
  triaging, remediating, awaiting_amendment, done, incomplete, failed}`, `iteration`,
  `last_completed_iteration` (forensic). The exact field set is not normative beyond these.
  (`merge_status ∈ {pending, merged, conflicted, invalidated}` is **not** a per-plan-state
  field — it is set by the orchestrator at wave boundaries, so it lives solely in the
  orchestrator-owned `merge.json` (§11), preserving I1's single-writer rule.)
- **Shared run-level artifacts** (`spec.md`, `spec_amendments.md`) — written only by the
  orchestrator, at wave boundaries (I1, I13).

`[verify-against-installed]` the exact state string values are internal labels; equivalent
names that preserve the ordering and terminal-set invariants are conformant.

---

## §4. MCP surface & CLI

### §4.1 Server shape — FastMCP, synchronous stdio

**Decision:** forge-mcp uses the **high-level FastMCP** API over **stdio**, exposing one
tool. The host holds the stdio session for the run's duration. This is the
Simplicity-First correct choice given the scope removed both the resource surface and
resume — the two reasons `develop` used the low-level `Server`.

```python
from mcp.server.fastmcp import Context, FastMCP   # [verify-against-installed]: mcp 1.x

mcp = FastMCP(name="forge-mcp")

@mcp.tool()
async def run_forge(target_dir: str, design_doc_path: str | None = None,
                    design_doc_content: str | None = None, max_iterations: int = 10,
                    max_runtime_minutes: int = 600, *, ctx: Context) -> RunResult:
    """Drive the Planner→Generator→Evaluator loop to implement the design into `target_dir`.

    Design: §1 the single tool; N1–N4 context-anxiety north star; D1–D3 durability. The
        parameters are SPREAD (not a single Pydantic param) so FastMCP generates a FLAT input
        schema whose top-level property names ARE the `RunForgeInput` anchor fields
        (§4.3/§14); the `RunResult` return yields structured output. `ctx` carries advisory
        progress only (§4.2).
    Implementation: validate by constructing `RunForgeInput(**params)` in-body (runs the
        path/content xor); enforce the runtime cap with
        `asyncio.wait_for(orchestrator.run(...), timeout=max_runtime_minutes*60)`; a
        TimeoutError finalizes 'incomplete'; an `asyncio.CancelledError` (host disconnect) runs
        the terminal-cleanup `finally` (mark state, close drivers, no git ops) and re-raises.
        The run never depends on `ctx` for correctness.
    Example: result = await run_forge(target_dir="/repo",
        design_doc_path="/repo/feature.md", ctx=ctx)  # -> RunResult(status="completed", ...)
    """
```

`forge serve` runs the server over stdio via the synchronous `mcp.run()` (stdio is the
default transport). `[verify-against-installed]` `mcp.run()` / `FastMCP` / `@mcp.tool()` /
`Context` are mcp 1.x shapes; forge-mcp is a stdio server by choice. The load-bearing
constraint is the **mcp 1.x major line** (the v2.x `mcp.server.mcpserver.MCPServer` is out
of range for the `mcp[cli] >=1.12,<2` pin and must not appear in the code); the exact floor
(`1.12`) is the pin, not a behavioral requirement.

**Cancellation honesty.** MCP stdio has no protocol idle timeout; the only thing that ends
a long call is an actual host disconnect or a host-side request timeout, surfacing as
`asyncio.CancelledError` in the tool body. The orchestrator's terminal cleanup (mark state,
close SDK drivers, release the lock, perform **no** git operations) MUST run in a
`finally`/except so durable `.harness` state stays consistent. The runtime cap is enforced
in-body (`asyncio.wait_for`), never by any transport timeout.

### §4.2 Progress

`await ctx.report_progress(progress, total, message=...)` and `await ctx.info(...)` at
phase/iteration boundaries are **advisory observability only**: `report_progress` is gated on
a host-supplied `progressToken` (no token → the SDK suppresses the progress notification);
`ctx.info` is a logging notification (`notifications/message`), not progressToken-gated.
Neither extends nor resets the host's request timeout, and neither is a keepalive. The run's
correctness never depends on them. `[verify-against-installed]` the
`report_progress(..., message=)` signature.

### §4.3 `run_forge` input/output

Both are Pydantic models so the JSON schema stays in sync with the code. The field **names**
of `RunForgeInput` / `RunResult` are **load-bearing schema anchors** the Evaluator diffs
against (§17) and must match exactly; internal schemas (Eval/Triage/Plan rows, §14) are an
internal contract where semantically-equivalent renames are conformant.

```python
class RunForgeInput(BaseModel, extra="forbid"):
    """Validated input to run_forge.

    Design: §4.3 — `target_dir` is required; the design doc is supplied as EITHER a path
        OR inline content (exactly one). The xor lives in a model_validator, NOT the JSON
        schema (a cross-field xor is naturally a validator and keeps the FastMCP-derived
        tool-input schema flat; "no top-level combinators" is itself a Claude
        structured-output requirement — see §14 — not an MCP tool-input one).
    Implementation: fields below + `_exactly_one_design_doc` validator.
    Example: RunForgeInput(target_dir="/repo", design_doc_path="/repo/feat.md").
    """
    target_dir: str
    design_doc_path: str | None = None
    design_doc_content: str | None = None
    max_iterations: int = 10          # per-plan iteration cap; tunable [verify-against-installed]
    max_runtime_minutes: int = 600    # wall-clock budget; the load-bearing north-star anchor (§1)
    # @model_validator(mode="after"): exactly one of design_doc_path / design_doc_content

class RunResult(BaseModel, extra="forbid"):
    """Structured result of a run.

    Design: §4.3, N4 honest non-convergence — `status` is the truth of the run; `verified`
        records whether D1's gates actually ran and passed.
    Implementation: fields below; on non-completion `unresolved_gaps` is the union over all
        non-completed plans of each plan's highest-iteration FULL post-synthesize gap set
        (eval.json gaps ∪ the synthesized git-violation / verify-failure gaps — the same set
        §6.5 gates on, so a verify-only or git-only block is still reported — ∪ a synthesized
        failure GapSummary for any per-plan `failed` plan with no gap set, §6.4); per-plan
        freshest, not aggregated across iterations, not collapsed across plans.
    Example: RunResult(status="incomplete", run_dir="...", iterations=23,
        unresolved_gaps=[...], stop_reason="non-progress: gap-set stable", verified=False,
        summary="3/4 plans completed; plan-2 stalled").
    """
    status: Literal["completed", "incomplete", "failed"]
    run_dir: str
    iterations: int                   # SUM of iterations performed across all plans
    unresolved_gaps: list[GapSummary] = []
    failure_kind: str | None = None   # status="failed" = orchestrator-internal/unhandled error ONLY,
                                      # NOT per-plan failure or non-convergence (those → "incomplete")
    stop_reason: str | None = None    # set when status == "incomplete" (cap / non-progress / conflict)
    verified: bool                    # D1 gates ran and passed (per-plan + run-level post-merge)
    summary: str

class GapSummary(BaseModel, extra="forbid"):
    """One unresolved gap surfaced in RunResult (a projection of EvalGap).

    Design: §4.3/§14 — nested inside the anchored RunResult, so {title, severity,
        design_doc_section} are part of the schema anchor (must match exactly); any added
        field is internal/tunable.
    Implementation: projected from each non-completed plan's freshest full post-synthesize
        gap set (§6.5) on non-completion; synthesized git/verify gaps carry a fixed sentinel
        `design_doc_section` ("§9" / "§6.5") so the non-optional field is always populated.
    Example: GapSummary(title="missing delete propagation", severity="high",
        design_doc_section="§7.4").
    """
    title: str
    severity: str
    design_doc_section: str
```

### §4.4 `forge check`

A diagnostics subcommand (the `doctor`), exit non-zero on any `FAIL`. Each check returns
`(label, status ∈ {OK, WARN, FAIL}, detail)`; `forge serve` runs the same checks as
preflight and maps `FAIL → a tagged error` before starting a run. Checks (§10.3): target/
harness writable; `git` available; Claude Code CLI ≥2.1.153; Codex binary +
`openai_codex` import + `codex --version` smoke; SDK contract introspection (§8.4);
**per-engine skill discovery** (§10.3); disk-space `WARN` below a threshold. A Claude CLI
or SDK contract `FAIL` suppresses the live Claude skill probe.

---

## §5. The three stages

All stage prompts follow named principles from Anthropic's prompt-engineering tutorial
(verified): **clarity & directness**, **role/system framing**, **structured-output
instructions**, **think-then-answer precognition**, and a **single worked example that
beats enumerated rules**, assembled in the complex-prompt order (task context → rules →
worked example → input → task → output format). Apparent contradictions are resolved
in-text (e.g. the Generator's "do not wrap up early" budget rule and "build nothing beyond
the contract" scope rule are stated to *not* conflict). `[verify-against-installed]` exact
prompt wording, section ordering, and probe timeouts are operational and must not be
diffed literally — they are behavioral requirements.

### §5.1 Planner (Claude)

- **Input:** the wave's frozen `spec.md` (the orchestrator-owned working copy of the
  design doc; the immutable original is `inputs/design.md`).
- **Skill:** invokes the plan-writing skill (`superpowers:writing-plans` on Claude
  `[verify-against-installed]`).
- **Output (structured, json_schema):** a **PlanSet** — for each plan a `plan-<id>.md`
  body plus metadata `{id, depends_on: [id], surface: "backend" | "frontend", file_scope:
  [glob], verification_command: str | null}`, plus an optional run-level
  `verification_command` for the post-merge gate (§6.5). `file_scope` is a best-effort
  ownership hint that lets the scheduler place disjoint plans in the same wave;
  overlap detection (§7.4) is the safety net, not `file_scope`.
- **Prompt stance:** decompose to product/architecture, not premature implementation
  detail (per the long-running-apps guidance); plan only what the design requires; record
  ambiguities as open questions rather than silently resolving them. Forbidden from git
  mutations (§9).

### §5.2 Generator (Codex, full access)

- **Input:** one plan's `contract.md` (what "done" looks like + how it is testable),
  executed inside that plan's **directory-copy sandbox** (§7).
- **Skills:** backend plans use the plan-execution skill; `surface: "frontend"` plans use
  the frontend-design skill. On Codex these load **natively** — there is no `Skill` tool;
  the skill content is surfaced and followed, so the prompt references the skill by
  capability/behavior, not "invoke the Skill tool" (§10.1). `[verify-against-installed]`
  the literal skill ids.
- **Access:** unconditional full host access — `Sandbox.full_access` + `ApprovalMode.deny_all`
  (§8.2). Non-interactive and autonomous.
- **No commits:** the sandbox has no `.git`, so `git commit` is structurally impossible
  there (§9); the prompt still forbids git mutations as defense-in-depth.
- **Prompt stance:** simplest implementation that satisfies the contract; strict scope
  discipline (build nothing beyond it); honest non-convergence (never fake done).

### §5.3 Evaluator (Claude) — gap → triage → remediate / propose-amendment

- **Skill:** the code-review skill (`code-review:code-review` on Claude
  `[verify-against-installed]`), **focused on the gap between the generated code and the
  wave's frozen `spec.md`**, not general review.
- **Output 1 — `EvalResult` (structured):** `{no_gaps: bool, gaps: list[EvalGap], summary}`,
  where `EvalGap = {title, severity, design_doc_section, current_state, expected_state,
  suggested_fix}`. `EvalGap.title` is whitespace-canonicalized for stable triage joins.
- **Output 2 — `TriageResult` (structured), only if gaps:** `{triages: list[GapTriage]}`, where
  `GapTriage = {gap_title, design_fault: bool, fault_kind ∈ {contradiction, infeasibility,
  deprecated_dependency, ambiguity, other, null}, cited_sections: [str], explanation,
  proposed_amendment?}`. A row
  may be a **design fault** (the spec is wrong/ambiguous) only if `design_fault` *and*
  `fault_kind` is set *and* every `cited_sections` entry is a non-trivial verbatim
  substring of the current `spec.md` (the **citation gate**; the threshold is **≥20
  characters** `[verify-against-installed]` — a tunable policy constant; what is normative
  is "non-trivial verbatim substring"). Anything else demotes to an **implementation gap**
  (code-bug).
- **Implementation gap →** a remediation contract is written (using the plan-writing
  skill) and the plan iterates: Generator implements → re-evaluate.
- **Design fault → propose an amendment (the per-plan Evaluator never writes `spec.md`):**
  the Evaluator records the proposed amendment `{cited_sections, before, after, rationale}`
  in its `triage.json`. The **orchestrator** applies amendments at the wave boundary
  (§3.1 AMEND), single-threaded:
  1. `inputs/design.md` stays **immutable** (provenance).
  2. The orchestrator re-runs the citation gate against the *then-current* `spec.md` —
     verifying both that every `cited_sections` entry **and the amendment's actual edit target
     `proposed_amendment.before`** are still verbatim substrings of the current `spec.md`. **If
     it passes**, it applies the amendment and **bumps a spec fingerprint**. **If it rejects**
     (the cited text or `before` is no longer a verbatim substring — e.g. an earlier amendment
     changed it), the amendment is dropped, the gap is demoted to an **implementation gap**, and
     the
     plan re-queues for normal per-plan remediation next wave (so its per-plan
     non-progress/cap can bind); the rejected amendment is also recorded into the §6.7
     amendment-churn history, so a thrashing amendment loop stops the run honestly. This gives
     an `awaiting_amendment` plan a terminating path other than the runtime cap.
  3. A structured entry is appended to the append-only **`spec_amendments.md`**
     `{iteration, plan_id, fault_kind, cited_sections, before, after, rationale}` (the
     orchestrator is the sole writer — I1).
  4. Plans whose `file_scope`/`surface` intersects an amended section are **invalidated**
     and re-plan/re-evaluate against the new `spec.md` next wave (their sandboxes
     discarded). An **already-merged** plan touching an amended section instead keeps
     `merge_status=merged` (its changes stay in `target_dir`) and re-runs to produce a
     *delta* against the post-merge baseline — distinct from a never-merged invalidated plan,
     whose sandbox is discarded.
  5. Because amendments apply only at wave boundaries, every plan in a wave evaluates
     against a single **frozen** `spec.md` snapshot; code baselines (§7.2) and the spec
     baseline advance together between waves.
- **Loop** until every plan satisfies the §6.5 four-conjunct completion gate
  (effective_no_gaps ∧ verify ∧ no git violation ∧ no pending amendment; contributing to a
  run-level `completed`), or a cap / non-progress stop.

The Evaluator is a **skeptical external judge** (separating the doer from the judge is the
strong lever against self-assessment inflation). Its prompt carries exactly one good-gap
example (precise §-citation, location, remedy) and one explicit weak-gap anti-example.

---

## §6. Context-anxiety & durability mechanisms

### §6.1 N1 — fresh SDK session per phase

Every Planner / Generator / Evaluator / triage call opens a new SDK session (Claude:
a fresh `ClaudeSDKClient` context; Codex: a fresh `AsyncCodex` + thread). No session is
reused across phases. Within a phase the SDK's own session persists for that single turn.

### §6.2 N2 — file-based handoff

The only cross-phase channel is files (§11). A new session reads `spec.md` / `contract.md`
/ `eval.json` — never a prior transcript.

### §6.3 N3 — hard boundaries

Three driver subprocesses, isolated by the SDKs. The orchestrator owns all durable state;
no agent maintains run state.

### §6.4 N4 — honest non-convergence

Caps, per-plan non-progress, and run-level conflict or amendment-churn non-progress produce
`status="incomplete"` with `unresolved_gaps` = the union over all non-completed plans of each
plan's highest-iteration full post-synthesize gap set (eval.json gaps ∪ synthesized git/verify
gaps, §6.5), and a `stop_reason`. A per-plan **`failed`** plan with no gap set (e.g. a crash)
contributes a **synthesized failure `GapSummary`** (title naming the plan + its per-plan
failure reason — distinct from `RunResult.failure_kind` — and a sentinel `design_doc_section`)
so a crashed plan is never silently absent. There is no path by
which an agent benefits from claiming false completion.

### §6.5 D1 — verification gate (four-conjunct completion + post-merge pass)

**Per-plan completion** requires ALL of (this ordering is normative):

```
# "no remaining code-bug gaps" is computed over the iteration's FULL post-synthesize gap set
# — (this iteration's eval.gaps UNION the synthesized git-violation and verify-failure gaps)
# — left-joined to the CURRENT iteration's triage.json by whitespace-canonicalized title.
# Count only gaps whose triage row is NOT a validated design_fault; a gap with no triage row,
# and every synthesized gap, counts as a non-demotable code-bug (matching gap_fingerprint.json,
# which is written after synthesize).
effective_no_gaps  = (no remaining code-bug gaps) AND (eval.no_gaps OR triage_ran)
verify_passed      = (verification_command is None) OR (last_verification.passed)
no_git_violation   = (this iteration's gitguard diff is empty)            # symmetric backstop
no_pending_amend   = (no validated design-fault amendment awaits AMEND)   # else stop-wave/re-run
plan_completed     = effective_no_gaps AND verify_passed AND no_git_violation AND no_pending_amend
```

Per-plan verification runs **in the plan's sandbox** and is deterministic and
orchestrator-owned. The load-bearing semantics are: run the command via a shell in
`cwd=sandbox`, capture and bound the output to `iteration-N/verify.txt`, `passed =
exit_code == 0`, and a timeout ⇒ `passed=False, timed_out=True` (no exception on non-zero).
One conformant realization wraps `subprocess.run(command, shell=True, cwd=sandbox,
capture_output=True, text=True, timeout=…, check=False)` in a `try/except
subprocess.TimeoutExpired → passed=False, timed_out=True`: `check=False` suppresses the
non-zero-exit exception, but `timeout=` *raises* `TimeoutExpired` (it does not return a
sentinel), so the timeout branch must be caught. Any equivalent yielding those semantics is
conformant. `last_verification` is the cached outcome from `iter_verifying`,
read at completion (not re-run). `shell=True` is acceptable because `verification_command`
originates from the Planner (an Anthropic-model output), not arbitrary user input.

**Per-plan verification gates the plan's sandbox, not the merged tree.** A command that
passes in isolation can fail post-merge once siblings are unioned in; merged correctness
therefore rests on overlap detection (§7.4) being complete. To close this, after the final
merge the orchestrator runs the **run-level `verification_command`** (declared by the
Planner) over `target_dir` (`VERIFY-RUN`, §3.1). `RunResult.verified` is true only if every
completed plan's per-plan gate passed **and** the run-level post-merge gate passed; if no
run-level command was declared, `verified=False` with an honest note (merged-tree
verification was not performed).

### §6.6 D2 — crash-safe durable state, no resume

Durable, atomic, single-writer state (§13) means a crash leaves a consistent forensic
record. **Every `run_forge` call is a fresh run** in a new `.harness/<timestamp>/` dir.
There is no `resume` input, no scan for resumable runs, no interrupted-iteration archival,
no lock adoption. `last_completed_iteration` is retained as a forensic "how far did this
plan get" field, not a resume anchor.

### §6.7 D3 — oscillation / non-progress (pure policy, two scopes)

A pure, no-I/O module:

```
fingerprint(items)        -> frozenset of stable strings (order-independent, ignores prose)
detect_non_progress(history, window=2):
    if len(history) < window:          -> none
    if latest fingerprint is empty:    -> none
    if len(history) >= 2*window AND last 2*window all == latest: -> EARLY_STOP  (honest stop)
    elif last window all == latest:    -> NUDGE                  (nudge the next contract)
    else:                              -> none
```

- **Per-plan scope:** run over that plan's `gap_fingerprint.json` sidecars (fingerprint of
  `"title|severity"`). `EARLY_STOP` → that plan stops `incomplete` (with a `stop_reason`)
  **before** its iteration cap; `NUDGE` → the next remediation contract is nudged.
- **Run-level scope:** run over two run-level histories: (i) the orchestrator's
  `conflict_fingerprint.json` (fingerprint of `"path|sorted(plan_ids)|kinds"` for each
  unresolved merge conflict, §7.4); and (ii) **amendment churn**, built in-process at each
  AMEND boundary from that wave's **processed-amendment batch — both applied AND
  citation-gate-rejected** proposals (the orchestrator already holds the full batch when
  draining it serially; in-memory only, no new *persisted* artifact — note rejected proposals
  are deliberately *not* written to `spec_amendments.md` but still count toward churn, so a
  repeatedly-rejected amendment trips the stop) — fingerprint the sorted
  `"cited_sections|fault_kind"` of every amendment in that wave's batch. Only waves with a
  **non-empty** processed batch append a fingerprint (so the window measures consecutive
  amendment-bearing waves, not intervening clean ones); feed that sequence into the same
  `detect_non_progress`. `EARLY_STOP` on either → the **run** stops `incomplete` with
  `stop_reason="unresolvable cross-plan overlap"` or `"amendment thrash"`, rather than burning
  the budget to the runtime cap. This makes I7 honest for the cross-plan path, which a
  purely per-plan detector cannot observe.

`[verify-against-installed]` the `window` value is a tunable policy constant, and the
`EARLY_STOP`/`NUDGE` return values are internal labels — any pair of signals distinguishing
"honest early stop" from "nudge the next contract" is conformant.

---

## §7. Concurrency & merge (directory-copy isolation, orchestrator-owned)

**Decision:** per-plan isolation uses **directory-copy sandboxes**, not git worktrees.
Because no stage commits and git-state is explicitly irrelevant ("if the Evaluator passes,
it passes"), a directory copy is simpler, equally correct, dependency-complete (so
in-sandbox verification works), git-repo-independent, race-free, and makes the Generator
structurally unable to commit.

### §7.1 Scheduling, concurrency, and failure isolation

The PlanSet is a DAG (`depends_on`). The orchestrator runs **waves**: each wave is the set
of ready plans (all transitive dependencies merged) up to a bounded concurrency cap. Plans
within a wave run concurrently; the orchestrator's shared-artifact mutations happen only at
the wave boundary (§3.1).

**Failure isolation (I8) is a contract on the fan-out primitive.** Each plan coroutine
**catches its own exceptions internally** and resolves to a per-plan terminal state
(`done`/`incomplete`/`failed`) — it never propagates. The fan-out uses
`asyncio.gather(*plan_tasks, return_exceptions=True)` (or a semaphore-bounded set of
awaited tasks), **not** `asyncio.TaskGroup` (which cancels siblings on the first error and
would violate I8). The only sanctioned mass-cancellation is the run-level
`asyncio.wait_for` runtime cap (§4.1), which is distinct from per-plan failure.

### §7.2 Sandbox creation (lazy, post-merge) & manifest

For each plan **in the wave being scheduled** (never eagerly at run start — I14), copy
`target_dir` into `target_dir/.harness/<run>/plans/<id>/sandbox/` **excluding `.git` and
`.harness`** (copy everything else — including gitignored build deps — so in-sandbox builds
work; copy symlinks **as links** via `os.symlink`, never dereferenced; preserve file modes).
The destination lives under `.harness/`, which is self-ignored (a never-overwritten `*`
`.gitignore` created with `O_EXCL`) and ignored by the repo, so sandboxes never re-enter any
later operation on `target_dir`. A dependent plan's sandbox is copied from the **post-merge**
`target_dir`, so it builds on its merged predecessors (I14).

At creation, capture a **content manifest** keyed by relative path:
`{path → (kind, digest, mode)}`, `kind ∈ {file, symlink, dir}`:

- **file** — `digest = sha256(bytes)`, `mode = stat.S_IMODE(st_mode)` (permission + setuid/
  setgid/sticky bits; type bits masked out — `S_IMODE` is used consistently for capture, the
  §7.3 comparison, and the §7.4 `os.chmod`, so type bits never enter a chmod or comparison).
- **symlink** — captured via `os.lstat` (**never followed**); `digest =
  sha256(os.readlink(path).encode())` (hash the target *string* — `os.readlink` returns
  `str`, so `.encode()` is required since `sha256` takes bytes — not dereferenced bytes, so a
  dangling link never raises and a retarget-to-identical-content is still detected). Symlink
  permission bits are not meaningfully portable (a plain `os.chmod` would follow the link and
  mutate the *target*), so symlinks carry **no** `mode` and are excluded from mode-change
  detection (§7.3) and application (§7.4 step (3)).
- **dir** — no digest; `mode` only (so empty dirs and dir-mode changes are representable).

### §7.3 Change detection

After the Generator finishes, diff the edited sandbox against its manifest, per path:

- **added** / **deleted** — path present now but absent in manifest / vice-versa (any kind).
- **modified** — both present, same kind, digest differs (file content or symlink target).
- **type-flip** — both present, kind differs (e.g. file↔symlink, file↔dir) → treated as
  **modified** (delete-old-kind + add-new-kind at merge).
- **mode-changed** (files & dirs only, not symlinks — §7.2) — both present, same kind, digest
  equal, mode differs (e.g. executable
  bit, or a directory's permission bits).

Regular-file comparison is byte-exact (`sha256`), never text-mode, so binary files are
handled correctly. Symlinks use `os.lstat`/`os.readlink` throughout (never dereferenced). Each
**added or modified** file/dir change-set row also records the re-stat'd post-edit
`stat.S_IMODE(st_mode)` (not only the `mode-changed` kind), so §7.4 steps (1)/(2) have the mode
to `chmod` an added/modified path to.

### §7.4 Merge (change-kind-aware, cumulative overlap, conflict state machine)

At a wave boundary the orchestrator unions each completed plan's change-set into
`target_dir`, single-threaded. Apply by change kind, in this order so directory structure
is consistent: **(1)** create added directories **idempotently and parents-first** via
`os.makedirs(path, exist_ok=True)`, then `os.chmod` each to its **change-set's recorded
post-edit `mode`** (re-stat'd during §7.3 change detection — an added directory has no
pre-edit §7.2 manifest entry) — since
`os.makedirs` applies `0o777 & ~umask` = `0700` under the run's `umask(0o077)`, an added
dir's captured in-scope permission bits (§7.2/§2) would otherwise be lost. For a
**file→dir / symlink→dir type-flip** (§7.3) the old non-directory leaf MUST be `os.unlink`'d
**before** `os.makedirs` (which raises `FileExistsError` on a pre-existing regular file at the
leaf) — the remove-before-recreate rule below applies symmetrically here, not only to step
(2). A directory a sibling plan already created earlier in the wave is a no-op (never
`FileExistsError`) and a nested `a/b/c` never hits a missing parent; **directory add/add is
not a content conflict** (directories carry no digest, §7.2),
so only file/symlink leaf paths participate in add/add conflict detection and independent
files under a shared newly-added directory merge cleanly (divergent captured *mode* bits on
the same added directory resolve last-writer-wins — the one mode dimension exempted from I9,
since directory permissions are rarely plan-significant); **(2)** apply file/symlink add &
modify — byte-copy each file then `os.chmod` it to the **change-set's** recorded post-edit
`mode` (re-stat'd from the edited sandbox during §7.3 change detection; the §7.2 manifest is
only the *pre-edit* comparison baseline) — under `umask(0o077)` a freshly
`open(dst, "wb")`-created file is `0600`, so the explicit chmod carries captured bits such as
the executable bit (§7.2/§2); recreate symlinks via
`os.symlink`); **(3)** apply mode-changes; **(4)** apply deletions of files/symlinks
(missing-path-tolerant — guard with `os.path.lexists` or ignore `FileNotFoundError`, so a path
already removed by a sibling's type-flip `rmtree` or an earlier deletion never raises), then run
a single **wave-end** prune pass — visiting candidates **deepest-first** (descending path
depth, so a nested `a/b/c` is removed before `a/b`) — removing only directories whose **live
on-disk** contents are empty at prune time (`os.listdir(d) == []` → `os.rmdir`) **and that are
not an intentionally-added directory** (a `dir` add in any merged change-set, §7.2 — a
deliberate empty dir is kept), *after* all of the wave's adds/modifies and deletions are
applied — so a directory a sibling repopulated is never pruned; never auto-prune a directory
that still holds content another plan kept. **Remove-before-recreate.** `os.symlink`/`os.mkdir` have no
overwrite mode and raise `FileExistsError` when the path exists, so any existing node must be
removed (`os.unlink` for a file/symlink, `shutil.rmtree` for a directory) immediately before
recreation in two cases: **(a)** a **symlink add/modify** — a same-kind retarget (§7.3) must
`os.unlink(dst)` then `os.symlink`, since a link cannot be rewritten in place; and **(b)** a
**type-flip** (§7.3 — a path whose kind changed, applied as delete-old-kind + add-new-kind).
(Regular-file modify is unaffected — a byte-copy via `open(dst, "wb")` truncates in place.)

**Overlap detection is cumulative across the whole run, not just the current wave.** The
orchestrator maintains a writer map `path → first-writing-plan` over **all merged waves**.
A path is a **conflict** when two plans touch it in any way and neither is the other's
transitive dependency:

- Same-wave siblings touching the same path (modify/modify, modify/delete, delete/add, and
  **identical-content add/add** — an intentional conflict, never silently auto-merged).
  **delete/delete is the sole exempted case**: both plans agree on the terminal state
  "absent", so it auto-merges to one deletion (idempotent, not a conflict).
- A later-wave plan that is **not** a transitive dependent of the earlier writer touching a
  path an earlier merged plan already wrote (prevents silent last-writer-wins clobbering).
- **Directory-prefix relations:** a deleted directory in one plan conflicts with any
  add/modify *under* that directory by a non-dependent plan.

A legitimate build-on-predecessor change (a plan modifying a path written by its transitive
dependency) is **not** a conflict — the dependency edge already serializes them.

**Conflict-resolution state machine (never auto-resolve content):** on conflict, mark all
contributing plans `merge_status=conflicted` and apply none of the conflicting paths' changes
this wave. In the re-plan, the orchestrator picks exactly **one winner per conflicting pair**
— a *single consistent ordering* for that pair across **all** their conflicting paths (a
multi-path conflict between the same two plans yields one winner, never opposing edges),
tie-broken by narrower/owning `file_scope` then lower `id` — **merges the winner's** change,
and adds `depends_on(loser → winner)` so each loser **re-runs** in a later wave against the
winner's now-merged baseline (not "apply none to both forever"). **Conflict edges must never
introduce a cycle:** if the reverse edge already exists from an earlier conflict, keep the
existing order rather than adding the opposing edge. A
per-conflict re-plan counter bounds retries; the conflict is also recorded in
`conflict_fingerprint.json` so run-level non-progress (§6.7) can stop the run honestly as
`incomplete` if the same conflict set persists. Non-conflicting changes from the same plans
are merged normally.

### §7.5 Relationship to git-deny

Directory-copy needs **no** git operations by the orchestrator, so there is no
worktree-add race and nothing for gitguard to self-flag. The Claude stages (Planner,
Evaluator) run against `target_dir` (which may be a git repo) and keep the git-deny guard
(§9); the Codex Generator runs in a non-git sandbox and cannot commit at all.

---

## §8. SDK seams (the crux)

Two thin **Protocol seams** are the *only* places the SDKs are imported. The orchestrator
depends on the Protocols; tests substitute fakes. This both isolates drift and gives the
"single mocking point." All shapes below are verified against the installed source; every
Python-SDK shape is **beta-volatile** (§8.4).

### §8.1 Claude seam — `drivers/_claude.py` (claude-agent-sdk)

Verified against installed `claude-agent-sdk 0.2.137` (satisfies `>=0.1.74,<1`; shapes hold
across the installed 0.2.x line).

**Options builder** — one chokepoint for every Claude call:

```python
def build_options(*, skills="all", setting_sources=("user","project","local"), system: str,
                  output_format: dict | None = None, cwd: Path | None = None,
                  add_dirs=None, disallowed_tools=(), cli_path: Path | None = None,
                  hooks: dict | None = None, stderr=None) -> "ClaudeAgentOptions":
    """Build ClaudeAgentOptions for a fresh-session Claude turn.

    Design: §8.1 a single chokepoint prevents option drift. The claude_code TOOL preset
        and the system prompt are INDEPENDENT fields — selecting the tools preset does not
        set a system prompt, so `system` is passed separately as the `system_prompt`.
    Implementation: ClaudeAgentOptions(mcp_servers={}, strict_mcp_config=True,
        permission_mode="bypassPermissions",
        tools={"type":"preset","preset":"claude_code"}, system_prompt=system,
        setting_sources=list(setting_sources), ...). output_format is idempotently
        enveloped to {"type":"json_schema","schema":<bare>}; a bare schema is dropped by
        the transport, so wrapping is required. `stderr` is a Callable[[str],None] teeing
        subprocess stderr to run.log fail-soft. `hooks` carries the git-deny matcher.
    Example: build_options(system=PLANNER_SYSTEM, output_format=PLANSET_SCHEMA, cwd=repo).
    """
```

Verified facts (the exact field SET / enum members carry `[verify-against-installed]`; the
load-bearing ones below are confirmed in 0.2.137):

- `permission_mode="bypassPermissions"` is a valid `PermissionMode`.
- The claude_code tool capability set is `tools={"type":"preset","preset":"claude_code"}`
  — the `tools` field, **distinct** from `allowed_tools` (which only auto-approves a list,
  and is *not* how the preset is selected).
- `system_prompt` is an independent field (plain string here).
- `mcp_servers={}` passes no explicit servers and `strict_mcp_config=True` excludes MCP
  servers from settings, project files, and plugins while leaving skill settings enabled.
- **Skills are enabled via the dedicated `skills` option** (`skills="all"`, or
  `skills=["superpowers:writing-plans", …]`) — the single switch that turns skills on; the SDK
  then auto-adds the `Skill` tool and the needed `setting_sources`. `setting_sources`
  (`["user","project","local"]`; `[]` = isolation) *separately* controls filesystem settings
  (`settings.json`, plus `CLAUDE.md` when it includes `"project"`) and does **not** by itself
  enable skills in this SDK line. `[verify-against-installed]` (the dedicated `skills` option
  exists in 0.2.137; older lines loaded skills via `setting_sources` only).
- `output_format` must be the envelope `{"type":"json_schema","schema":<bare>}`.
- `cwd`, `add_dirs`, `disallowed_tools`, `cli_path`, `stderr` are valid fields.

**Fresh-session single turn** — use `ClaudeSDKClient`, not the one-shot `query()`, because
the design wants `interrupt()` and early-break teardown:

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query(prompt=prompt)
    async for msg in client.receive_response():   # yields up to & incl. ResultMessage, then stops
        absorb_session_id(msg); messages.append(msg)
```

- **Use `receive_response()`, never `receive_messages()`** for a single-turn drain:
  `receive_messages()` does not stop at `ResultMessage` (it continues indefinitely without
  a stop predicate), so a hand-rolled drain blocks.
- **Structured output drains from `ResultMessage.structured_output`** (a dict / `Any`),
  *not* a `StructuredOutput` tool-use block. Keep the last non-`None` `structured_output`
  dict and concatenate `TextBlock.text`. Defensively `isinstance`-check for `dict`.
- **`session_id` is captured from the init `SystemMessage` at `msg.data["session_id"]`**
  (subtype `"init"`). The `SystemMessage` dataclass has only `subtype` and `data` — there
  is no top-level `session_id` on it (`ResultMessage`/`AssistantMessage` carry their own).
  Capture is forensic and fail-soft. `[verify-against-installed]` the init payload keys.
- **Transient-error classification:** `ConnectionError | BrokenPipeError` OR the
  lazily-imported `CLIConnectionError`. Bare `OSError` is deliberately excluded
  (filesystem/permission faults must propagate); **`TimeoutError` is deliberately NOT
  transient** — it is always re-raised untouched (with `CancelledError`) so the §4.1 runtime
  cap and cancellation propagate. (The harness assumes `requires-python>=3.11`, where
  `asyncio.TimeoutError` *is* the builtin `TimeoutError` — so treating it as retryable would
  swallow the cap.) Schema-bearing calls retry exactly once on schema mismatch with a pinned
  suffix; transient faults retry ≤3 with backoff.
- **`interrupt()`** exists only on `ClaudeSDKClient`, requires streaming mode + active
  consumption, and raises `CLIConnectionError` otherwise; call it best-effort
  (getattr-guarded, exception-swallowing). It is opportunistic — the async-with `__aexit__`
  is what actually aborts a turn.

**Git-deny PreToolUse hook** (§9):

```python
def git_deny_hooks() -> dict:
    """Build the PreToolUse hook map denying git-mutating Bash commands.

    Design: §9 deterministic in-band guard for Claude phases, layered atop the prompt
        forbid and post-iteration gitguard. Defense-in-depth, NOT a sufficient standalone
        guard (a simple command matcher can miss git mutations buried in '&&'-chains or
        alternate spellings like 'git checkout -b' — the hook sees the full command string,
        the matcher is the limit).
    Implementation: {"PreToolUse": [HookMatcher(matcher="Bash", hooks=[deny_cb])]}; the
        async deny_cb(input_data, tool_use_id, context) reads input_data["tool_name"] and
        input_data["tool_input"]["command"], returns {} to pass and the deny envelope to
        block.
    Example: build_options(hooks=git_deny_hooks(), ...).
    """
```

The deny return shape is
`{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":<str>}}`;
`{}` passes. `[verify-against-installed]` the hook envelope keys **and** their protocol
enum values (`hookEventName`/`permissionDecision`).

### §8.2 Codex seam — `drivers/_codex.py` (openai-codex)

Verified against the cached wheel `openai-codex 0.1.0b2` (satisfies `>=0.1.0b2`). **Every
shape here is beta-volatile and MUST be re-introspected (§8.4); do not copy the older
`AppServerConfig` / `sandbox_policy=` shapes from any prior brief — those describe the OLDER
`0.131.0a4` line and fail against this pin. Identify the right wheel by its
`openai_codex-0.1.0b2.dist-info`: in `0.1.0b2` the config class is `CodexConfig`, the sandbox
enum is `Sandbox` (`full_access` → wire `danger-full-access`), the never-ask approval member
is `ApprovalMode.deny_all` (→ wire `never`), `TransportClosedError` is a real class in
`openai_codex.errors`, and `turn()` exposes no public `sandbox_policy=` (it is computed
internally). **context7 does NOT index the `openai_codex` Python SDK** (only the Codex
CLI/product), so its *wire/CLI* names (`danger-full-access`, `never`) differ from the Python
member names used here — confirm the Python symbols by introspecting the wheel, not context7.
A uv cache commonly holds many `0.131.0a4` copies and only a few `0.1.0b2` ones, so introspect
the version that satisfies the pin — not whichever copy is found first.**

```python
def build_codex_config(*, codex_bin: str, cwd: Path, env: dict | None = None):
    """Construct the Codex launch config.

    Design: §8.2 the launch config class is `CodexConfig` (imported from openai_codex /
        openai_codex.client). `AppServerConfig` was an OLDER name — do not use it.
    Implementation: CodexConfig(codex_bin=codex_bin, cwd=str(cwd), env=env or {}).
    Example: build_codex_config(codex_bin="codex", cwd=sandbox).
    """

async def generate(*, instructions: str, config, run_log_path: Path | None = None):
    """Run one full-access Codex turn, streaming events.

    Design: §5.2/§8.2 unconditional full host access; the never-ask approval mode is
        non-interactive.
    Implementation: codex = AsyncCodex(config=config); install the stderr tee BEFORE
        __aenter__; await codex.__aenter__(); thread = await codex.thread_start(
            sandbox=Sandbox.full_access,            # typed enum preset (wire 'full-access')
            approval_mode=ApprovalMode.deny_all,    # MUST pass explicitly; default is auto_review
            cwd=str(config.cwd));
        handle = await thread.turn(TextInput(text=instructions), cwd=str(config.cwd),
            approval_mode=ApprovalMode.deny_all);
        async for ev in handle.stream(): yield CodexEvent(kind=ev.method, payload=_dump(ev.payload));
        close codex idempotently on stream exhaustion / error.
    Example: async for ev in generate(instructions=contract, config=cfg): forward(ev).
    """
```

Verified facts:

- `CodexConfig(codex_bin, cwd, env)`; `AsyncCodex(config=…)` is an async context manager
  with an explicit `close()`.
- **`thread_start(sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all, cwd=…)`
  — the launch config is NOT re-passed here (it is supplied via `AsyncCodex(config=…)`;
  `thread_start` does expose an optional per-thread `config=` JSON override that forge-mcp does
  not use).** `Sandbox.full_access` is the typed enum (its enum *value* is `'full-access'`; it
  serializes to the **wire sandbox mode `'danger-full-access'`**, matching the §8.2 intro —
  pass the **typed enum only**, never a config-override string, to avoid that namespace trap).
- **`ApprovalMode.deny_all` must be passed explicitly** — `thread_start` defaults to
  `ApprovalMode.auto_review` (which inserts an automated reviewer). `deny_all` means
  *never ask* (fully non-interactive), the correct autonomous setting; `auto_review` (the
  default) must be overridden. `[verify-against-installed]` re-introspect the exact enum
  membership and the never-ask member; do not rely on the member count.
- `thread.turn(TextInput(text=…), cwd=…, approval_mode=…)` — `input` positional, the rest
  keyword-only; **no `sandbox_policy=` in this pin.** Returns an `AsyncTurnHandle` whose
  `.stream()` yields `Notification` objects with `.method: str` and `.payload` (a pydantic
  model — `model_dump()` — or a fallback dataclass; use a `model_dump → dict → {}` triple
  fallback).
- `AsyncThread.id` / `AsyncTurnHandle.id` are `str`; expose `last_thread_id` fail-soft and
  forensic-only.
- **Transient classification:** `ConnectionError | BrokenPipeError` OR the
  lazily-imported `TransportClosedError` OR `is_retryable_error(exc)` (True for
  `ServerBusyError` and overload-tagged `JsonRpcError`); bare `OSError` excluded, and
  `TimeoutError`/`CancelledError` always re-raised untouched (§8.1 — preserves the runtime cap).
- **stderr tee** (forensic): swap `codex._client._sync._stderr_lines` (a bounded deque,
  observed `maxlen=400`) for a teeing deque **before** `__aenter__`, guarded by
  `try/except AttributeError → warn-and-continue`. This is a private, undocumented
  3-object attribute chain — **the single most drift-fragile fact**; it must degrade to
  no-tee, never crash. `[verify-against-installed]` (covers the attribute chain and the
  exact maxlen).

Official Codex docs confirm the policy: `danger-full-access` + never-approval is the
documented full-autonomy pairing, valid where the deployer already provides process
isolation — forge-mcp's documented posture.

### §8.3 Protocol seams

```python
@runtime_checkable
class ClaudeRunner(Protocol):
    """Seam for the Claude SDK; concrete drivers depend on this, never on SDK imports."""
    last_session_id: str | None
    async def run(self, *, prompt: str, options) -> "StructuredResult": ...   # system is inside options (§8.1)
    async def interrupt(self) -> None: ...
    async def aclose(self) -> None: ...

@runtime_checkable
class CodexRunner(Protocol):
    """Seam for the Codex SDK; one streaming `generate`, fail-soft `last_thread_id`."""
    @property
    def last_thread_id(self) -> str | None: ...
    def generate(self, *, instructions: str, config, run_log_path=None) -> "AsyncIterator": ...
    async def interrupt(self) -> None: ...
    async def aclose(self) -> None: ...
```

### §8.4 SDK drift discipline (the lesson made structural)

The reference's tests once modeled *assumed* SDK shapes while runtime was broken
end-to-end, because the only real-SDK test was excluded from default CI. This design makes
that failure impossible to repeat:

1. The seams (§8.1/§8.2) are the **sole** SDK import sites.
2. A **`tests/test_sdk_contract.py` introspects the *installed* SDK** and asserts the
   exact symbols / kwargs / enum members the seams use actually exist (e.g.
   `inspect.signature(AsyncCodex.thread_start)` has `sandbox` & `approval_mode`;
   `Sandbox.full_access`; `ApprovalMode.deny_all`; `ResultMessage.structured_output`;
   `HookMatcher`; `CLIConnectionError`). It runs whenever the SDK is importable — **not**
   gated as slow.
3. The implementing agent **MUST re-introspect** `claude-agent-sdk` and `openai-codex`
   against the actually-installed versions before finalizing the seams
   (`python -c "import openai_codex, inspect; print(openai_codex.__version__);
   print(inspect.signature(openai_codex.AsyncCodex.thread_start))"`, etc.). Treat a
   reasonable installed-version equivalent as conformant, not a gap.

---

## §9. Git-commit denial

No stage runs `git commit/push/branch/tag/rebase/reset/worktree`. Enforced in layers:

- **Claude stages (Planner, Evaluator)** run in `target_dir` (possibly a git repo) and get
  **all three** layers: (1) prompt forbid; (2) the deterministic `PreToolUse` git-deny
  hook (§8.1) — defense-in-depth, not sufficient alone (a simple matcher can miss git
  mutations buried in `&&`-chains or alternate spellings like `git checkout -b`; the hook sees
  the full command string, the matcher is the limit); (3) a post-iteration **gitguard**
  read-only snapshot diff.
- **Codex Generator** runs in a **non-git directory-copy sandbox**, so `git commit` is
  *structurally impossible* there — the prompt forbid remains as defense-in-depth.
- **gitguard is read-only.** `capture_state()` snapshots the repo's mutable git surface —
  the current commit (`HEAD`), branch and tag refs, and the worktree set — as a comparable
  string (None if not a git repo); `diff_state(base, end)` is non-empty iff any of these
  changed. `[verify-against-installed]` the exact porcelain commands and section format are
  operational; any capture that detects commit/branch/tag/worktree mutation is conformant.
  `_run_git` uses `subprocess` with a short timeout, no shell, `check=False` (and catches
  `subprocess.TimeoutExpired`, which `timeout=` raises, treating it as a non-mutating error
  outcome rather than letting it propagate). On a
  non-empty diff the orchestrator synthesizes a high-severity gap and writes
  `iteration-N/git-violation.txt`. `[verify-against-installed]` the exact gap `title` is an
  internal label; any stable, unique high-severity title identifying a git-mutation
  violation is conformant — what is normative is that a non-empty diff synthesizes a
  high-severity gap before fingerprinting (I6, §6.5).
- **Merge is commit-resilient.** Because the merge (§7.4) reads sandbox *files*, not
  commits, even a hypothetical stray commit cannot corrupt the result.

---

## §10. Skills wiring & `forge check`

### §10.1 How each engine discovers skills

- **Claude (Planner, Evaluator):** skills are enabled because `build_options` passes the
  dedicated `skills` option (`skills="all"`, or the specific `plugin:skill` ids), which the SDK
  uses to add the `Skill` tool and load the matching `CLAUDE_CONFIG_DIR` plugin skills; the
  prompt then **names** the skill to invoke. (`setting_sources` separately loads
  `settings.json`/`CLAUDE.md`.) `[verify-against-installed]`.
- **Codex (Generator):** there is **no `Skill` tool**. Codex auto-discovers skills from
  plugins declared in `~/.codex/config.toml` and `~/.codex/skills/` and "just follows the
  instructions"; project guidance reaches Codex only via `AGENTS.md`/`AGENTS.override.md`
  (never `CLAUDE.md`). The Generator prompt therefore references the skill by
  capability/behavior, not "invoke the Skill tool," and the orchestrator warns
  (non-fatal) when `target_dir` lacks `AGENTS.md`.

### §10.2 The skills matrix (fully wired in-scope)

| Stage | Engine | Capability | Candidate skill id `[verify-against-installed]` |
|---|---|---|---|
| Planner | Claude | write plans | `superpowers:writing-plans` |
| Generator (backend) | Codex | execute plans | `superpowers:executing-plans` (Codex cache) |
| Generator (frontend) | Codex | frontend design | `frontend-design:frontend-design` |
| Evaluator | Claude | code review | `code-review:code-review` |
| Evaluator (remediation) | Claude | write plans | `superpowers:writing-plans` |

Wiring each invocation requires **both halves**: the prompt names/uses the skill **and**
the engine can discover it. Skill ids are plugin- and **engine-specific** (the same
capability has different ids on Claude vs Codex; the superpowers Codex plugin exposes
`requesting-code-review`/`receiving-code-review`, not `code-review:code-review`), so each
id above is a `[verify-against-installed]` **candidate** referenced by capability, never a
hard-pinned cross-engine literal.

### §10.3 `forge check` per-engine skill probe

- **Claude side (deterministic):** open a real Claude SDK session with the project
  `setting_sources`, issue a minimal query, and read the init `SystemMessage`'s
  `data["skills"]` list; `FAIL` if a required Claude skill (`writing-plans`, the
  code-review skill) is absent. Bounded by a timeout. `[verify-against-installed]` that the
  init payload still carries `skills`.
- **Codex side (NEW):** there is no `data["skills"]` equivalent, so probe Codex
  discoverability separately — inspect the `~/.codex` plugin cache / `~/.codex/skills/` for
  the required ids (plan-execution, frontend-design), and run a `codex --version` smoke.
  `FAIL` if a required Codex skill is not discoverable. **Verification found
  `frontend-design` and the `executing-plans` (plan-execution) skill are not guaranteed in
  the current Codex superpowers cache** — so this probe will legitimately `FAIL` until the
  operator installs them (that is operator runtime state, not deferred implementation);
  `check` reports the remediation. (`code-review` is a Claude-side skill, probed only on the
  Claude side above — never required on Codex.) Bounded by a timeout.

### §10.4 Configuration & environment (fallbacks)

- `CLAUDE_CONFIG_DIR` → default `~/.claude`.
- `FORGE_CLAUDE_BIN` → else `shutil.which("claude")` → else `~/.local/bin/claude`;
  resolved absolute and threaded into `build_options(cli_path=…)`.
- `FORGE_CODEX_BIN` → else `shutil.which("codex")` → else `~/.npm-global/bin/codex`;
  threaded into `CodexConfig(codex_bin=…)`.

`[verify-against-installed]` the exact env-var names and default paths.

---

## §11. Artifacts layout

```
<target_dir>/.harness/
  .gitignore                                  # self-ignoring '*', created O_EXCL (§7.2)
  run.lock                                    # per-target lock marker (§12); payload in-content (opt a) or in run.lock.meta (opt b)
  run.lock.meta                               # option-(b) SoftFileLock payload sidecar (optional; §12)
  <YYYYMMDDHHMMSS>/                           # run dir (timestamp; §12)
    state.json                                # run-level, single-writer, dir-fsync (§13)
    run.log                                   # teed SDK stderr; private (never in RunResult)
    conflict_fingerprint.json                 # run-level conflict history (D3, §6.7)
    inputs/
      design.md                               # IMMUTABLE original (provenance)
      design.fingerprint                      # content hash of design.md
    spec.md                                   # orchestrator-owned working copy (amended at boundaries)
    spec_amendments.md                        # append-only audit log (orchestrator sole writer)
    spec.fingerprint                          # bumped on each amendment
    planset.json  plan-<id>.md                # Planner output (the DAG)
    plans/<id>/
      sandbox/                                # directory-copy sandbox (no .git)
      manifest.json                           # change-detection baseline (kind + per-kind digest/mode; §7.2)
      state.json                              # per-plan, single-writer, dir-fsync
      iteration-N/
        contract.md  summary.md
        eval.json  triage.json                # triage.json carries any proposed_amendment
        gap_fingerprint.json                  # sorted ["title|severity"] (D3 sidecar)
        verify.txt  git-violation.txt
      merge.json                              # change-set + merge_status + conflicts
```

Run-level and per-plan `state.json` **and** the shared run-level `spec.md` /
`spec_amendments.md` use the durable writers (§13); ordinary iteration artifacts use the
lighter fd-fsync writer. The whole run sets
`os.umask(0o077)` and creates dirs at `0700` (restored in `finally`).

---

## §12. Run-dir naming

Run dirs are `<target_dir>/.harness/<YYYYMMDDHHMMSS>/` (the requirement's example
`202606231831` is minute-precision; this design uses second precision to reduce collisions).
The timestamp shape MUST be threaded **consistently** wherever a run-id is used: the run-id
matcher/validator (regex `^\d{14}(-\d{2})?$`), the `RunState`/`RunResult` `run_dir` field,
the per-target lock payload, `create_run_dir`, and any old-run pruning filter. (The
reference used an 8-hex id `^[0-9a-f]{8}$`; switching to a timestamp means *all* of those
must use the timestamp shape together — a mismatched pruning filter would silently never
prune.) Collisions are prevented by the per-target lock (one run per `target_dir` at a
time); on the rare same-second re-run, append a `-NN` uniquifier.
`[verify-against-installed]` the exact timestamp format string, the same-second uniquifier
shape, and the lock-payload field set; the normative requirements are second-precision
run-ids threaded consistently across all run-id uses, per-target serialization, and
PID-reuse-safe stale recovery (which the lock payload must carry enough state to perform).

A per-target lock (`<target_dir>/.harness/run.lock`) serializes runs per target, using a
**persistent marker that survives a `kill -9`** (D2) — **not** OS-level `filelock.FileLock`,
whose lock auto-releases on holder death and carries no payload. Two realizations, pick one:
**(a)** a hand-rolled `os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)` marker
(atomically exclusive-created and writable — then `os.write` the payload, fsync, close) whose
**file content is** the JSON
payload `{pid, run_id, started_at, target_dir, create_time}` (where `create_time` is the
holder's process-start time via `psutil.Process(pid).create_time()` — add `psutil` to
dependencies; the PID-reuse guard degrades to pid-liveness where it is unavailable), with the
liveness-first stale recovery below hand-rolled; or **(b)** `filelock.SoftFileLock`, which owns its own PID
lock-file format (exposing the holder via `lock.pid`) and performs stale cleanup on acquire
(PID-reuse-safe only on Windows, where it stores process creation time; **pid-liveness-based
on POSIX** via `kill(pid,0)`) — in which case the extra payload fields live in a **separate
sidecar** (`run.lock.meta`), not as the lock file's content, and the design accepts the
library's built-in recovery (on POSIX, pid-liveness recovery is the conformant equivalent;
prefer option (a) if strict PID-reuse-safety on POSIX is required). Either way, the marker's persistence after a crash is what makes
recovery implementable. Stale recovery (for the hand-rolled path) is
**liveness-first**: it steals only on an unreadable payload, a dead pid, or a `create_time`
mismatch (PID-reuse-safe). **The steal is race-free:** the steal critical section is serialized
by an `O_CREAT|O_EXCL` guard (`run.lock.steal`) — a stealer acquires it, re-confirms staleness,
atomically replaces `run.lock` by `os.replace`-ing a temp payload into place (atomic
replace-over-existing on POSIX and Windows, matching §13's durable writer), then removes the
guard (which carries its own `{pid, create_time}` so a crashed stealer cannot deadlock it — on
`FileExistsError`, apply the same liveness-first check to the *guard* and steal it only if its
holder is dead / `create_time`-mismatched, else back off and re-read `run.lock` rather than
unlinking it), so at most one recoverer ever replaces the marker. It must **never** steal a lock held by a live, healthy run within
its runtime budget — so a bare mtime threshold is **not** an independent steal trigger; if
used at all (a fallback when the pid cannot be probed), the threshold MUST exceed
`max_runtime_minutes`, since the lock is acquired once and not heartbeated. `release()` is
idempotent. **No `adopt_run_id`** (no resume).

---

## §13. Durable state & atomic writes

Three write modes, all single-writer (Invariant I1):

- **Durable whole-file replace (`state.json`, run-level and per-plan; `spec.md`):**
  write to a same-dir tempfile → `flush()` → `os.fsync(fd)` → `chmod 0600` (POSIX) →
  `os.replace(tmp, path)` → open parent dir and `os.fsync(dir_fd)` (best-effort, swallow
  `OSError`). The parent-dir fsync makes the rename itself crash-durable. `RunState`/per-plan
  state are Pydantic `extra="forbid"`. Every transition: `model_dump → update fields → set
  last_updated_at → model_validate → write`. (`state.json`/`spec.md` are whole-state files, so
  replacing the whole file is correct.)
- **Durable append (`spec_amendments.md`):** the append-only audit log MUST NOT use
  whole-file replace — `os.replace` overwrites the destination wholesale, so a tempfile
  holding only the new entry would replace the prior log wholesale, discarding earlier entries
  (violating I3). Instead append in
  place: `open(path, "a")` → write the entry → `flush()` → `os.fsync(fd)` (the standard
  durable-append pattern), with the file first created durably (parent-dir fsync once). This
  preserves both I2 (durable) and I3 (append-only).
- **Light tier (ordinary artifacts):** same tempfile + fd-fsync + `chmod` + `os.replace`,
  **without** the parent-dir fsync. All `*.fingerprint` files (`spec.fingerprint`,
  `design.fingerprint`) and `*_fingerprint.json` sidecars (`conflict_fingerprint.json`,
  per-iteration `gap_fingerprint.json`) use this tier and carry **no resume invariant** — they
  need not be crash-atomic with `spec.md`/`design.md`, since they are forensic/observability
  state or consumed only **in-run** by the live orchestrator (§6.7), never a cross-run resume
  anchor (D2/§6.6).

"Crash-safe" means best-effort POSIX durability (the dir fsync is itself wrapped
`try/except OSError`), not a hard guarantee on all filesystems — the doc does not over-claim.

---

## §14. Schemas

Pydantic models (code-derived JSON schema): `RunForgeInput`, `RunResult`, `GapSummary`
(§4.3); `PlanSet`/`Plan` (§5.1); `EvalResult`/`EvalGap` (§5.3); `TriageResult`/`GapTriage`
(§5.3). For Claude structured output, the *bare* schema is enveloped as
`{"type":"json_schema","schema":<bare>}` (§8.1); `additionalProperties:false` on gap/triage
rows; **no top-level combinators** in the Claude structured-output schemas (this constraint
is specific to that channel). Separately, `RunForgeInput`'s path/content xor lives in a
`model_validator` rather than its schema — a cross-field xor is naturally a validator, and
the FastMCP-derived MCP tool-input schema has no such combinator restriction.
`EvalGap.title` is whitespace-canonicalized for stable triage joins; `GapTriage`'s
validator enforces "design_fault ⇒ fault_kind set ∧ non-empty cited_sections" and coerces a
stray `fault_kind` to `None` when `design_fault` is false.

**Anchor vs internal contract:** the Pydantic field **names** of `RunForgeInput` /
`RunResult` — including the nested `GapSummary` rows of `unresolved_gaps` (`{title,
severity, design_doc_section}`) — are load-bearing schema anchors (must match exactly, per
§17). The internal `EvalResult` / `TriageResult` / `Plan` row field names are an internal
contract where semantically-equivalent renames are conformant `[verify-against-installed]`.

---

## §15. Module structure

A focused set (Core + durability; not the reference's ~50 files):

```
src/forge_mcp/
  __init__.py  __main__.py
  cli.py                      # typer: `forge serve`, `forge check`
  server.py                   # FastMCP instance + run_forge tool (§4.1)
  config.py                   # env + fallbacks (§10.4); run-dir naming (§12)
  models.py                   # RunForgeInput, RunResult, Plan/PlanSet, Eval/Triage (§14)
  check.py                    # the `forge check` / preflight check set (§10.3, §4.4)
  skills.py                   # per-engine skill discovery probes (§10.3)
  gitguard.py                 # read-only git-mutation detector (§9)
  sandbox.py                  # directory-copy isolation, manifest, change-detect, merge (§7)
  verifier.py                 # deterministic verification gate, per-plan + run-level (§6.5)
  state.py                    # durable single-writer atomic writers (§13)
  artifacts.py                # run-dir layout + light atomic writers (§11)
  ids.py                      # run-dir/timestamp matcher (§12)
  lockfile.py                 # per-target lock, no adoption (§12)
  convergence.py              # PURE non-progress policy, per-plan + run-level (§6.7)
  triage.py                   # PURE gap classification + citation gate (§5.3)
  schemas/                    # json_schema envelopes for structured output
  prompts/                    # planner_system.md, generator_system.md,
                              #   evaluator_system.md, evaluator_triage.md, remediation.md
  drivers/
    _claude.py  _codex.py     # the two SDK seams (§8)
    planner.py generator.py evaluator.py    # the three stages
  orchestrator/
    engine.py                 # thin conductor: lock→plan→[wave: schedule→execute→merge→amend]→verify→finalize
    scheduler.py              # DAG waves, bounded concurrency, gather(return_exceptions=True), failure isolation (§7.1)
    phases.py                 # the per-plan iteration loop (§3.2)
    statemachine.py           # run-level state (§3.3)
    plan_state.py             # per-plan durable state (§3.3)
    amend.py                  # orchestrator-owned spec amendment at wave boundaries (§5.3)
    lifecycle.py              # terminal honesty: completed/incomplete/failed cleanup (§6.4)
tests/  scripts/check_docstrings.py  pyproject.toml
```

Reused **as-is** from the reference's patterns (re-implemented, not copied): `convergence`
(pure), `triage` (pure), `gitguard` (read-only), `verifier`, the atomic writers. **Dropped
entirely:** `resume.py`, lineage/cross-design, resource/subscription surface, the MCP task
context. **Net-new:** `sandbox.py`, `scheduler.py`, `plan_state.py`, `amend.py`, the
run-level conflict-/amendment-churn fingerprint policy, the Codex skill probe.

---

## §16. Code conventions

- **Rule-21 three-section docstring on every function/method:** `Design:` (why / which §),
  `Implementation:` (how), `Example:` (one concrete call). Enforced by
  `scripts/check_docstrings.py` in CI (the §8 sketches above are the house style).
- **ruff** (`E,F,I,B,UP,ASYNC`, line-length 100) + **ruff-format** + **pyright** (basic).
- `from __future__ import annotations`; lazy SDK imports inside the seams so module-import
  smoke tests don't require the SDKs.

---

## §17. Testing strategy

- **Protocol fakes** for `ClaudeRunner`/`CodexRunner` — unit-test the orchestrator, stages,
  triage, convergence, sandbox merge without the SDKs.
- **Pure-policy unit tests** for `convergence` (per-plan and run-level window/EARLY_STOP/
  NUDGE) and `triage` (collision demotion, citation gate, design-fault coercion).
- **`sandbox` tests:** manifest diff for **files** (add/modify/delete/mode/binary),
  **symlinks** (add/modify/retarget/delete/dangling/type-flip), and **directories**
  (added-empty/deleted/dir-mode); change-kind merge ordering; cumulative cross-wave overlap
  and dir-prefix overlap → conflict (never auto-resolved); the conflict-resolution state
  machine (winner/loser re-run).
- **Scheduler tests:** failure isolation (a raising plan does not cancel siblings under
  `gather(return_exceptions=True)`); lazy post-merge sandbox creation.
- **Amend tests:** the orchestrator re-runs the citation gate against the then-current
  `spec.md`, applies/appends the amendment, bumps the spec fingerprint, and invalidates plans
  whose `file_scope`/`surface` intersects an amended section (re-plan/re-run next wave)
  (§5.3, I4).
- **Verifier tests:** exit-0 → passed; non-zero exit → not passed (no exception, `check=False`);
  timeout → `passed=False`/`timed_out=True` (`subprocess.TimeoutExpired` caught);
  `last_verification` cached and read at completion (not re-run); the run-level post-merge gate
  over `target_dir` feeds `RunResult.verified` (§6.5).
- **Schema-pin tests** on `RunForgeInput`/`RunResult` JSON schema (the Evaluator's diff
  anchor), including the spread tool-input params and nested `GapSummary` (§4.3/§14).
- **`test_sdk_contract.py`** — introspects the *installed* SDKs (§8.4); runs whenever
  importable, **not** slow-gated.
- **`@slow` real-CLI e2e** — one end-to-end run against the real `claude`/`codex` binaries,
  excluded from default CI.

---

## §18. Invariants (normative)

- **I1 — Single writer per durable/shared file.** Run-level `state.json` is written only by
  the orchestrator; each per-plan `state.json` only by that plan's context; the shared
  run-level files `spec.md` and `spec_amendments.md` only by the orchestrator. Never two
  writers.
- **I2 — Durable writes.** `state.json` and `spec.md` use the durable whole-file replace
  (fd-fsync + parent-dir fsync); `spec_amendments.md` uses durable append (fd-fsync per
  entry; parent-dir fsync once at creation) — never whole-file replace, which would truncate
  the log (§13).
- **I3 — Append-only audit.** `iteration-N/` dirs and `spec_amendments.md` are append-only;
  the orchestrator appends to `spec_amendments.md` serially at wave boundaries (no
  concurrent appends).
- **I4 — Immutable input, orchestrator-owned spec.** `inputs/design.md` is never modified.
  The per-plan Evaluator only **proposes** amendments (into `triage.json`); the
  **orchestrator commits** them to `spec.md` at wave boundaries. Within a wave `spec.md` is
  frozen and read-only to plans.
- **I5 — No stage commits.** No Planner/Generator/Evaluator action runs
  `git commit/push/branch/tag/rebase/reset/worktree` (§9). The orchestrator does no git
  mutation either (directory-copy needs none).
- **I6 — Completion is current-iteration, fully-guarded.** A plan is `completed` ⇒ no
  remaining current-iteration code-bug gaps (over the *full post-synthesize* set) ∧ (`no_gaps`
  ∨ triage ran) ∧ verification passed-or-absent ∧ no git violation this iteration ∧ no pending
  design-fault amendment (§6.5). The run is `completed` ⇒ all plans completed ∧ the run-level
  post-merge verification passed-or-absent (with `verified` reflecting whether it actually ran).
- **I7 — Honest non-convergence, two scopes.** Any per-plan cap/non-progress, run-level
  conflict **or amendment-churn** non-progress, or runtime-cap stop ⇒ `status="incomplete"`
  with `unresolved_gaps` =
  the union over all non-completed plans of each plan's highest-iteration full post-synthesize
  gap set (eval.json gaps ∪ synthesized git/verify gaps, §6.5; ∪ a synthesized failure gap for
  any `failed` plan with no gap set, §6.4), and a `stop_reason`.
- **I8 — Per-plan failure isolation.** One plan's failure never aborts siblings: each plan
  coroutine self-contains exceptions; the fan-out uses `gather(return_exceptions=True)`,
  never `TaskGroup` (§7.1).
- **I9 — Content overlap never auto-resolved.** Cross-plan *content* overlap (including
  identical-content add/add of files and directory-prefix relations), cumulative across waves,
  ⇒ honest conflict, apply none, resolve via the §7.4 winner/loser state machine. The §7.4
  non-content exemptions auto-merge: delete/delete (agreeing on terminal absence), directory
  add/add (no digest), and divergent added-directory permission-mode bits (last-writer-wins).
- **I10 — SDK shapes introspected.** Every SDK call shape is verified against the installed
  version; `[verify-against-installed]` facts treat installed-equivalents as conformant
  (§8.4).
- **I11 — Fresh session per phase; files-only handoff.** N1/N2 hold for every phase.
- **I12 — Every run is fresh.** No resume; a new timestamped run dir per call (§6.6).
- **I13 — Shared mutations only at wave boundaries (post-init).** After run-init (which
  one-time-creates `spec.md` and the empty `spec_amendments.md`), all mutations of
  `target_dir`, `spec.md`, and `spec_amendments.md` happen single-threaded in the orchestrator
  at wave boundaries (merge, amend, conflict resolution); plans mutate only their own sandbox
  and per-plan files.
- **I14 — Lazy post-merge sandboxes.** A plan's sandbox copy + manifest capture occur only
  after all its transitive dependencies have `merge_status=merged`; sandboxes are created
  wave-by-wave, never eagerly at run start.
