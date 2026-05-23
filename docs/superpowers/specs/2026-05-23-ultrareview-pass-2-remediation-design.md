# Ultrareview pass-2 remediation — root-cause fix design

**Date:** 2026-05-23
**Status:** approved (design); pending implementation plan
**Scope:** the 9 unconfirmed-but-real findings surfaced by the second pass over
the ultrareview report on `develop`, plus their infected siblings. This is a
remediation brief, not a normative `§*` brief; it cites the base design doc and
companion briefs by their existing namespaces and changes nothing about
`RunForgeInput` / `RunResult` schemas (preserves the §18 schema pin) and does
**not** extend the §W1 closed `FailureKind` taxonomy.

## Context

The first ultrareview pass produced 7 findings; 6 were addressed in
`2026-05-23-ultrareview-remediation-design.md`. A re-audit of the report's
unconfirmed remainder produced 9 additional findings that are all real defects.
Three are silent-correctness bugs (resume divergence, schema-retry bypass,
defensive-parse asymmetry); three are best-effort policy violations that abort
or wire-escape a run on transient IO faults; three are UX / fail-fast gaps
(opaque env-var error, silent planner failure, UTF-8 truncation off-by-one).

The first pass favored root-cause fixes over symptom patches and applied an
infection-map discipline (see the *Findings and infection map* table in the
prior brief). This brief follows the same discipline: where a single root
cause manifests at more than one call site, the fix lands at every site;
where the cited line is a structural divergence, the fix removes the
divergence rather than papering over the reader.

## Findings and infection map

| # | Cited symptom | Root cause | Infected siblings |
|---|---|---|---|
| 1 | Resume fingerprint history diverges from live (`engine.py:50`) | `_reconstruct_fingerprints` reads raw `EvalResult.gaps` from `eval.json`; live `phases.py:625` fingerprints `eval_for_loop.gaps` — post-triage, plus `carried_gaps`, plus synthesized Rule-11 / verify-fail gaps | structural two-source divergence — fixed by persisting the live fingerprint, not by re-running business logic on resume |
| 2 | `find_resumable_run` resumes client-cancelled runs (`resume.py:13`) | `_TERMINAL` skip set does not include the `cancelling` state nor the `state.cancelled` flag; mid-cancellation crashes (or SIGKILL between cancellation transitions) leave a durable `cancelling`/`cancelled=True` state that the resume scan treats as resumable | only `resume.py:find_resumable_run` consumes `_TERMINAL` |
| 3 | `handle_timeout` crashes uncaught on malformed `eval.json` (`lifecycle.py:182`) | `collect_unresolved_gaps` runs `EvalResult.model_validate_json` and raises on corruption; `handle_failure` (lifecycle.py:220-224) guards the call, the timeout path does not | engine inline-incomplete path at `engine.py:397` has the same unguarded `collect_unresolved_gaps` call |
| 4 | `FORGE_KEEP_RUNS` env var has no validation (`config.py:79`) | inline `int(os.environ.get("FORGE_KEEP_RUNS", "10"))` with no try/except and no range check; sister `FORGE_LINEAGE_TOP_K` (`config.py:66-74`) validates type and range explicitly | only `FORGE_KEEP_RUNS` — `FORGE_HARNESS_ROOTS` validates, `FORGE_CODEX_BIN` is unconstrained string |
| 5 | `cross_design_patterns.md` write failure aborts run (`engine.py:349`) | unguarded `atomic_write_text` in cold-start feed-forward; sibling cold-start writes (`design.fingerprint` at engine.py:291-300, lineage at engine.py:307-339, prune at 274-281) ARE guarded with `OSError` → ledger warning | only this one cold-start write |
| 6 | Pydantic `ValidationError` bypasses schema retry (`evaluator.py:100, 140`) | `_run_and_parse_json` wraps `json.JSONDecodeError` in `OutputSchemaError`; `EvalResult.model_validate(...)` and `TriageResult.model_validate(...)` are called *outside* that helper, so pydantic `ValidationError` is not wrapped and `with_schema_retry` (which catches only `OutputSchemaError`) does not retry on schema mismatch | both `EvaluatorDriver.evaluate()` and `EvaluatorDriver.triage_design_flaws()` |
| 7 | Silent planner failure → `FileNotFoundError` deep in `_seed_start_contract` (`planner.py:51`) | `write_plan` returns `None` when neither `plan.md` exists nor a `Write` tool_use can be recovered; `run_plan_phase` (`phases.py:127-141`) re-authors only when `plan_path.exists() and problems`, so a missing plan is never detected; downstream `_seed_start_contract` (`phases.py:315`) reads `plan/plan.md` unconditionally | `evaluator.write_remediation` has the same silent-None shape, but its consumer (`phases.py:658-685`) handles the miss via the degenerate-remediation synthesis path, so the bug doesn't manifest there |
| 8 | `apply_caps_and_overflow` OSError surfaces as `McpError` (`lifecycle.py:253`) | unguarded `atomic_write_text` for the two `*-overflow.md` files in `apply_caps_and_overflow`; `_finalize_terminal` wraps `write_design_flaws` (lifecycle.py:123-131) but not `apply_caps_and_overflow` (line 132); OSError propagates up, hits the server boundary catch-all at `server.py:385-388`, returns `McpError(SERVER_ERROR)` instead of `RunResult(failed)` — violates the §6.3 bright-line | only this caps step |
| 9 | `write_prior_attempts` UTF-8 byte cap splits multi-byte characters (`artifacts.py:241`) | no-newline walk-back loop checks `encoded[cut - 1] & 0xC0 == 0x80` (the last byte of the left half); to land at a UTF-8 character boundary the check must be on `encoded[cut] & 0xC0 == 0x80` (the first byte of the right half); off-by-one means a lead byte at `cut-1` stops the loop with the lead included and continuations dropped, decoding via `errors="replace"` to `U+FFFD` | none — only this single occurrence |

## Design principles applied

- **Single source of truth** (C): persist what was actually computed live;
  never recompute business logic on resume.
- **Symmetric error handling** (B, F, G): the same kind of fault gets the
  same treatment everywhere. Best-effort writes are uniformly guarded;
  defensive parses are uniform across timeout / inline-incomplete / failure
  paths.
- **Fail fast and loud** (Rule 8 — A, E, H): silent `None` returns and
  bare `int()` `ValueError`s become typed errors with descriptive messages.
- **§6.3 bright-line preserved** (G, H): in-run failures return
  `RunResult(failed)`, never raise `McpError`. The unguarded OSError in
  apply-caps (8) and the unguarded silent-planner FileNotFoundError (7) both
  currently violate this — the fixes restore terminal-RunResult semantics.
- **DRY env-var parsing** (E): one helper shared by all bounded-int env vars.
- **No new failure_kind** (H): §W1's six-kind taxonomy is closed; new typed
  exception classes are introduced freely (they land in `error_class`), and
  `failure_kind` is decided by `result._failure_kind_for(status)` from the
  existing mapping — `failed → infra_failure`, `incomplete → timeout` — so
  no mapping change is needed.
- **§R-Inv 3 allowlist not denylist** (C): the new `gap_fingerprint.json`
  artifact is added to `_ALLOWED_ARTIFACTS` and the iteration-dir walker.
- **CLAUDE.md Rule 21**: every new helper / class carries the three-section
  `Design:` / `Implementation:` / `Example:` docstring (enforced by
  `scripts/check_docstrings.py`).

## Workstreams

### A. Schema-retry catches all schema deviations (finding 6)

`_run_and_parse_json` in `drivers/evaluator.py` (lines 189-206) is renamed and
generalized to `_parse_and_validate` so the parse step and the Pydantic
validation step share the *same* error wrapping. Signature:

```python
def _parse_and_validate(
    model: type[BaseModelT],
    structured: dict | None,
    raw_text: str,
) -> BaseModelT
```

The helper:
1. Resolves the dict (from `structured` if present, else `json.loads(raw_text)`
   with `JSONDecodeError` → `OutputSchemaError(raw=raw_text, reason=str(exc))`).
2. Calls `model.model_validate(dict)`; `pydantic.ValidationError` →
   `OutputSchemaError(raw=raw_text, reason=str(exc))` via `raise ... from exc`.
3. Returns the validated model.

Both `EvaluatorDriver.evaluate()` and `EvaluatorDriver.triage_design_flaws()`
replace their `Model.model_validate(_run_and_parse_json(...))` two-step with
a single `_parse_and_validate(Model, result.structured, result.text)` call.

`with_schema_retry` is unchanged — it already catches `OutputSchemaError` and
retries with `retry=True`. The retry now covers pydantic-schema mismatch, which
was the most common silent failure mode the prior code could not recover from.

**Imports added to `drivers/evaluator.py`:**

```python
from typing import TypeVar
from pydantic import BaseModel

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)
```

Pattern mirrors `orchestrator/retry.py:7,11` and `orchestrator/watchdog.py:9,11` (single-letter TypeVars already in use across the codebase).

### B. Defensive `unresolved_gaps` collection symmetric across all paths (finding 3 + engine.py:397 sibling)

New helper in `lifecycle.py`:

```python
def collect_unresolved_gaps_safe(run_dir: Path, logger: Any) -> list[EvalGap]
```

Wraps `collect_unresolved_gaps(run_dir)` in `try / except Exception` (with
`# noqa: BLE001` — codebase precedent in `engine.py:339`, justified because
the latest `eval.json` may fail any of OSError / `json.JSONDecodeError` /
`pydantic.ValidationError` and the original terminal signal must not be
masked). On failure: `logger.warning(..., exc_info=True)` and return `[]`.

Three call sites switch to the helper:

- `handle_timeout` (`lifecycle.py:196`) — currently unguarded; now safe.
- `handle_failure` (`lifecycle.py:220-224`) — currently has a local
  try/except that suppresses silently; now uses the helper for symmetry and
  for the warning log.
- `engine.run` inline-incomplete branch (`engine.py:397`) — currently
  unguarded; now safe.

`collect_unresolved_gaps` itself (`lifecycle.py:229-250`) is unchanged — it
remains the strict-parse leaf; only callers gain the safe-wrap.

### C. Persist `gap_fingerprint.json` sidecar for resume parity (finding 1)

A new per-iteration artifact records the live fingerprint exactly as
`phases.py` computed it, so resume reads the persisted truth rather than
re-deriving from `eval.json`.

**Imports added** (per side):
- `phases.py`: extend the existing `from ..artifacts import` line to include `atomic_write_json` (currently imports only `atomic_write_text, write_sessions_json` at `phases.py:14`).
- `engine.py`: add `import json` (currently `asyncio, inspect, logging, os` only at `engine.py:5-9`).

**Write side** (`phases.py:run_iteration_loop`):

The current ordering at lines 622-625 is:

```python
sm.transition("iter_done", iteration=iteration_n, last_completed_iteration=iteration_n)
lifecycle.poll_task_cancellation(task)
ledger.completed_phases.append(f"iter-{iteration_n}")
ledger.gap_fingerprints.append(fingerprint_gaps(eval_for_loop.gaps))
```

Reorder so the sidecar is durable **before** `iter_done`:

```python
fingerprint = fingerprint_gaps(eval_for_loop.gaps)
atomic_write_json(
    iteration_dir / "gap_fingerprint.json",
    sorted(fingerprint),  # JSON-serializable; sorted for deterministic content
)
await deps.emitter.emit_iteration(iteration_n, "gap_fingerprint.json")  # §S5.2

sm.transition("iter_done", iteration=iteration_n, last_completed_iteration=iteration_n)
lifecycle.poll_task_cancellation(task)
ledger.completed_phases.append(f"iter-{iteration_n}")
ledger.gap_fingerprints.append(fingerprint)
```

§H-Inv 2 (durable resume anchor) is preserved: when `state.json` shows
`last_completed_iteration=N`, `iteration-N/gap_fingerprint.json` is already
fsync'd on disk because `atomic_write_json` → `atomic_write_text` flushes and
fsyncs the temp file before `os.replace` (`artifacts.py:32-37`).

**Read side** (`engine.py:_reconstruct_fingerprints`):

The function gains a `ledger` parameter for surfacing the legacy-fallback warning. The lone caller at `engine.py:373` is updated accordingly:

```python
ledger.gap_fingerprints = _reconstruct_fingerprints(run_dir, resume_point, ledger)
```

Body:

```python
def _reconstruct_fingerprints(run_dir: Path, point: ResumePoint, ledger: RunLedger) -> list[frozenset[str]]:
    history: list[frozenset[str]] = []
    legacy_fallback = False
    for iteration_n in range(1, point.last_completed_iteration + 1):
        iteration_dir = run_dir / f"iteration-{iteration_n}"
        sidecar = iteration_dir / "gap_fingerprint.json"
        if sidecar.exists():
            try:
                entries = json.loads(sidecar.read_text())
                history.append(frozenset(entries))
                continue
            except Exception:  # noqa: BLE001 — best-effort sidecar parse; fall through to legacy.
                pass
        # Legacy fallback: pre-pass-2 run with no sidecar. Reconstruct from
        # eval.json — known-lossy (no triage filter, no synthesized gaps),
        # surfaced as a one-time ledger warning so operators see the drift.
        legacy_fallback = True
        eval_path = iteration_dir / "eval.json"
        if not eval_path.exists():
            continue
        try:
            gaps = EvalResult.model_validate_json(eval_path.read_text()).gaps
        except Exception:  # noqa: BLE001 — best-effort legacy reconstruction.
            continue
        history.append(fingerprint_gaps(gaps))
    if legacy_fallback:
        ledger.warnings.append(
            "resumed run lacks gap_fingerprint.json sidecars from prior iterations; "
            "fingerprint history reconstructed from eval.json (approximate, "
            "missing triage filter and synthesized gaps)"
        )
    return history
```

**Resource layer** (`resources.py`):

Add to `_ALLOWED_ARTIFACTS`:

```python
_ArtifactPattern(
    None,
    re.compile(r"^iteration-([1-9]\d*)/gap_fingerprint\.json$"),
    "application/json",
),
```

Add `"gap_fingerprint.json"` to the iteration-dir name tuple in
`expand_scope_to_resources` (`resources.py:348-358`).

**Tests:** `tests/test_resources.py:189-196` enumerates concrete iteration-dir
subpaths in its allowlist expectation; the test fixture's tuple must gain
`"gap_fingerprint.json"` in lockstep with the resource-layer change, or the
allowlist-coverage test will fail.

§R5.1 stdlib-only leaf constraint preserved (no new imports). §R-Inv 3
allowlist-not-denylist preserved.

### D. Resume scan respects `state.cancelled` (finding 2)

`resume.py:51` change one line:

```python
if state.state in _TERMINAL:
    continue
```

becomes:

```python
if state.state in _TERMINAL or state.cancelled:
    continue
```

Closes the narrow window where a SIGKILL or disk write failure between
`handle_cancellation`'s `cancelling` and `failed` transitions
(`lifecycle.py:173-178`) leaves a durable `state="cancelling", cancelled=True`
that the prior scan would treat as resumable. §C-Inv 1 (single cancellation
owner) preserved — the cancelled flag is the durable signal the client owned
this run's termination.

No schema change: `state.cancelled` already exists (`state.py:57`).

### E. Env-var validation parity via shared helper (finding 4)

Extract a helper in `config.py`:

```python
def _parse_bounded_int_env(name: str, *, default: int, low: int, high: int) -> int:
    """Read a bounded-int environment variable with a descriptive error.

    Design: §6.5 / Rule 8 — both env vars in `from_env` need identical
        type+range validation, so the helper is the single source of truth.
    Implementation: read os.environ with default, parse with try/except,
        check `low <= value <= high`, raise ValueError with a name-aware
        message chained from the original via `from exc`.
    Example: keep_runs = _parse_bounded_int_env(
        "FORGE_KEEP_RUNS", default=10, low=0, high=1000).
    """
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer in [{low}, {high}], got {raw!r}"
        ) from exc
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}], got {value}")
    return value
```

Both bounded-int env-var reads go through it:

```python
keep_runs = _parse_bounded_int_env("FORGE_KEEP_RUNS", default=10, low=0, high=1000)
lineage_top_k = _parse_bounded_int_env("FORGE_LINEAGE_TOP_K", default=4, low=0, high=10)
```

The `FORGE_KEEP_RUNS` range `[0, 1000]` is added to CLAUDE.md's env-var
table (the row currently reads `... | 10`; updated to note `[0, 1000]`).
Rationale: 1000 completed-run dirs at typical per-run size ≤10MB caps disk
burn at ~10GB; effectively unbounded for realistic operators while rejecting
pathological values. `prune_old_runs`'s pre-existing
`if keep_last < 0: return` defensive guard (`artifacts.py:99`) stays —
belt-and-suspenders.

### F. Cross-design feed-forward write is best-effort (finding 5)

`engine.py:348-350` is wrapped to mirror the established cold-start guard
pattern (compare `design.fingerprint` block at lines 291-300):

```python
try:
    digest = render_cross_design_digest(self._prepared.harness_dir, logger)
    atomic_write_text(inputs_dir / "cross_design_patterns.md", digest)
    await emitter.emit_path("inputs/cross_design_patterns.md")  # §S5.2 / §X7
except OSError as exc:
    ledger.warnings.append(
        f"cross_design_patterns.md write failed (cross-design context disabled): "
        f"{type(exc).__name__}: {exc}"
    )
    logger.warning("cross_design_patterns.md write failed", exc_info=True)
```

The exception type stays narrow (`OSError`) because `render_cross_design_digest`
is internally fail-soft per §X7 / `X-Decision 9` (aggregator failure → empty
digest, never raised) — any non-OSError out of the digest renderer is a
programmer error and should propagate.

### G. Overflow writes are best-effort; cap always applies (finding 8)

`apply_caps_and_overflow` (`lifecycle.py:253-285`): wrap each
`atomic_write_text` (lines 268, 277) in `try / except OSError`. On failure:
append a ledger warning, leave the corresponding `*_overflow_path` as `None`,
**still apply the cap slicing** so the in-memory list fits `RunResult`'s
implicit budget.

```python
if unresolved_overflow is not None:
    path = run_dir / "unresolved-gaps-overflow.md"
    try:
        atomic_write_text(path, unresolved_overflow)
        ledger.unresolved_overflow_path = str(path)
    except OSError as exc:
        ledger.warnings.append(
            f"unresolved-gaps-overflow.md write failed: {type(exc).__name__}: {exc}"
        )
        if logger is not None:
            logger.warning("unresolved-gaps-overflow.md write failed", exc_info=True)
    ledger.unresolved_gaps = ledger.unresolved_gaps[:GAP_LIST_CAP]
```

(Symmetric block for `design-flaw-gaps-overflow.md`.)

`_finalize_terminal` (`lifecycle.py:120-137`) is otherwise unchanged: its
existing `if ledger.unresolved_overflow_path:` guard already skips the
emit when the path is None, so write failure produces no spurious
`resources/updated` notification (§S5.2 / §S-Inv 4 preserved).

Net result: `_finalize_terminal` never raises an unguarded `OSError` →
terminal `RunResult` is always produced → §6.3 bright-line restored.

### H. Planner failure becomes terminal `RunResult(failed)` with descriptive error (finding 7)

New typed exception in `errors.py`:

```python
class PlannerNoOutputError(RuntimeError):
    """Raised when run_plan_phase cannot produce a plan.md (§9.1).

    Design: §9.1 / §6.3 — the planner phase is in-run, so failure must
        terminate as RunResult(failed) with a descriptive error_class, not
        as a FileNotFoundError surfaced from a downstream reader.
    Implementation: thin RuntimeError subclass; caught by engine.run's
        except-Exception handler and routed through handle_failure.
    Example: raise PlannerNoOutputError("planner produced no plan.md ...").
    """
```

`run_plan_phase` (`phases.py:run_plan_phase`) becomes symmetric across the
"missing" and "degenerate" failure modes — re-author once for **either**,
then raise if plan.md is still missing:

```python
async def run_plan_phase(...):
    sm.transition("planning")
    lifecycle.poll_task_cancellation(task)
    await deps.status.update(phase="planning", agent="planner", message="writing plan")
    planner_ctx = RunContext(...)
    started = _utcnow_iso()
    warning = await with_transient_retry(
        lambda: deps.drivers.planner.write_plan(planner_ctx),
        is_transient=is_transient_claude,
    )
    completed = _utcnow_iso()
    if warning:
        ledger.warnings.append(warning)
    plan_path = deps.run_dir / "plan" / "plan.md"
    problems: list[str] = []
    if plan_path.exists():
        problems = validate_plan(plan_path.read_text())
    # Re-author once on EITHER missing or degenerate.
    if not plan_path.exists() or problems:
        warning = await with_transient_retry(
            lambda: deps.drivers.planner.write_plan(planner_ctx),
            is_transient=is_transient_claude,
        )
        if warning:
            ledger.warnings.append(warning)
        if plan_path.exists():
            problems = validate_plan(plan_path.read_text())
    # Fail fast and loud after the second attempt — Rule 8.
    if not plan_path.exists():
        raise PlannerNoOutputError(
            "planner produced no plan.md and Write tool_use recovery failed "
            "after two attempts"
        )
    if problems:
        ledger.warnings.append(f"plan.md degenerate after re-author: {problems}")
    await deps.emitter.emit_path("plan/plan.md")  # §S5.2
    ledger.completed_phases.append("plan")  # §7 / §9.1
    write_sessions_json(...)
    await deps.emitter.emit_path("plan/sessions.json")  # §S5.2
    sm.transition("planned")
    lifecycle.poll_task_cancellation(task)
```

Engine's existing `except Exception as exc:` (engine.py:416) catches
`PlannerNoOutputError`; `handle_failure` (lifecycle.py:201-226) builds
`RunResult(status="failed", failed_phase="planning",
error_class="PlannerNoOutputError", error_message="planner produced no ...")`.
`failure_kind` lands on `"infra_failure"` automatically because
`result._failure_kind_for("failed")` returns `"infra_failure"` (`result.py:29`)
— no mapping change needed here, the §W1 taxonomy stays closed, and operators
get both the categorical `infra_failure` signal and the descriptive
`error_class="PlannerNoOutputError"`.

### I. UTF-8 truncation off-by-one fix (finding 9)

`artifacts.py:write_prior_attempts` no-newline branch (lines 242-245):

```python
if cut == -1:
    cut = _PRIOR_ATTEMPTS_MAX_BYTES
    # Walk back until encoded[cut] is NOT a UTF-8 continuation byte (10xxxxxx).
    # encoded[cut] is the FIRST byte of the right half; it must begin a new
    # character. The prior version checked encoded[cut-1] (last byte of the
    # left half), which stops one byte too late when cut-1 is a lead byte and
    # cut..cut+k-1 are the continuations — leaving the lead byte stranded in
    # the left half and decoded as U+FFFD via errors="replace".
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
```

The newline-aligned branch (the common case) is unchanged: `\n` (0x0A) cannot
appear inside a multi-byte UTF-8 sequence, so `rfind` always lands on a
character boundary.

## Test plan (§18 style — pin behavior first)

For each workstream, a focused pinning test or paired tests:

- **A:** evaluator returns valid JSON whose dict fails `EvalResult` schema
  validation → `OutputSchemaError` raised, `with_schema_retry` calls
  `evaluate(retry=True)` exactly once, and the retry prompt carries the
  verbatim `SCHEMA_RETRY_SUFFIX` (§18 schema-retry-suffix pin stays green).
  Identical assertion for `triage_design_flaws`.
  (`tests/test_evaluator_driver.py`)
- **B:** `handle_timeout` with a corrupted `iteration-N/eval.json` produces
  `RunResult(status="incomplete", unresolved_gaps=[])` and a ledger warning,
  no exception escapes. Same for `engine.run`'s inline-incomplete path.
  `handle_failure` still produces `RunResult(failed)` with the original
  exception's metadata preserved (corrupt `eval.json` does not mask the
  failure cause). (`tests/test_lifecycle.py`)
- **C:** (1) after one iteration in a live run, `iteration-1/gap_fingerprint.json`
  exists and equals `sorted(list(fingerprint_gaps(eval_for_loop.gaps)))`;
  (2) a resumed run reads the sidecar and `ledger.gap_fingerprints` matches
  the live history exactly; (3) a resume from a run with missing sidecars
  falls back to eval.json and emits exactly one ledger warning about
  approximate history. (`tests/test_resume.py`, `tests/test_phases.py`)
- **D:** `state.json` with `state="cancelling", cancelled=True,
  last_completed_iteration=2` → `find_resumable_run` returns `None`. Also
  pin: `state="iter_generating", cancelled=False, last_completed_iteration=2`
  is still picked up (cancelled flag does not affect normal crash recovery).
  (`tests/test_resume.py`)
- **E:** `FORGE_KEEP_RUNS="abc"` → `ValueError("FORGE_KEEP_RUNS must be an
  integer in [0, 1000], got 'abc'")`; `="-1"` → range ValueError; `="0"`,
  `="10"`, `="1000"` all pass. Parametrized symmetric assertion for
  `FORGE_LINEAGE_TOP_K`. (`tests/test_config.py`)
- **F:** monkeypatch `atomic_write_text` to raise `OSError` for
  `inputs/cross_design_patterns.md`; full run completes with the otherwise-
  expected terminal status and ledger warning prefix `"cross_design_patterns.md
  write failed"`. (`tests/test_orchestrator.py`)
- **G:** `apply_caps_and_overflow` with `ledger.unresolved_gaps` exceeding
  `GAP_LIST_CAP` and `atomic_write_text` raising `OSError` → returns normally,
  `ledger.unresolved_overflow_path is None`, `len(ledger.unresolved_gaps) ==
  GAP_LIST_CAP`, ledger warning prefix
  `"unresolved-gaps-overflow.md write failed"`. Same shape for the design-flaw
  overflow. `_finalize_terminal` then produces a terminal `RunResult` (no
  McpError). (`tests/test_lifecycle.py`)
- **H:** two tests are needed at distinct boundaries.
  *(1) Unit test against `run_plan_phase` (`tests/test_phases.py`):* planner
  runner returns no plan.md across two attempts → `run_plan_phase` raises
  `PlannerNoOutputError`; verify the second attempt is actually invoked when
  the first leaves plan.md missing.
  *(2) Integration test through the engine (`tests/test_orchestrator.py`):*
  same planner-no-output scenario → engine catches via `except Exception`
  and `handle_failure` produces `RunResult(status="failed",
  failed_phase="planning", error_class="PlannerNoOutputError",
  failure_kind="infra_failure")` with a descriptive `error_message`. The
  bright-line is the integration test (no `McpError` raised, §6.3 preserved).
- **I:** 33000-byte string of only 3-byte UTF-8 characters and no newlines →
  `write_prior_attempts` writes head and overflow whose round-tripped UTF-8
  contains no `U+FFFD`. Also pin: 33000 bytes with a newline at byte 30000
  cuts at that newline (unchanged behavior). (`tests/test_artifacts.py`)

Existing tests stay green; in particular §C-Inv 1 (single cancellation
owner), §H-Inv 2 (durable resume anchor), §H-Inv 3 (resume append-only —
new sidecar is per-iteration, not modifying completed-iteration artifacts),
§8.5 ordering, §R-Inv 3 (allowlist not denylist — `gap_fingerprint.json`
added to the allowlist), and §18 schema pin all untouched.

## Out of scope / non-goals

- No `RunForgeInput` / `RunResult` schema changes (preserves §18 schema pin).
- No `§8.5` cancellation ordering changes (Workstream D adds a
  `find_resumable_run` filter, not a state-machine change).
- No `§15` Playwright reintroduction.
- No extension of the `§W1` closed `FailureKind` taxonomy. Workstream H's
  new `PlannerNoOutputError` lands in `error_class`; `failure_kind` is
  `"infra_failure"` from the existing status-based mapping
  (`result._failure_kind_for`).
- No migration script for prior-completed runs lacking
  `gap_fingerprint.json` sidecars — the in-place fallback in Workstream C
  is the migration.
- No changes to the planner driver contract (`write_plan` keeps returning
  `str | None`). Workstream H's terminal failure is owned by the orchestrator
  phase, not by the driver — matches the Rule 8 "fail at the right boundary"
  pattern.

## Risks

- **Workstream A** changes the public-ish behavior of `_parse_and_validate`
  vs the old `_run_and_parse_json` (now also runs `model_validate`). Any
  test that imported the old helper by name needs to adopt the new signature.
  Mitigation: rename and update call sites in one commit; the helper is
  module-private (`_`-prefixed) so external callers do not exist.
- **Workstream C** introduces a new artifact emitted via the resource
  subscription path. The `_ALLOWED_ARTIFACTS` allowlist change must be
  matched by an iteration-dir walker entry, or `list_resources` will fail to
  surface the file even though emitter writes it. The §R9.1 module-isolation
  pin still holds (the new pattern is data, not an import). Mitigation:
  paired changes in `resources.py` + walker test.
- **Workstream G** changes the observable shape of `RunResult` when overflow
  writes fail: the `ArtifactIndex` fields `unresolved_gaps_overflow_path`
  / `design_flaw_gaps_overflow_path` on `RunResult.artifacts` become `None`
  instead of the run crashing. Both fields are already `str | None`
  (`models.py:261-262`), as are the corresponding ledger sources
  `ledger.unresolved_overflow_path` / `ledger.design_flaw_overflow_path` —
  so consumers tolerate `None` by construction. Mitigation: explicit test
  that `RunResult` validates with the field as None on write failure.
- **Workstream H** adds a new exception class that engine catches via
  `except Exception`. Any test that asserts "the orchestrator only raises
  CancelledError" needs to update — though the bright-line rule already
  forbids in-run raises, so such an assertion would itself be incorrect.
  Mitigation: grep tests for blanket exception-type assertions before
  implementation.
- **Workstream I** changes the byte position the algorithm cuts at in the
  no-newline edge case. The test at `tests/test_artifacts.py` should pin
  both the new (correct) boundary and the existing (newline-aligned) one
  to prevent the new fix from breaking the typical-case path.
