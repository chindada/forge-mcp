# Ultrareview remediation — root-cause fix design

**Date:** 2026-05-23
**Status:** approved (design); pending implementation plan
**Scope:** the 6 verified ultrareview findings on `develop` **plus** the infected
siblings each root cause spread to. This is a remediation brief, not a normative
`§*` brief; it cites the base design doc and companion briefs by their existing
namespaces and changes nothing about `RunForgeInput` / `RunResult` schemas.

## Context

An ultrareview of `develop → main` produced 7 findings; one (`result.py:20`
"incomplete mislabeled as timeout") was withdrawn, leaving 6. An initial draft
fixed each at its cited line. A follow-up infection audit showed that **three of
the six root causes recur elsewhere** and two have a duplicated-logic root, so
the cited-line fixes were treating symptoms. This brief fixes the root causes and
their siblings.

A throwaway draft of the cited-line fixes exists in the working tree; it will be
**reverted** before this plan is executed. Do not build on it.

## Findings and infection map

| # | Cited symptom | Root cause | Infected siblings |
|---|---------------|-----------|-------------------|
| 1 | `apply_caps_and_overflow` runs twice on the timeout path (`engine.py`) | the inline path and `handle_timeout`/`handle_failure` each re-implement the full terminal-finalization sequence; caps is the step that overlapped | the whole finalization tail is duplicated 3× |
| 3 | multi-line gap prose breaks tables (`lineage.py`) | `escape_md_inline` escapes `\`/`|` but preserves newlines | none — all 3 tables (`render_eval_md`, lineage unresolved + design-flaw) route every cell through `escape_md_inline`, so the shared escaper fix covers all |
| 4 | skill-probe failure escapes `prepare_run` untagged (`preflight.py`) | `prepare_run` raises non-`McpError` exceptions on several paths; the server boundary tags only `ValidationError` | `_load_design_doc` (`read_text`), `_ensure_harness` (`mkdir`/`os.open`), lock-file IO; `run_forge_handler` has no catch-all |
| 5 | `list_changed` broadcast task may be GC'd (`resources.py`) | bare `create_task` keeps only a weak ref | none — `watchdog.py` binds its tasks to locals and `await`s them |
| 6 | resume wipes `last_completed_iteration` to 0 (`engine.py`) | `RunStateMachine.__init__` rewrites `state.json` with a freshly-constructed state instead of continuing the durable one | `started_at` (forensic "when rooted", §7) is also reset; `iteration` too |
| 7 | `prune_old_runs` deletes any 8-char dir (`artifacts.py`) | run-id shape check uses `len==8`, not the `^[0-9a-f]{8}$` shape | **4 copies** of the run-id regex already exist (`server.py:71`, `resources.py:20`, `lineage.py:22`, + the draft's 4th); `resume.py` and `cross_design.py` glob `*/state.json` with no shape check |

## Design principles applied

- **Single source of truth / DRY** (findings 6, 7): one durable record, one run-id
  matcher — not N copies that drift.
- **Defense in depth** (finding 4): translate known domain errors to specific
  tags **and** a boundary catch-all so nothing crosses the wire untagged (§W2,
  Rule 8 fail-loud).
- **Don't repeat the finalization sequence** (finding 1): one terminal tail,
  called once per path, makes double-application structurally impossible.
- **Follow the platform's documented contract** (finding 5): the CPython
  `asyncio.create_task` docs require holding a strong reference to fire-and-forget
  tasks (the event loop keeps only weak refs); the fix uses the documented
  set + `add_done_callback(discard)` idiom.
- **Preserve pinned invariants** (finding 1): §8.5 cancellation ordering and §8.1
  control-flow are load-bearing; the refactor keeps every invariant test and is
  validated by the full suite.

## Workstreams

### A. Unify terminal finalization (finding 1)

Extract the duplicated tail into one helper in `orchestrator/lifecycle.py`:

```
async def _finalize_terminal(sm, ledger, deps, *, status, reason=None) -> None
```

Tail = `sm.transition(status, reason=reason)` → `emit_state` →
`write_design_flaws` (OSError-guarded) + `emit_path` →
`apply_caps_and_overflow` + overflow `emit_path`s → `emit_terminal_status(status)`.

Each path keeps only its prologue and calls the tail exactly once:

- **inline** (`engine.run`): `transition("finalizing")` + emit; if incomplete,
  `ledger.unresolved_gaps = collect_unresolved_gaps(run_dir)`; `decided_at`; then
  tail with `reason = ledger.stop_reason if (status=="incomplete" and
  ledger.stop_reason) else None`. **The post-`try` caps block is deleted.**
- **`handle_timeout`**: `transition("finalizing")` + emit; `close_drivers`;
  collect gaps; `decided_at`; tail(`status="incomplete"`).
- **`handle_failure`**: `close_drivers`; failure metadata (`failed_phase`,
  `error_class`, `error_message`, truncate-before-build `traceback_truncated`);
  best-effort gaps; `decided_at`; tail(`status="failed"`, `reason=str(exc)`).
  Deliberately **no** `finalizing` transition (failures jump straight to failed).
- **`handle_cancellation`** is **untouched** — §8.5's five-step ordering produces
  no terminal `RunResult` and must not call the tail.

**Observable change:** on the inline path, caps + overflow emits now run *before*
`emit_terminal_status` (matching the handlers), so overflow artifacts are durable
before the terminal status notification. Caps run exactly once on every
result-producing path, by construction.

### B. Centralize the run-id matcher (finding 7)

`resources.py` is a pinned **stdlib-only leaf** (§R5.1 / §R9.1: no `mcp.*`,
`orchestrator`, `drivers`, `logging`), so it cannot import from `state.py`
(pydantic). The canonical matcher therefore lives in a new stdlib-only leaf:

```
# forge_mcp/ids.py  — imports only `re`
RUN_ID_PATTERN = r"^[0-9a-f]{8}$"
RUN_ID_RE = re.compile(RUN_ID_PATTERN)
def is_run_id(name: str) -> bool: return RUN_ID_RE.match(name) is not None
```

Wire-ins:
- `state.py` `RunState.run_id` Field `pattern=RUN_ID_PATTERN` (schema stays the SoT
  shape, now shared).
- Replace the 3 module-level copies in `server.py`, `resources.py`, `lineage.py`.
- `prune_old_runs`: replace `len(name)==8` with `is_run_id(name)`.
- `resume.find_resumable_run` and `cross_design.find_cross_design_patterns`: skip
  `state_path` whose `parent.name` is not `is_run_id` (defensive shape check on the
  `*/state.json` globs).
- Update the §R9.1 module-isolation test to permit the new stdlib-only leaf import
  — the "no mcp/orchestrator/drivers/logging" guarantee is preserved (`ids` imports
  only `re`).

### C. Make pre-run failures exception-safe (finding 4)

Defense in depth, two layers:
1. **Specific translation** in `prepare_run`: wrap `probe_required_skills` and map
   `SkillMissingError` / `SkillProbeTimeout` → `_raise(SERVER_ERROR,
   kind="infra_failure")`. (`doctor`/`cli.py` catches these directly and is
   unaffected.)
2. **`prepare_run` catch-all**: `except McpError: raise` then `except Exception as
   exc: _raise(SERVER_ERROR, f"preflight failed: ...", kind="infra_failure")`.
   `Exception` excludes `CancelledError`/`KeyboardInterrupt`/`SystemExit`
   (BaseException), which still propagate; already-tagged `McpError`s pass through;
   the existing post-lock `except` still releases the lock before the re-raised
   `McpError` reaches the catch-all.
3. **Boundary catch-all** in `run_forge_handler`: wrap the `prepare_run` (and
   dispatch) call so any unexpected non-`McpError` becomes a tagged
   `McpError(SERVER_ERROR, infra_failure)` — belt-and-suspenders for §W2.

### D. Read-and-continue durable state on resume (finding 6)

In `engine.run()`, when `self._prepared.resume_point` is set, seed the state
machine from the durable record rather than a fresh zeroed state:

```
if resume_point is not None:
    try:
        initial = read_state(run_dir / "state.json")   # preserves started_at,
                                                        # iteration, lci, run_id
    except Exception:
        initial = <fresh RunState seeded with resume_point anchor>  # defensive
else:
    initial = <fresh RunState, started_at=now, iteration=0>
sm = RunStateMachine(run_dir / "state.json", initial)
```

`build_result` computes runtime from its own process-local `started_at` param
(`result.py:223`), so preserving the durable `RunState.started_at` is forensically
correct **and** leaves session runtime accounting intact (verified). The first
real transition advances `state` forward; the durable anchor survives a re-crash
before the next `iter_done`.

### E. Newline-safe table cells (finding 3)

`escape_md_inline` collapses `\r\n` / `\r` / `\n` → `<br>` (a GFM cell line-break)
**after** the existing backslash/pipe escaping (`<br>` carries no further-escaped
char). Single shared escaper → fixes all three tables. Documented example extended;
the §10.4 pin's existing example (`a|b\c → a\|b\\c`) is unaffected.

### F. Anchor the broadcast task (finding 5)

Module-level `_BROADCAST_TASKS: set[asyncio.Task]`; `_fire_list_changed` adds the
task and registers `task.add_done_callback(_BROADCAST_TASKS.discard)` — the
documented CPython idiom. `asyncio` becomes a module-level import in `resources.py`
(stdlib — leaf constraint preserved).

## Test plan (pin behavior first, §18 style)

- **A:** `apply_caps_and_overflow` runs **exactly once** on each of inline-incomplete,
  timeout, and failure paths (count across the engine + lifecycle bindings); existing
  §8.5/§8.1 invariant tests stay green.
- **B:** `is_run_id` unit table; `prune` keeps 8-char non-hex / uppercase / `.gitignore`
  siblings and prunes a real run-id; `resume`/`cross_design` skip a non-run dir that
  happens to contain a `state.json`.
- **C:** `prepare_run` surfaces a tagged `McpError` (prefix via `tag()`) for a missing
  skill, a skill-probe timeout, and a raw `OSError` (e.g. unreadable design doc);
  `run_forge_handler` boundary tags an unexpected exception.
- **D:** resume preserves durable `started_at` **and** `last_completed_iteration`
  before any new `iter_done`; cold-start still starts at 0.
- **E:** newline → `<br>`; pipes/backslashes still escaped; no `\n` in output.
- **F:** `_fire_list_changed` anchors the task in `_BROADCAST_TASKS` and the
  done-callback empties it after completion.

## Out of scope / non-goals

- No `RunForgeInput` / `RunResult` schema changes (preserves the §18 schema pin).
- No change to §8.5 cancellation ordering or `handle_cancellation`.
- No Playwright reintroduction (§15).
- The `started_at`-reset-on-resume *policy* is decided here (preserve), not deferred.

## Risks

- **Workstream A** rewrites §8.5/§8.1-pinned ordering. Mitigation: keep all
  invariant tests; add the once-only caps tests on all three paths; run the full
  fast suite. The inline caps-before-terminal-status reorder is the only intended
  behavior change and is consistent with the existing handlers.
- **Workstream B** touches the §R9.1 isolation pin. Mitigation: the new leaf is
  stdlib-only; update the pin to assert the leaf set still excludes
  mcp/orchestrator/drivers/logging.
