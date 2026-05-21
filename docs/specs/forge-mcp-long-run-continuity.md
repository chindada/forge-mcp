# forge-mcp — Long-Run Continuity

**What:** A normative enhancement brief that closes the two §H18 forward-looking
deferrals chosen for adoption: the MCP **task-augmented tool API**
(`call_tool_as_task`) so a 10-hour run survives client disconnect, and **per-phase
SDK session-id recording** as the durable foundation for a future mid-phase
resume layer. Companion to `forge-mcp-design.md` and
`forge-mcp-long-run-hardening.md`: the base doc wins on anything it already
specifies; the hardening doc wins on anything it specifies; this brief only adds
new behavior in its own `§C*` and `C-Invariant N` namespaces so code comments can
cite it unambiguously (e.g. `# §C1 task bridge`, `# §C-Inv 1 cancellation single
owner`). Three load-bearing decisions are also tagged as `C-Decision 1..3` where
they appear inline; §C10 is a broader rationale catalog keyed by `§C*`.

**Status:** Design complete. The base implementation and the H1–H10 hardening
are both shipped and CI-green. This brief is the implementation contract for
the next increment.

**Audience:** The implementing agent. Precision over prose; every interface
sketch is normative and carries the Rule 21 three-section docstring
(`Design:` / `Implementation:` / `Example:`).

**Scope (2 enhancements, chosen explicitly):** C1 `call_tool_as_task` adoption ·
C2 per-phase `session_id` / `thread.id` recording. **Explicitly out of scope:**
Jaccard-similarity non-progress threshold (the third §H18 deferral) stays
deferred — §H18's adoption gate ("once exact-equality proves too strict in
practice") has no empirical trigger yet; introducing the knob speculatively is
YAGNI. Also out of scope: any reader of the recorded session ids (mid-phase
resume), which stays a §H18 future layer.

---

## C0. Thesis, north star, and what does *not* change

### C0.1 The two gaps this brief closes

The base doc and the hardening doc together let a forge-mcp run work for ~10
hours without context anxiety (fresh SDK session per phase, file-based handoff,
hard agent boundaries, honest cap-hit, durable resume from `iter_done`). Two
gaps remain that are **outside an agent** and therefore unsolvable by any
in-agent discipline:

- **§6.1 / §8.5** — the MCP transport binds the run to the client connection.
  An idle-timed-out or disconnected client raises `asyncio.CancelledError` in
  the tool body; §8.5's forensic `cancelling → failed` ordering runs; the run
  is dead. There is no current mechanism to detach the work from the
  connection.
- **§H2** — iteration-boundary resume re-enters at `last_completed_iteration +
  1` from durable artifacts. A future *mid-phase* resume (the §H18 forward-
  looking item) would reattach to the SDK session itself — but the Claude
  `session_id` and Codex `thread.id` are emitted live during a phase and
  discarded today, leaving the future layer with nothing to resume.

The article's binding principles for this brief are **11 ("instrument for
observability over long runs")** for §C1 and **1 + 7 ("context reset via
durable files" + "file handoff")** for §C2. context7 (queried at design time)
confirms the SDK surface for both: the MCP Python SDK exposes
`session.experimental.{call_tool_as_task, poll_task, cancel_task,
get_task_result}` and a server-side `ServerTaskContext` with `task.is_cancelled`
+ `task.update_status`; the Claude Agent SDK exposes
`ClaudeAgentOptions(session_store=…, resume="<id>")`; the Codex SDK exposes
`AsyncCodex().thread_resume(<thread_id>)`.

### C0.2 The binding constraint (the north star is preserved)

Neither enhancement threads **live context** across phases:

- §C1 only changes the *wire surface* and the *cancellation poll path*. The
  orchestrator's per-phase work is byte-identical: same fresh SDK sessions, same
  file handoffs, same artifact tree.
- §C2 only **writes ids to disk**. No code in this round reads them. Drivers
  expose the id; the orchestrator composes the file. Agent context is
  unchanged.

`C-Invariant 0 (north star):` neither §C1 nor §C2 widens or persists an agent's
in-context working set across a phase boundary; the only durable additions are
on-disk artifacts (`iteration-N/sessions.json`, `plan/sessions.json`) and an
optional `RunResult.task_id` scalar.

### C0.3 Architectural stance: minimum-blast-radius rewrite where unavoidable

> **Alternative considered & rejected.** Keeping FastMCP and adding a *sibling*
> low-level `Server` on the same stdio (so `run_forge` stays on FastMCP and a
> new `run_forge_task` runs on the low-level Server). Rejected: MCP stdio
> transport multiplexes a **single** server connection per process — two server
> instances cannot share stdio cleanly. The honest path, when FastMCP does not
> expose task augmentation, is to migrate `server.py` to the low-level `Server`
> once and register both call paths on the same instance. This is the "right"
> option, not the "sweet" one (Rule 4): a dual-server kludge would carry
> permanent bootstrap complexity to avoid a one-time rewrite.

---

## C1. `call_tool_as_task` adoption

### C1.1 Problem & best-practice basis

`engine.run` wraps `run_phases` in `asyncio.wait_for(..., timeout=...)` for the
runtime cap, but the outer wall is the MCP transport itself: on a client
disconnect FastMCP's read loop raises `asyncio.CancelledError`,
`handle_cancellation` runs §8.5's five-step ordering, and the run is
forensically dead. Article principle 11 frames the gap: a 10-hour autonomous
run cannot assume the operator's terminal stays open.

context7 (`/modelcontextprotocol/python-sdk` at v1.12.4) confirms the surface:

- **Server side**: `server.experimental.enable_tasks()` one-liner registers the
  task management handlers; `ServerTaskContext` carries `is_cancelled` (polled),
  `update_status(message)` (status emission), and `elicit` / `create_message`
  (out of scope for forge-mcp's autonomous loop); `ctx.experimental.run_task(work)`
  runs the work inside the task lifecycle; `ToolExecution(taskSupport=...)`
  declares per-tool support as `TASK_REQUIRED`, `TASK_OPTIONAL`, or
  `TASK_FORBIDDEN` (the default).
- **Client side**: `session.experimental.call_tool_as_task(name, args, ttl=<ms>)`
  → `CreateTaskResult` with a `task_id`; `poll_task(task_id)` async-yields
  status; `cancel_task(task_id)` cooperatively cancels; `get_task_result(task_id,
  CallToolResult)` retrieves the final result after disconnect/reconnect.
  (The TTL parameter is in milliseconds — referred to as `ttl_ms` throughout
  the rest of this doc for clarity.)

> **`C-Decision 1` (experimental adoption):** the API is still in the
> `.experimental.` namespace. We adopt it because the disconnect-survival win
> is too valuable to defer further (§H18 noted this was the right "when it
> stabilizes" call), accepting that `mcp[cli] >=1.12,<2` does **not** protect
> against breaking changes within the `1.x` line for experimental APIs.
> Mitigation: bump `uv.lock` deliberately on every `mcp` release, and isolate
> task code in `server.py` plus one thin helper so any breaking change is
> localized.

### C1.2 Verification gate (mandatory pre-implementation step)

> **`C-Decision 2`:** implementation MUST begin with a written verification of
> whether FastMCP exposes `ToolExecution(taskSupport=…)` at the `@mcp.tool()`
> decorator/registration level for the pinned `mcp` version. Implementation
> does not start until this is resolved. Rationale (Rule 4): the entire
> `server.py` rewrite cost in Path B is avoided if Path A is feasible —
> guessing wrong burns the whole increment.

The verification produces one of two outcomes:

- **Path A available** — FastMCP supports declaring `taskSupport=TASK_OPTIONAL`
  on `@mcp.tool` (either natively or via an obvious extension hook). Adopt
  §C1.3.
- **Path A unavailable** — FastMCP does not expose `taskSupport`. Adopt §C1.4
  (single low-level Server migration). **This is the expected outcome** based
  on context7's design-time snapshot: every server-side task example
  (`docs/experimental/tasks-server.md`) uses the low-level `Server`, and no
  FastMCP example carries `taskSupport`. The verification stays mandatory in
  case a release added it after the snapshot.

The verification artifact is a small failing test
(`tests/test_fastmcp_task_support.py`) that attempts to construct a FastMCP
tool with `taskSupport=TASK_OPTIONAL` and asserts the outcome — pass = Path A,
fail = Path B. The test is part of the verification-step commit.

### C1.3 Path A — Lean-FastMCP (preferred if available)

```python
@mcp.tool(taskSupport=TASK_OPTIONAL)          # exact decorator surface TBD by §C1.2
async def run_forge(
    target_dir: str,
    ...,
    *,
    ctx: Context,
) -> RunResult:
    """Run the Planner/Generator/Evaluator loop, task-augmented when supported (§C1).

    Design: §C0 — task augmentation closes the client-disconnect cliff without
        adding cross-phase context (C-Inv 0). TASK_OPTIONAL preserves the
        existing direct-call path for short tests; long callers opt into the
        task lifecycle via session.experimental.call_tool_as_task.
    Implementation: os.umask(0o077) (Invariant 7) -> RunForgeInput validate ->
        RunConfig.from_env -> prepare_run; if the SDK reports task mode active
        for this call (detection API TBD by §C1.2 sub-verification — see the
        Path B `_client_requested_task_mode(ctx)` shim in §C1.4 for the
        candidate surfaces), wrap the orchestrator call in
        ctx.experimental.run_task(work); else run the existing inline path. The
        wrapped work(task) threads `task` through the Orchestrator constructor
        so it can poll task.is_cancelled (§C1.6) and emit task.update_status
        (§C1.5).
    Example: a host calling call_tool_as_task("run_forge", {...},
        ttl_ms=86_400_000) gets a task_id; disconnecting and reconnecting still
        returns the final RunResult via get_task_result.
    """
```

The body's pre-validation `os.umask(0o077)` (Invariant 7) and the §6.3
error-taxonomy bright line both hold unchanged. `RunResult.task_id` is populated
when `ctx.experimental.run_task` is invoked, `None` otherwise.

### C1.4 Path B — Single low-level Server migration (expected outcome)

If §C1.2 confirms FastMCP lacks `taskSupport`, `server.py` migrates to the
low-level `Server` API once. The migration is mechanical because the §18 schema
pins are the load-bearing surface, and they are already derived from pydantic:

```python
from mcp.server import Server
from mcp.server.experimental.task_context import ServerTaskContext
from mcp.types import (
    CallToolResult, CreateTaskResult, TextContent, Tool, ToolExecution,
    TASK_OPTIONAL,
)
from forge_mcp.models import RunForgeInput, RunResult

server = Server("forge-mcp")
server.experimental.enable_tasks()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Advertise run_forge with TASK_OPTIONAL so both call paths are first-class (§C1.4).

    Design: §C1.4 — Path B replaces FastMCP for run_forge once, on the API that
        supports task augmentation natively; existing direct-call clients keep
        working (TASK_OPTIONAL, §C1.3 back-compat note). The schemas are
        derived from the same pydantic models as before, so the §18 pins
        (object-root, no top-level combinators, cap minimum/maximum exposure)
        carry over unchanged.
    Implementation: input_schema = RunForgeInput.model_json_schema();
        output_schema = RunResult.model_json_schema(); execution =
        ToolExecution(taskSupport=TASK_OPTIONAL). Only one tool entry; the
        run_forge name and shape are identical to today.
    Example: an MCP host calling tools/list sees one tool, "run_forge", with
        taskSupport=optional advertised in execution.
    """
    return [
        Tool(
            name="run_forge",
            description=RUN_FORGE_DESCRIPTION,
            inputSchema=RunForgeInput.model_json_schema(),
            outputSchema=RunResult.model_json_schema(),
            execution=ToolExecution(taskSupport=TASK_OPTIONAL),
        )
    ]


@server.call_tool()
async def handle_tool(name: str, arguments: dict) -> CallToolResult | CreateTaskResult:
    """Dispatch a tool call; route run_forge to the task-aware handler (§C1.4).

    Design: dispatch is intentionally trivial — every tool's real body lives
        in its own handler so the §6.3 taxonomy stays the body's
        responsibility, not the dispatcher's. Unknown tools are a protocol-
        level error, surfaced as an isError CallToolResult per §6.3‡.
    Implementation: name match; pre-validation McpError paths inside the
        handler raise into FastMCP's existing translation, surfacing as
        isError text results (the numeric code is not transmitted on the wire
        — §6.3‡).
    Example: handle_tool("run_forge", {"target_dir":"/p", ...}) ->
        await run_forge_handler(arguments).
    """
    if name == "run_forge":
        return await run_forge_handler(arguments)
    return CallToolResult(
        content=[TextContent(type="text", text=f"Unknown tool: {name}")],
        isError=True,
    )


async def run_forge_handler(arguments: dict) -> CallToolResult | CreateTaskResult:
    """Validate inputs, prepare the run, then enter task or direct mode (§C1.4).

    Design: §C1.4 — both call paths reach the SAME orchestrator entry, so
        cancellation forensics (§8.5), the §6.3 taxonomy, and the §C-Inv 1
        single-cancellation-owner rule are uniform. The only branch is whether
        to wrap the work in ctx.experimental.run_task.
    Implementation: os.umask(0o077) FIRST (Invariant 7); RunForgeInput
        validate; RunConfig.from_env; prepare_run (which raises McpError on
        any preflight failure per §6.3); construct Orchestrator with task=None
        OR pass to run_task(work) where work(task) constructs Orchestrator
        with the live ServerTaskContext.
    Example: see §C5 control-flow sketch.
    """
    os.umask(0o077)
    inputs = RunForgeInput(**arguments)
    config = RunConfig.from_env()
    prepared = await prepare_run(inputs, config)
    ctx = server.request_context

    if _client_requested_task_mode(ctx):         # detection API TBD by §C1.2 sub-verification
        async def work(task: ServerTaskContext) -> CallToolResult:
            result = await _orchestrator_entry(prepared, inputs, config, ctx, task=task)
            return _result_to_call_tool_result(result)
        return await ctx.experimental.run_task(work)

    result = await _orchestrator_entry(prepared, inputs, config, ctx, task=None)
    return _result_to_call_tool_result(result)
```

**`_client_requested_task_mode(ctx)`** is a thin shim that wraps whichever
SDK predicate the §C1.2 sub-verification settles on. Two candidate APIs from
context7 evidence:

- *Explicit predicate* (preferred if it exists): something like
  `ctx.experimental.is_task_mode_active()` returning a bool.
- *Try-catch on `validate_task_mode(TASK_REQUIRED)`*: the documented validator
  raises when the call is not in task mode; catching it gives a bool. Ugly but
  works.

The shim isolates whichever path is chosen so the rest of the handler stays
clean.

**`_result_to_call_tool_result(result: RunResult) -> CallToolResult`** is a
small adapter that returns the `RunResult` as `structuredContent`
(pydantic-serialized) plus a one-line text summary; this keeps the §18 output
pin (object-root, no top-level combinators) and matches what FastMCP's
`-> RunResult` annotation produced for `structuredContent` today.

**`_extract_task_id(task: ServerTaskContext | None) -> str | None`** is a
fail-soft helper for §C11 risk 9. It returns `None` when `task is None`,
otherwise `getattr(task, "task_id", None) or getattr(task, "id", None)` — i.e.
tries `task.task_id` first, falls back to `task.id`, and never raises. The
client always has authoritative access to its own task_id via
`CreateTaskResult`; this helper exists only for offline artifact-to-task
correlation (§C3).

**`cli.py` bootstrap reconciliation:** `forge serve` calls
`stdio_server()` from `mcp.server.stdio` and runs the low-level `server` on it.
The existing `forge serve` entry point is preserved; only its internal wiring
moves from FastMCP's `mcp.run()` to the low-level idiom.

### C1.5 Status fan-out — one new sink

`status.Status.__init__` gains an optional `task: ServerTaskContext | None =
None`; `Status.update` fans out to the existing three sinks (`ctx.report_progress`,
`ctx.info`, NDJSON) **plus** `task.update_status(line)` when `task` is bound.
The line is the same human-readable string `Status.update` already builds for
`ctx.info` (§12).

```python
async def update(
    self, *, phase, agent, message, kind="phase", iteration=None,
) -> None:
    """Emit one status event across every wired sink, including tasks (§C1.5).

    Design: §C1.5 — task.update_status is one additional sink on the existing
        Status fan-out (§12); the human line is reused so a task-mode poller
        sees the same prose as a direct-call client's ctx.info stream. Status
        emission must NEVER raise — a failing sink is logged and suppressed,
        identical to today's ctx-sink discipline. No new status state is
        introduced (Decision 11 holds for direct-call; §H4.2 holds for the
        progress dimension).
    Implementation: build the human line as today; await ctx.report_progress
        and ctx.info in their existing per-sink try/except (status.py wraps
        these two today); write NDJSON as today (today's NDJSON write is NOT
        wrapped — out of scope to change here); then, if self._task, await
        self._task.update_status(line) in its OWN try/except so a task sink
        failure cannot crash the run (this is the §C1.5 sink's full
        responsibility — the doc does NOT rely on a pre-existing wrapper).
    Example: await status.update(phase="iter_generating", agent="generator",
        message="implementing", iteration=3)  # -> task poller sees the line.
    """
```

### C1.6 Cancellation bridge — preserves §8.5 exactly

A new tiny helper in `orchestrator/lifecycle.py`:

```python
def poll_task_cancellation(task: ServerTaskContext | None) -> None:
    """Raise CancelledError if the live task has been cancelled (§C1.6).

    Design: §C1.6 — the bridge from task.is_cancelled to the existing §8.5
        cancellation path is intentionally trivial: the orchestrator polls at
        phase boundaries (sparse but sufficient; mid-turn urgency falls through
        to the §H10 interrupt+terminate path). Centralizing the raise here
        keeps §8.5's handle_cancellation as the SINGLE owner of cancellation
        forensics — no parallel terminal path to maintain (C-Inv 1).
    Implementation: if task is None, return; if task.is_cancelled, raise
        asyncio.CancelledError() with a descriptive message
        ("client cancelled via cancel_task"); else return.
    Example: poll_task_cancellation(task)  # raises if cancelled, else no-op.
    """
```

`run_iteration_loop` calls this immediately after every `sm.transition(...)`
inside the loop body (after `iter_generating`, `iter_verifying` *when
`verify_command` is set*, `iter_evaluating`, `iter_triaging` *when triage ran*,
`iter_done`, `iter_remediating`). The plan phase calls it after `planning` and
`planned`. Polling at boundaries is sparse (4–6 polls per iteration depending on
`verify_command` and whether triage/remediation ran; 2 polls in the plan phase)
but sufficient because the §H10 SDK-level `interrupt() → terminate()` path
covers urgent mid-turn cancellation as a backstop.

> **`C-Invariant 1` (single cancellation authority):** `task.is_cancelled` only
> ever produces `asyncio.CancelledError` via `poll_task_cancellation`; it never
> directly transitions the state machine, releases the lock, or closes drivers.
> §8.5's `handle_cancellation` (five-step ordering) remains the only path that
> transitions to `cancelling → failed`.

> **`C-Invariant 2` (disconnect survives in task mode):** client disconnect
> during task mode does **not** raise `CancelledError`. The work runs to its
> normal terminal (`completed` / `incomplete` / `failed`); the result is
> retrievable via `get_task_result` until TTL expiry.

---

## C2. Per-phase `session_id` / `thread.id` recording

### C2.1 Problem & best-practice basis

§H2 iteration-boundary resume re-enters at `last_completed_iteration + 1` from
durable artifacts. A future *mid-phase* resume layer (§H18) would re-enter the
SDK session itself — but only if the session id is recorded. Today it is
discarded: `_claude.ClaudeRunnerImpl` consumes the SDK's `init`/`SystemMessage`
during `drain_text` without persisting `session_id`, and
`_codex.CodexRunnerImpl` does not retain the `AsyncThread` at all — the local
`thread` variable is consumed by `thread.runs.create(...)` inside `turn()` and
discarded; only `self._session` (the run handle) is retained. The article's
principle 7 (file handoff) and forensic-state ethos both argue for capturing
every identifier with stable identity, even when no consumer exists yet.

context7 confirms both ids are durable across processes: Claude Agent SDK's
`ClaudeAgentOptions(session_store=store, resume="<session_id>")` accepts a prior
`session_id`; Codex's `AsyncCodex().thread_resume(<thread_id>)` reattaches by
`thread.id`.

### C2.2 Capture surface (driver-side)

Both SDK Runner Protocols gain a read-only attribute populated during the
active call and persisting after `aclose()`:

```python
class ClaudeRunner(Protocol):
    """Claude SDK seam (existing) — extended with last_session_id (§C2).

    Design: §C2 — recording the SDK session id is a no-cost forensic write that
        unlocks a future mid-phase resume layer (§H18) without threading live
        context across phases (C-Inv 0). The attribute is per-call: set when
        the init/SystemMessage arrives, read after the call returns,
        overwritten on the next call (single runner is in-flight at a time —
        Invariant 2).
    Implementation: ClaudeRunnerImpl extracts session_id from the SDK's init
        SystemMessage during stream processing; remains None if the SDK did
        not emit one (fail-soft, §C11 risk 4 — a null entry in sessions.json
        is acceptable forensic; raising would terminate the run).
    Example: await runner.run(...); sid = runner.last_session_id.
    """
    last_session_id: str | None
    # existing: run, aclose, terminate, interrupt
```

```python
class CodexRunner(Protocol):
    """Codex SDK seam (existing) — extended with last_thread_id (§C2).

    Design: mirrors ClaudeRunner.last_session_id — capture-only, no resume use
        in this round. Codex's thread.id is durable across processes and can
        be thread_resume'd by a future mid-phase layer.
    Implementation: CodexRunnerImpl retains self._thread (the AsyncThread
        returned by the thread-construction call — `codex.thread_start(...)`
        per context7's current docs, `codex.threads.create()` per the current
        codebase; reconcile in §C11 risk 5) and exposes self._thread.id as
        last_thread_id. Verify the AsyncThread object exposes .id at the
        pinned openai-codex commit (§C11 risk 5).
    Example: await runner.turn(...); tid = runner.last_thread_id.
    """
    last_thread_id: str | None
```

The three concrete driver classes (`planner`, `generator`, `evaluator`)
re-export the captured id via their own `last_session_id` attribute so phase
code reads off the driver rather than reaching into the runner. For the
evaluator (three Claude calls per iteration: `evaluate`, `triage_design_flaws`,
`write_remediation`), each call overwrites; the phase function reads the
attribute **immediately after each call returns** before the next call
overwrites it (§C2.5).

**Per-attempt overwrite under retries.** Every wrapped call layer
(`with_transient_retry` up to 3 attempts × `with_schema_retry` up to 2
attempts for the evaluator; `with_transient_retry` for the planner; the
post-validation re-author for plan/contract) creates a fresh SDK session per
attempt and overwrites `last_session_id`. The captured id is therefore **the
last successful attempt's session id** — earlier attempts' ids are silently
discarded. This is by design under `C-Inv 0` (forensic-only, no reader) and
`C-Inv 3` (no current consumer): a future mid-phase resume layer would
naturally want to resume the *last* session anyway, so this is the right
behavior even when the consumer arrives. Test plans in §C8 implicitly assume
the no-retry path; the matrix is correct for one attempt per call.

### C2.3 Artifact shape (`iteration-N/sessions.json` and `plan/sessions.json`)

Per-iteration sidecar, parallel to `eval.json`/`triage.json`:

```json
{
  "iteration": 3,
  "phases": [
    {"phase": "iter_generating",  "sdk": "codex",  "session_id": "thr_abc123",  "started_at": "2026-05-22T10:14:01Z", "completed_at": "2026-05-22T10:42:18Z"},
    {"phase": "iter_verifying",   "sdk": null,     "session_id": null,          "started_at": "2026-05-22T10:42:18Z", "completed_at": "2026-05-22T10:43:02Z"},
    {"phase": "iter_evaluating",  "sdk": "claude", "session_id": "sess_xyz789", "started_at": "2026-05-22T10:43:02Z", "completed_at": "2026-05-22T10:51:44Z"},
    {"phase": "iter_triaging",    "sdk": "claude", "session_id": "sess_uvw456", "started_at": "2026-05-22T10:51:44Z", "completed_at": "2026-05-22T10:53:09Z"},
    {"phase": "iter_remediating", "sdk": "claude", "session_id": "sess_def012", "started_at": "2026-05-22T10:53:09Z", "completed_at": "2026-05-22T10:55:01Z"}
  ]
}
```

Plan phase gets its own `plan/sessions.json` with a single entry. The verify
phase (§H1) records `sdk: null`, `session_id: null` — it is an orchestrator-run
subprocess (`verifier.run_verification`), not an SDK session. The file always
contains the phases that actually ran for this iteration; see §C8 for the full
matrix (entry count varies with `verify_command` × gaps × remediation: 2 to 5
entries).

### C2.4 Writer (`artifacts.py`, additive)

```python
def write_sessions_json(path: Path, iteration: int, entries: list[dict]) -> None:
    """Atomically persist a phase's session-id record (§C2.4).

    Design: parallel to render_eval_md / atomic_write_text — pure templating
        plus an atomic write. Per-iteration shape so the artifact reflects
        the phase order through the most recent write boundary (the two
        per-iteration writes happen immediately before `sm.transition("iter_done",
        ...)` and at the end of the `iter_remediating` step body — C-Inv 4); a
        crash STRICTLY BEFORE the first write of an iteration leaves no
        sessions.json for that iteration (state.json remains the authoritative
        crash forensic, §8.2). For plan/sessions.json, iteration is 0 (sentinel:
        pre-loop).
    Implementation: payload = {"iteration": iteration, "phases": entries};
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False) +
        "\\n"); chmod 0600 (POSIX) consistent with the existing artifacts.
    Example: write_sessions_json(iteration_dir / "sessions.json", 3, [...]).
    """
```

### C2.5 Phase wiring (orchestrator-owned)

`run_plan_phase` (one entry, written once):

```
sm.transition("planning"); started = now()
recovery = await deps.drivers.planner.write_plan(ctx)
entry = {"phase": "planning", "sdk": "claude",
         "session_id": deps.drivers.planner.last_session_id,
         "started_at": started, "completed_at": now()}
write_sessions_json(plan_dir / "sessions.json", iteration=0, entries=[entry])
sm.transition("planned")
```

`run_iteration_loop` (per-iteration accumulator, written immediately before
`sm.transition("iter_done", ...)` and again at the end of `iter_remediating`):

```
for n in start_iteration .. max_iterations:
  phase_sessions: list[dict] = []
  # step 2 iter_generating: append codex entry from drivers.generator.last_session_id
  # step 2.5 iter_verifying (if verify_command): append {sdk:null, session_id:null}
  # step 3 iter_evaluating: append claude entry
  # step 4 iter_triaging (if triage_ran): append claude entry
  # steps 5/5.5: synth gaps; no session entries (no SDK call)
  # step 6 iter_done:
  write_sessions_json(iteration_dir / "sessions.json", n, phase_sessions)
  sm.transition("iter_done", iteration=n, last_completed_iteration=n)
  lifecycle.poll_task_cancellation(task)                          # §C1.6
  # step 7 terminal check (unchanged)
  # step 8 iter_remediating: append claude entry; re-write sessions.json
  lifecycle.poll_task_cancellation(task)                          # §C1.6
```

> **`C-Decision 3`:** `sessions.json` is written by the orchestrator (not the
> drivers) so the cross-phase aggregation is atomic. Drivers expose ids; the
> orchestrator composes the artifact. This matches the existing pattern where
> `artifacts.render_eval_md` composes `eval.md` from driver-written `eval.json`
> rather than the evaluator writing both.

> **`C-Invariant 3` (forensic-only):** `sessions.json` is **forensic-only** in
> this round. No code path (resume, retry, lifecycle, result builder beyond
> path-indexing) reads its contents. Adding a reader is out of scope; doing so
> without a corresponding mid-phase resume design is a layering violation.

> **`C-Invariant 4` (write cadence):** `iteration-N/sessions.json` is written
> exactly twice per iteration that runs to `iter_remediating` (once at the
> `iter_done` step body — write_sessions_json *immediately precedes*
> `sm.transition("iter_done", ...)` so the artifact is durable before the state
> transition; once at the end of the `iter_remediating` step body); exactly
> once if the iteration is terminal (no remediation). `plan/sessions.json` is
> written exactly once, before `sm.transition("planned")`. No other writers,
> no other call sites.

---

## C3. Public API & model changes (consolidated)

All additions are **optional fields**, so the §18 input/output schema pins
(object-root, no top-level combinators; cap fields expose `minimum`/`maximum`)
hold and existing callers are unaffected.

**`RunForgeInput`** (`models.py`): **no change.** Task TTL is per-call client-
controlled (not a server input); session recording needs no caller knob.
Documenting that `call_tool_as_task` callers SHOULD set
`ttl_ms >= max_runtime_minutes * 60 * 1000` lives in the README, not the
schema.

**`RunResult`** (`models.py`):

```python
task_id: str | None = None        # populated when in task mode AND the SDK exposes the
                                  # id on ServerTaskContext (best-effort, §C11 risk 9);
                                  # None on the direct-call path or when the SDK does
                                  # not expose it. The client already knows its own
                                  # task_id from CreateTaskResult; this field is for
                                  # offline artifact-to-task correlation only.
```

**`IterationArtifacts`** (`models.py`):

```python
sessions_path: str | None = None  # iteration-N/sessions.json (§C2)
```

**`ArtifactIndex`** (`models.py`):

```python
plan_sessions_path: str | None = None  # plan/sessions.json (§C2)
```

**`Status`** (`status.py`): gains optional `task: ServerTaskContext | None =
None` constructor parameter (§C1.5). `Status.update` adds one fan-out sink
(`if self._task: await self._task.update_status(line)`) — `try/except`
suppressed identically to the existing `ctx` sinks (status emission must never
raise).

**`RunLedger`** (`orchestrator/ledger.py`): **no new field.** Session ids are
written through to disk by the iteration loop's local `phase_sessions` list;
they are not accumulated in the ledger because no terminal-result code reads
them (`C-Inv 3`).

**SDK Runner Protocols** (`drivers/_claude.py`, `drivers/_codex.py`): new
attributes `last_session_id: str | None` / `last_thread_id: str | None`
(§C2.2).

**Driver classes** (`planner`, `generator`, `evaluator`): expose
`last_session_id` as a passthrough property reading from the held runner.

---

## C4. Module map & dependency-graph compliance

**No new modules.** Both enhancements localize to existing files:

| File | Change | Section |
|---|---|---|
| `server.py` | Path A: `taskSupport=TASK_OPTIONAL` declaration on `@mcp.tool` + the `_orchestrator_entry`/`_extract_task_id` helpers (§C1.3 still uses them — the helpers are path-agnostic). **Path B (expected)**: migrate to low-level `Server`; declare `Tool(..., execution=ToolExecution(taskSupport=TASK_OPTIONAL))`; add the dispatcher (`handle_tool`) + handler (`run_forge_handler`) + helpers (`_client_requested_task_mode`, `_result_to_call_tool_result`, `_extract_task_id`, `_orchestrator_entry`). | §C1.3 / §C1.4 |
| `cli.py` | Path B only: `forge serve` wires `stdio_server()` to the low-level `Server` instead of `mcp.run()`. | §C1.4 |
| `status.py` | Optional `task` ctor param + one new fan-out sink in `Status.update`. | §C1.5 |
| `orchestrator/lifecycle.py` | New `poll_task_cancellation(task)` helper (no I/O beyond reading the flag); existing `close_drivers` / `handle_cancellation` unchanged. | §C1.6 |
| `orchestrator/engine.py` | Accept optional `task: ServerTaskContext | None` and an optional `task_id: str | None` on the `Orchestrator` constructor; store on `self._task` / `self._task_id`. Inside `run()` (unchanged today — `Status` is constructed in engine.py per §8.1), pass `task=self._task` when constructing `Status(...)`; pass `task=self._task` to **every** phase entry point (today's `engine.py` has two: the fresh-run `run_phases(...)` call and the resumed-run `run_iteration_loop(...)` call — both get `task=self._task`); pass `task_id=self._task_id` to `build_result(...)`. **Both `task` and `task_id` are constructor-passed by `_orchestrator_entry` in `server.py` so the existing `server.py → orchestrator` edge is reused; no reverse edge is introduced (§5.2 preserved).** | §C5 |
| `orchestrator/phases.py` | Extend `run_phases`, `run_plan_phase`, and `run_iteration_loop` signatures with `task: ServerTaskContext | None = None` (keyword-only, default `None`); accumulate `phase_sessions` list per iteration; write `iteration-N/sessions.json` immediately before `sm.transition("iter_done", ...)` and again at the end of `iter_remediating`; write `plan/sessions.json` in `run_plan_phase`; call `lifecycle.poll_task_cancellation(task)` at phase boundaries (see §C5 for exact call sites). | §C1.6 / §C2.5 |
| `drivers/_claude.py` | Capture `session_id` from SDK init `SystemMessage`; add `last_session_id` attribute on `ClaudeRunnerImpl`. | §C2.2 |
| `drivers/_codex.py` | Retain `self._thread` (the `AsyncThread` returned by the thread-construction call — see §C11 risk 5 for the `thread_start` vs `threads.create()` reconciliation) alongside the existing `self._session`; expose `last_thread_id` on `CodexRunnerImpl`. | §C2.2 |
| `drivers/{planner,generator,evaluator}.py` | Add `last_session_id` passthrough property reading from the held runner. | §C2.2 |
| `artifacts.py` | New `write_sessions_json(path, iteration, entries)` helper (leaf; stdlib-only addition). | §C2.4 |
| `models.py` | `RunResult.task_id`, `IterationArtifacts.sessions_path`, `ArtifactIndex.plan_sessions_path` — all optional. | §C3 |
| `orchestrator/result.py` | `build_result` accepts a `task_id: str | None` keyword arg sourced from `Orchestrator.__init__`'s constructor-passed `task_id` (which `_orchestrator_entry` in `server.py` extracts via `_extract_task_id(task)` — §C1.4); the ledger holds no task_id (§C3). `_artifact_index` glob picks up `iteration-N/sessions.json` and `plan/sessions.json` when present. | §C3 |

**Graph stays acyclic (§5.2 preserved):** no new **internal** edges; the only
new edges are external (to the `mcp` package), and they are gated to
type-checking time only.

- `server.py` already imports `mcp.server.fastmcp`; Path A adds nothing.
  Path B switches to `mcp.server.Server` + `mcp.server.experimental.task_context`
  (same `mcp` package) and `mcp.server.stdio` — all runtime edges, expected.
- `artifacts.py` stays a leaf; the new helper imports only stdlib (`json`,
  `pathlib`).
- Only `orchestrator/*` writes `state.json` (Invariant 1) — `sessions.json` is
  **not** `state.json`; it is an additive sidecar in the artifact tree, and the
  Invariant 1 ownership is unaffected.
- The forbidden `drivers → orchestrator` and `drivers → doctor` edges are not
  crossed: drivers only expose attributes; the orchestrator composes the file.
- **`ServerTaskContext` type-only import discipline.** `status.py`, `engine.py`,
  `lifecycle.py`, and `phases.py` each gain a `ServerTaskContext | None`
  parameter as part of this brief. Today **none of these four files import any
  `mcp.*` module** (verified against the current tree — each file imports only
  stdlib + intra-package siblings from `forge_mcp/*`; in particular `engine.py`
  pulls in `..artifacts`/`..doctor`/`..gitguard`/`..models`/`..preflight`/
  `..state`/`..status` plus `orchestrator/*` siblings, `phases.py` pulls in
  `..artifacts`/`..config`/`..gitguard`/`..models`/`..runcontext`/`..verifier`
  plus `orchestrator/*` siblings, `lifecycle.py` reaches drivers via
  `deps: Any` rather than importing `drivers/*`, and `status.py` is a
  stdlib-only leaf). To avoid introducing **runtime** edges from any of these
  files to `mcp.server.experimental.task_context`, the type import MUST be
  gated under `if TYPE_CHECKING:` (all four files already have
  `from __future__ import annotations`, verified at the current tree, so the
  gating is one-line per file and the runtime edge stays zero):
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from mcp.server.experimental.task_context import ServerTaskContext
  ```
  The actual runtime `task: ServerTaskContext | None` value is constructed in
  `server.py` (which already imports the type) and passed by reference — no
  receiver needs the import at runtime. This preserves the existing §5.2 edge
  set verbatim. The runtime contract `Status.update`,
  `lifecycle.poll_task_cancellation`, `Orchestrator.__init__`, and the
  `phases.py` loop functions rely on (`task.is_cancelled`, `task.update_status`,
  `task.task_id`/`task.id`) is exercised via duck-typing, the same pattern
  `lifecycle.py` already uses for `deps: Any` and `status.py` for `ctx: Any`.

---

## C5. Enhanced control flow (integrated)

Augments §H13's control flow in three places — the orchestrator entry, the
`run_plan_phase`, and the iteration loop:

```
server.py (Path B handler):
  os.umask(0o077); RunForgeInput.validate(...); config = RunConfig.from_env()
  prepared = await prepare_run(inputs, config)
  if _client_requested_task_mode(ctx):                      # §C1.4 shim
    async def work(task):
      return _result_to_call_tool_result(
          await _orchestrator_entry(prepared, inputs, config, ctx, task=task))
    return await ctx.experimental.run_task(work)            # §C1.4
  return _result_to_call_tool_result(
      await _orchestrator_entry(prepared, inputs, config, ctx, task=None))

_orchestrator_entry(prepared, inputs, config, ctx, *, task):                # server.py
  task_id = _extract_task_id(task)                                            # §C11 risk 9 (server.py helper)
  return await Orchestrator(prepared, inputs, config, ctx, drivers=...,
                            task=task, task_id=task_id).run()                 # §C4: constructor-passed values (no engine→server edge); Status stays engine-owned per §8.1

Orchestrator.run():  # unchanged structure; `task` and `task_id` are
                     # constructor-stored on `self` (read as `self._task` and
                     # `self._task_id`); `run()` itself takes no new parameters.
                     # Status is still constructed HERE (engine-owned per §8.1) — only its
                     # ctor call gains `task=self._task` as the §C1.5 sink wiring.
  ... existing pre-try setup (§8.1), with Status now taking task=self._task ...
  status = Status(run_id, ctx, run_dir / "status.log", task=self._task)       # §C1.5 — engine.py constructs as today; only adds the new kwarg
  try:
    sm.transition("canonicalizing"); canonicalize_design(...)
    base_git = capture_state_forensic(...)
    ... existing ...
    # Engine has TWO phase entry points today — both MUST receive task=self._task:
    #   (a) fresh-run: run_phases(deps, sm, ledger, base_git)               (engine.py current call)
    #   (b) resumed-run: run_iteration_loop(deps, sm, ledger, base_git,
    #                                       start_iteration=resume_point.start_iteration)
    # Add `task=self._task` to BOTH call sites. The sketch below shows (a); apply the
    # same edit at the resume branch in engine.py.
    terminal_status, iters = await asyncio.wait_for(
        run_phases(deps, sm, ledger, base_git, task=self._task),              # base_git stays positional (matches today's engine.py call shape); task is the new keyword-only param
        timeout=inputs.max_runtime_minutes * 60)
    ... existing ledger / sm / emit_terminal_status / apply_caps_and_overflow ...
  except TimeoutError:        # unchanged, lifecycle.handle_timeout
  except asyncio.CancelledError:
    await lifecycle.handle_cancellation(sm, ledger, deps, prepared.lock)
    raise                                                   # C-Inv 1
  except Exception as exc:    # unchanged, lifecycle.handle_failure
  finally:                    # unchanged release/teardown
  return build_result(..., task_id=self._task_id)            # constructor-passed; no new dependency edge

run_plan_phase(deps, sm, ledger, *, task=None):
  sm.transition("planning"); started = now()
  lifecycle.poll_task_cancellation(task)                                          §C1.6 (post-planning)
  recovery = await deps.drivers.planner.write_plan(ctx)
  entries = [{"phase":"planning","sdk":"claude",
              "session_id": deps.drivers.planner.last_session_id,
              "started_at": started, "completed_at": now()}]
  write_sessions_json(plan_dir / "sessions.json", iteration=0, entries=entries)  §C2.5
  sm.transition("planned")
  lifecycle.poll_task_cancellation(task)                                          §C1.6 (post-planned)

run_iteration_loop(deps, sm, ledger, base_git, *, start_iteration=1, task=None):
  for n in start_iteration .. max_iterations:
    phase_sessions = []
    1  build/ensure iteration-n/                                   (existing)
    2  iter_generating  -> watchdog(with_transient_retry(generator.implement(..., network_access)))
       phase_sessions.append({"phase":"iter_generating","sdk":"codex",
                              "session_id":deps.drivers.generator.last_session_id,...})
       lifecycle.poll_task_cancellation(task)                                     §C1.6
    2.5 iter_verifying (§H1, only if inputs.verify_command is set):
       run_verification; verify.txt
       phase_sessions.append({"phase":"iter_verifying","sdk":None,
                              "session_id":None,...})
       lifecycle.poll_task_cancellation(task)                                     §C1.6
    3  iter_evaluating  -> with_transient_retry(with_schema_retry(evaluate(..., changed_files)))
       phase_sessions.append({"phase":"iter_evaluating","sdk":"claude",
                              "session_id":deps.drivers.evaluator.last_session_id,...})
       lifecycle.poll_task_cancellation(task)                                     §C1.6
    4  iter_triaging    -> (if gaps) with_transient_retry(with_schema_retry(triage))
       if triage_ran:
         phase_sessions.append({"phase":"iter_triaging","sdk":"claude",
                                "session_id":deps.drivers.evaluator.last_session_id,...})
         lifecycle.poll_task_cancellation(task)                                   §C1.6 (only if triage_ran)
    5  git-violation synth                                                        (existing)
    5.5 verify-gap synth                                                          (existing §H1)
    6  iter_done:
       write_sessions_json(iteration_dir / "sessions.json", n, phase_sessions)      §C2.5
       sm.transition("iter_done", iteration=n, last_completed_iteration=n)
       ledger.gap_fingerprints.append(fingerprint_gaps(eval_for_loop.gaps))
       signal = detect_non_progress(...)                                          (existing §H3)
       lifecycle.poll_task_cancellation(task)                                     §C1.6
    7  terminal check (existing §H1/§H3)
    8  iter_remediating:
       write_remediation(..., pivot=signal.pivot); validate §H6
       phase_sessions.append({"phase":"iter_remediating","sdk":"claude",
                              "session_id":deps.drivers.evaluator.last_session_id,...})
       write_sessions_json(iteration_dir / "sessions.json", n, phase_sessions)      §C2.5
       lifecycle.poll_task_cancellation(task)                                     §C1.6
```

---

## C6. Invariant & taxonomy preservation (summary)

The §6.3 bright line and §8.5 ordering are **unchanged**; every new path slots
into an existing terminal:

- **Client disconnect in task mode** → no `CancelledError` → run continues to
  its normal terminal → returns to the eventual `get_task_result` poller
  (`C-Inv 2`).
- **Client `cancel_task(task_id)`** → `task.is_cancelled` flips True →
  orchestrator polls at the next phase boundary → `poll_task_cancellation`
  raises `CancelledError` → §8.5's `handle_cancellation` runs unchanged
  (forensic `cancelling → failed`, five-step ordering preserved) (`C-Inv 1`).
- **Runtime cap / non-progress break / verify-fail / SDK crash / transient
  retry exhaustion / iteration cap**: all paths unchanged. `task_id` is the
  only new `RunResult` field these paths may populate (when running in task
  mode).

**New invariants summary:**

- **`C-Inv 0`** — neither §C1 nor §C2 widens or persists an agent's in-context
  working set across a phase boundary.
- **`C-Inv 1`** — `task.is_cancelled` only produces `CancelledError` via
  `poll_task_cancellation`; never directly transitions state, releases the
  lock, or closes drivers.
- **`C-Inv 2`** — client disconnect in task mode does **not** raise
  `CancelledError`; work runs to completion; result retrievable until TTL
  expiry.
- **`C-Inv 3`** — `sessions.json` is forensic-only this round; no code reads
  it.
- **`C-Inv 4`** — `iteration-N/sessions.json` is written exactly twice per
  iteration that runs to `iter_remediating` (immediately before
  `sm.transition("iter_done", ...)`, and at the end of the `iter_remediating`
  step body); exactly once if the iteration is terminal (no remediation);
  `plan/sessions.json` is written exactly once, before `sm.transition("planned")`.
  See §C2.5 / §C5 for the precise call sites.

---

## C7. Best-practice grounding (article + context7)

| Enhancement | Article principle | context7 evidence |
|---|---|---|
| §C1 `call_tool_as_task` | 11 (instrument for observability over long runs); the article's "design for the duration the work actually takes, not the connection's idle limit" | `/modelcontextprotocol/python-sdk` v1.12.4 ships `session.experimental.{call_tool_as_task, poll_task, cancel_task, get_task_result}`, `ServerTaskContext` (with `is_cancelled`, `update_status`, `elicit`, `create_message`), `server.experimental.enable_tasks()`, and `ToolExecution(taskSupport=TASK_OPTIONAL)` declaration via low-level `Server` (`docs/experimental/tasks.md`, `docs/experimental/tasks-server.md`, `docs/experimental/tasks-client.md`). Every server example uses the low-level `Server` — FastMCP support is unconfirmed (§C1.2 verifies). |
| §C2 session_id recording | 1 (context reset via durable files), 7 (file handoff); the article's discipline of capturing every stable identifier so the next harness iteration could reattach | Claude `/anthropics/claude-agent-sdk-python` exposes `ClaudeAgentOptions(session_store=…, resume="<id>")` with example S3/Postgres/Redis adapters and an `init` `SystemMessage` carrying `session_id`; Codex `/openai/codex` exposes `AsyncCodex().thread_resume(thread_id)`, with `thread_fork` and `compact` complementary affordances. Both id types are durable across processes — recording is cheap and lossless. |

The base architecture and the H1–H10 hardening already cover the bulk of the
article. This brief closes the **transport-resilience** and the
**mid-phase-resume-foundation** corners that those two doses left open by
design.

---

## C8. Testing strategy (extends §18 and §H16)

TDD discipline (as in §H16): write the failing test first for each behavior;
all existing tests stay green; Rule 21 docstrings on every new `def`;
`scripts/ci.sh` green at the end.

**Pure / unit (no SDK / no disk):**

- `artifacts.write_sessions_json` — atomic write; chmod 0600 on POSIX; valid
  JSON; phase order preserved; iteration field present and correct.
- Runner attribute: `last_session_id` is `None` before first call; populated
  after the init message arrives; preserved after `aclose()`; **reset to None
  at the start of the next call**, populated again.
- `lifecycle.poll_task_cancellation`: `task is None` → no-op; `task.is_cancelled
  is False` → no-op; `task.is_cancelled is True` → raises `asyncio.CancelledError`
  with the expected message.

**Loop / driver (mocked runners):**

- §C1 task-mode cancellation: a `FakeServerTaskContext` whose `is_cancelled`
  flag flips True between phases triggers `CancelledError` at the next
  `poll_task_cancellation` and reaches §8.5's `handle_cancellation` (asserting
  the same five-step ordering as today's transport-level cancellation).
- §C1 status fan-out: `Status.update` calls `task.update_status` with the same
  human line as `ctx.info`; an exception from `task.update_status` is
  suppressed (status emission never raises); a `Status(task=None)` does not
  call `update_status`.
- §C1 `task_id`: when `ctx.experimental.run_task` is invoked (Path A or Path B),
  the resulting task id appears in `RunResult.task_id`; the direct-call path
  leaves it `None`.
- §C2 sessions.json composition (matrix of `verify_command` × triage):
  - `verify_command` set + gaps + remediation → 5 entries (`iter_generating`,
    `iter_verifying`, `iter_evaluating`, `iter_triaging`, `iter_remediating`).
  - `verify_command` set + no gaps (terminal) → 3 entries (`iter_generating`,
    `iter_verifying`, `iter_evaluating`).
  - `verify_command` unset + gaps + remediation → 4 entries (no `iter_verifying`).
  - `verify_command` unset + no gaps (terminal) → 2 entries.
  The `iter_remediating` entry is appended via the second write at the end of
  step 8 (`C-Inv 4`); test all four configurations.
- §C2 `plan/sessions.json`: one entry after `run_plan_phase` completes.
- §C2 fail-soft capture: a runner whose `last_session_id` is `None` (SDK didn't
  emit) produces an entry with `"session_id": null`; the run does not raise.

**Schema / transport pins (extend §18 and §H16):**

- `RunResult.task_id` and the new optional `*_sessions_path` fields keep the
  output schema object-root with no top-level combinators (the §18 pin holds).
- New transport test (Path A or Path B as the verification selects): a
  `FakeClientSession` invokes `run_forge` via `call_tool_as_task` (with
  `ttl_ms >= max_runtime_minutes * 60 * 1000`) and completes successfully,
  returning a `RunResult` payload via `get_task_result`; a `cancel_task` mid-run
  triggers §8.5 cancellation as observed in the synthetic iteration test.
- Existing transport tests (xor violation, out-of-range cap, preflight failure)
  stay green on the post-Path-B `server.py` — the `isError` text-result shape
  is preserved (§6.3‡).

**Verification-gate artifact (`tests/test_fastmcp_task_support.py`):**

- A standalone test asserting whether the pinned `mcp` version exposes
  `taskSupport` at the FastMCP decorator / registration level.
  **Pass = Path A; fail = Path B.** The test outcome is the decision input for
  §C1.3 vs §C1.4 and is committed as part of the verification step.

---

## C9. Implementation sequencing

Both enhancements land behind their new optional surface; partial rollout is
identical to today when callers don't opt in.

1. **Verification step (no code change beyond the test artifact):** run the
   FastMCP-task-support probe; commit the outcome
   (`tests/test_fastmcp_task_support.py`) with a one-line message recording
   Path A vs Path B.
2. **§C2 ships first (smaller, decoupled):**
   1. Add `last_session_id` / `last_thread_id` to runner Protocols + Impls.
   2. Add `last_session_id` passthrough on the three drivers.
   3. Add `artifacts.write_sessions_json`.
   4. Wire `run_plan_phase` + `run_iteration_loop` writers.
   5. Add `IterationArtifacts.sessions_path` + `ArtifactIndex.plan_sessions_path`
      in `result._artifact_index`.
   6. Tests; `scripts/ci.sh` green.
3. **§C1 (depends on §C1.2 verification outcome):**
   - **Common substeps (both paths):**
     1. Add `RunResult.task_id` (optional, default `None`).
     2. Add `_extract_task_id(task)` helper to `server.py` (§C1.4 definition).
     3. Add `_orchestrator_entry(prepared, inputs, config, ctx, *, task)` helper
        to `server.py`: calls `_extract_task_id(task) → task_id`, constructs
        `Orchestrator(..., task=task, task_id=task_id)`, and calls `.run()`.
        **`Status` is NOT constructed here** — `engine.py`'s `Orchestrator.run()`
        keeps Status ownership per §8.1; the helper only passes `task` through.
     4. Extend `Orchestrator.__init__` to accept `task: ServerTaskContext |
        None = None` and `task_id: str | None = None`; store on `self._task`
        and `self._task_id`; inside `run()`, pass `task=self._task` to the
        existing `Status(...)` construction (§C1.5) and to `run_phases(...)`;
        pass `task_id=self._task_id` to `build_result(...)`.
     5. Extend `Status.__init__` to accept optional `task: ServerTaskContext |
        None = None` and add the new fan-out sink in `Status.update` (§C1.5).
     6. Add `lifecycle.poll_task_cancellation(task)` helper + all phase-boundary
        call sites in `run_plan_phase` and `run_iteration_loop` (§C1.6 / §C5).
     7. Apply the `if TYPE_CHECKING: from mcp.server.experimental.task_context
        import ServerTaskContext` discipline in `status.py`, `engine.py`,
        `lifecycle.py`, and `phases.py` (§C4 type-only import note — every file
        that gains a `task: ServerTaskContext | None` parameter without already
        importing `mcp.*`).
   - **Path A** (if verification passes): in `server.py`, declare
     `@mcp.tool(taskSupport=TASK_OPTIONAL)` on `run_forge`; inside the body,
     if the SDK reports task-mode active, wrap the existing
     `_orchestrator_entry(...)` call in `ctx.experimental.run_task(work)`; else
     call it inline.
   - **Path B** (expected): migrate `server.py` to low-level `Server`; declare
     `Tool(..., execution=ToolExecution(taskSupport=TASK_OPTIONAL))`; wire
     `@server.call_tool()` dispatch to `run_forge_handler` (which branches on
     `_client_requested_task_mode(ctx)` and either wraps in `run_task` or calls
     `_orchestrator_entry` inline); reconcile `cli.py serve` to use
     `stdio_server()`.
   - Port the §18 schema-pin tests to the new server (the pins are derived from
     `RunForgeInput.model_json_schema()` / `RunResult.model_json_schema()`, so
     they remain valid; only the assertion path changes).
   - Tests; `scripts/ci.sh` green.
4. **Update `CLAUDE.md`:** add this doc as a third normative source (alongside
   the base + hardening docs) and document the `§C*` and `C-Invariant N`
   citation namespaces (and the three inline `C-Decision 1..3` tags). Note the
   §18 schema-pin tests' source-of-truth shift (pydantic models, unchanged) if
   Path B was taken.

---

## C10. Decisions log (forge-mcp long-run continuity)

| § | Decision | Rationale |
|---|---|---|
| §C1.1 (**C-Decision 1**) | Adopt the `.experimental.` MCP task API now | Disconnect-survival win over a 10-hour run is too valuable to defer further; §H18's "when it stabilizes" call is exercised. |
| §C1.2 (**C-Decision 2**) | Mandatory verification gate before §C1 implementation | Path A vs Path B without checking burns the whole increment if wrong. |
| §C1.3 | `TASK_OPTIONAL`, not `TASK_REQUIRED` | Preserves the direct-call path for short tests and existing callers (back-compat). |
| §C1.4 | Single low-level `Server` migration as the Path B answer (not dual-server) | MCP stdio is single-server-per-process; dual-server is a permanent kludge to avoid a one-time rewrite. Migration is mechanical because the §18 schema pins derive from pydantic, not FastMCP. |
| §C1.5 | One new `Status` sink (`task.update_status`); no new status state | Mirrors the existing `ctx.report_progress` / `ctx.info` pattern; suppressed-on-raise. |
| §C1.6 | `task.is_cancelled` bridges to `CancelledError` via `poll_task_cancellation`; §8.5 stays the only authority | Centralizes cancellation forensics; no parallel terminal path to maintain. |
| §C2.3 | `sessions.json` per-iteration sidecar, not `state.json` extension | Matches the `eval.json` / `triage.json` pattern; keeps `state.json` minimal (§7); per-iteration scope keeps the artifact's iteration-scoped meaning truthful. |
| §C2.5 (**C-Decision 3**) | Orchestrator-owned write (drivers expose, orchestrator composes); forensic-only this round (no reader) | Drivers don't coordinate atomic writes; the orchestrator already coordinates per-iteration artifacts (mirrors `eval.md` composition from `eval.json`). A reader without a mid-phase resume design is a layering violation (mid-phase resume stays §H18-deferred — `C-Inv 3`). |
| — | Drop Jaccard non-progress (the third §H18 deferral) | No empirical evidence that exact-equality is too strict; §H18's adoption gate is honored. |

**Forward-looking (deferred, with stale-assumption notes):**

- Mid-phase resume layer that *reads* `iteration-N/sessions.json` and calls
  `ClaudeAgentOptions(resume=…)` / `AsyncCodex().thread_resume(…)` —
  unblocked by §C2 but stays §H18-deferred until the operational need is
  observed (recovering one iteration is the marginal win; iteration-boundary
  resume already covers the rest).
- HTTP-transport task support (the article and the SDK both support it via
  `streamable_http_app`) — out of scope for forge-mcp's stdio-only deployment;
  noted as future-work if a remote forge-mcp deployment is ever wanted.
- Jaccard-similarity non-progress threshold — adopt **once exact-equality
  proves too strict in practice** (the §H18 gate, unchanged).

---

## C11. Risks & verification notes for the implementer

1. **FastMCP task support (Path A) is the verification gate.** Resolve via
   §C8's `tests/test_fastmcp_task_support.py` before any other code change in
   this increment. Expected outcome based on context7 evidence: **Path A
   unavailable, proceed with Path B.**
2. **MCP task API is in the `.experimental.` namespace.** Pinning
   `mcp[cli] >=1.12,<2` does not protect against breaking changes within `1.x`
   for experimental APIs. Mitigation: bump `uv.lock` deliberately when the
   next `mcp` release lands; isolate task code in `server.py` (and any thin
   helper) so any breaking change is localized.
3. **`task.is_cancelled` polling cadence is sparse (phase boundaries only).**
   A long-running generator turn can take 30+ min; mid-turn cancellation only
   takes effect when the turn ends. Acceptable because (a) the §H10 SDK-level
   `interrupt()` covers urgent mid-turn cancellation via the `terminate()`
   backstop, and (b) sparse polling preserves the north star (no per-event
   live context). Do **not** push polling inside watchdog ticks — that re-opens
   the per-event coupling the architecture deliberately avoids.
4. **`session_id` extraction MUST fail-soft.** Claude SDK's init
   `SystemMessage` shape may evolve. The capture path SHOULD store `None`
   rather than raise — a `null` `session_id` in `sessions.json` is acceptable
   forensic; a raised exception during driver stream processing would
   terminate the run.
5. **Codex `thread.id` availability and thread-construction surface.** Today's
   `_codex.CodexRunnerImpl.turn()` holds
   `self._session = await thread.runs.create(developer_instructions=…)` (the
   assignment is at `_codex.py:234` of the current tree, inside the `turn()`
   async method declared at `_codex.py:209`; the hardening doc's §H10.1
   snapshot recorded the assignment at `_codex.py:209` — line numbers drift,
   so verify by symbol: the assignment is the only `thread.runs.create(...)`
   call inside `turn()`). The `AsyncThread` returned by the thread-construction
   call is consumed for `runs.create` but **not** retained on `self`. §C2
   requires retaining it as `self._thread` so `.id` is accessible after the
   call returns. Also note the SDK surface ambiguity: context7's current docs
   show `await codex.thread_start(...)` as the canonical entry, but the
   current code uses `await codex.threads.create()` — both return an
   `AsyncThread`, but reconcile (which surface to call) as part of the
   verification step. If `AsyncThread.id` is not exposed at the pinned
   `openai-codex` commit, treat the capture as fail-soft (set
   `last_thread_id = None`), mirroring §C11 risk 4.
6. **Path B port of §18 schema-pin tests.** The pins assert (a) input schema
   object-root with no top-level combinators, (b) cap fields expose
   `minimum`/`maximum`, (c) output schema object-root, (d) the §6.3‡ wire
   shape (`isError` text result, message text only, no numeric code on the
   wire). All four derive from `RunForgeInput.model_json_schema()` /
   `RunResult.model_json_schema()` and from the FastMCP/low-level `Server`
   tool-error translation, both of which remain valid after Path B. The
   assertion path moves from FastMCP-derived to `Tool(...)`-declared, but the
   asserted invariants are unchanged. Port the tests, do not delete them.
7. **TTL discipline at the caller.** A `call_tool_as_task` client that sets
   `ttl_ms < max_runtime_minutes * 60 * 1000` will have its task aged out by
   the server before the run terminates. Document the rule in README and in
   `RunForgeInput`'s docstring; do **not** enforce a TTL floor server-side
   (the caller's clock, not the server's, governs TTL semantics).
8. **Three normative docs now.** Update `CLAUDE.md`'s "Source of truth" line
   to name this companion (third normative source) and the `§C*` and `C-Inv N`
   citation namespaces (plus the three inline `C-Decision 1..3` tags); base-doc
   sections still win on anything they specify, and hardening-doc sections
   still win on anything they specify.
9. **`RunResult.task_id` is best-effort.** Context7 evidence shows the client
   receives `task.taskId` from `CreateTaskResult` but does **not** explicitly
   show a corresponding accessor on the server-side `ServerTaskContext`. If
   the pinned SDK exposes the id on `ServerTaskContext` (likely as
   `task.task_id` or `task.id`), populate `RunResult.task_id` from it; if it
   does not, leave the field `None` and document it as forward-looking. Do
   **not** invent the id server-side (e.g., by hashing). The client always
   has authoritative access to its own task_id via `CreateTaskResult`.
