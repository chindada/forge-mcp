# forge-mcp Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `forge-mcp` — a stdio MCP server exposing one tool, `run_forge`, that drives a Planner (Claude) → Generator (Codex) → Evaluator (Claude) loop to autonomously implement a feature from a design document into a caller-specified `target_dir`, with concurrent wave-structured plan execution, crash-safe durable state, and honest non-convergence.

**Architecture:** A thin async orchestrator owns all durable and shared state; three SDK-isolated driver subprocesses do the work, handing off only via files on disk (never live context). Plans run concurrently in directory-copy sandboxes, structured as dependency-DAG waves; the orchestrator is the sole authority for every shared-file mutation (merge, spec amendment, conflict resolution), performed single-threaded at wave boundaries.

**Tech Stack:** Python ≥3.11 · `mcp[cli]` (FastMCP, stdio) · `claude-agent-sdk` · `openai-codex` · `pydantic` v2 · `typer` · `psutil` · `pytest`/`pytest-asyncio` · `ruff`/`pyright`. Source of truth is `docs/specs/0001-forge-mcp-harness.md` (cited inline as `§N`).

---

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec.

- **Python:** `requires-python = ">=3.11"`. The harness assumes 3.11+, where `asyncio.TimeoutError is TimeoutError` (§8.1) — load-bearing for the runtime cap.
- **Dependency pins (normative version floors):**
  - `mcp[cli] >=1.12,<2` — the **mcp 1.x major line**. The v2.x `mcp.server.mcpserver.MCPServer` MUST NOT appear (§4.1).
  - `claude-agent-sdk >=0.1.74,<1` — 0.1.74 introduced strict MCP configuration; verified against installed `0.2.137`.
  - `openai-codex >=0.144,<1` — the active supported range. The original implementation evidence in §8.2 used the historical `0.1.0b2` wheel; current SDK contract tests are authoritative.
  - `pydantic >=2.7,<3` · `typer >=0.12` · `psutil >=5.9`.
  - **`filelock` is intentionally NOT a dependency** — see Recommended Option Decisions D1 (lock uses the hand-rolled `O_EXCL` marker, not `SoftFileLock`).
  - `git` available on PATH (verified `2.54.0`).
- **Git-deny (I5, §9):** no stage runs `git commit/push/branch/tag/rebase/reset/worktree`. The orchestrator does no git mutation either.
- **No cross-run resume (I12, §6.6):** no `resume` input, no resumable-run scan, no lock adoption. Every call is a fresh timestamped run.
- **No MCP resource surface, no subscriptions, no task-augmented tool API** (§2). The host holds the stdio session for the run's duration.
- **Schema anchors (§4.3/§14/§17):** the Pydantic field **names** of `RunForgeInput`, `RunResult`, and the nested `GapSummary` (`{title, severity, design_doc_section}`) MUST match the spec exactly — the Evaluator diffs against them. Internal schemas (`Plan`/`EvalResult`/`TriageResult` rows) allow semantically-equivalent renames.
- **Rule-21 docstrings (§16):** every non-trivial `def`/`async def` carries a docstring containing `Design:`, `Implementation:`, `Example:` (≥5 non-whitespace chars each). Enforced by `scripts/check_docstrings.py` in CI. Files under any `fixtures/` path are exempt.
- **Style (§16):** `from __future__ import annotations` at the top of every module; **lazy SDK imports inside the seams only** (`drivers/_claude.py`, `drivers/_codex.py`) so module-import smoke tests don't need the SDKs. `ruff` lint select `E,F,I,B,UP,ASYNC`, line-length 100, target `py311`; `ruff-format`; `pyright` basic.
- **Filesystem posture (§11):** the run sets `os.umask(0o077)` and creates dirs at `0700`, restored in a `finally`. `.harness/` is self-ignored via a `*` `.gitignore` created `O_EXCL`.
- **`[verify-against-installed]` (§8.4):** internal label strings (state names, gap titles, signal names) and tunable policy constants (windows, thresholds, timeouts) are conformant under any behavior-preserving equivalent. **Exact Python SDK symbols are beta-volatile and MUST be re-introspected at implementation time** (Task 2 of the SDK-seam track).

---

## Recommended Option Decisions

The spec presents several "pick one" options. Each is resolved here toward the **durability/correctness-correct** choice (the north star is N1–N4 + D1–D3), not the lowest-effort one. These decisions are binding for the tasks below.

**D1 — Per-target lock: hand-rolled `O_EXCL` marker with in-content JSON payload (§12 option a).** ✅ chosen.
- *Why:* D2 requires a lock that **survives `kill -9`** and carries a **forensic payload** for PID-reuse-safe stale recovery. `filelock.SoftFileLock` (option b) auto-cleans on holder death is fine, but on POSIX its staleness check is only **pid-liveness** (`kill(pid,0)`) — **not** PID-reuse-safe — and it cannot store the payload in the lock file (needs a `run.lock.meta` sidecar). The hand-rolled `os.open(O_CREAT|O_EXCL|O_WRONLY, 0o600)` marker stores `{pid, run_id, started_at, target_dir, create_time}` **as the file content** (§11 payload option a) and uses `psutil.Process(pid).create_time()` for a true PID-reuse guard. This is "the right thing" for the crash-safe north star.
- *Consequence:* `filelock` is dropped from dependencies; `psutil` is required.

**D2 — Plan fan-out: semaphore-bounded `asyncio.gather(*tasks, return_exceptions=True)` (§7.1).** ✅ chosen over bare `gather` and **never** `asyncio.TaskGroup`.
- *Why:* the wave needs **both** a bounded concurrency cap (a semaphore) **and** failure isolation I8 (`return_exceptions=True` so one plan's crash never cancels siblings). `TaskGroup` cancels siblings on first error — it violates I8 and is forbidden. The only sanctioned mass-cancel is the run-level `asyncio.wait_for` runtime cap (§4.1).

**D3 — Verifier realization: `subprocess.run(cmd, shell=True, cwd=sandbox, capture_output=True, text=True, timeout=…, check=False)` wrapped in `try/except subprocess.TimeoutExpired` (§6.5).** ✅ chosen (the spec's conformant realization).
- *Why:* `check=False` suppresses the non-zero-exit exception (a failing command is `passed=False`, not a raise); `timeout=` **raises** `TimeoutExpired` (no sentinel), so the timeout branch must be caught → `passed=False, timed_out=True`. `shell=True` is acceptable because the command originates from the Planner (a model output), not arbitrary user input.

**D4 — Codex stderr tee: private-attribute swap, fail-soft to no-tee (§8.2).** ✅ chosen with mandatory `try/except AttributeError → warn-and-continue`.
- *Why:* the tee swaps `codex._client._sync._stderr_lines` (a bounded deque) — a private, undocumented 3-object chain, "the single most drift-fragile fact." It is forensic-only and MUST degrade to no-tee, never crash the run.

**D5 — Server: FastMCP synchronous `mcp.run()` over stdio, one tool, spread params (§4.1/§4.3).** ✅ (spec-decided; recorded for completeness).
- *Why:* with the resource surface and resume both out of scope, FastMCP is the Simplicity-First choice. Tool params are **spread** (not a single Pydantic param) so FastMCP emits a **flat** input schema whose top-level names are the `RunForgeInput` anchor fields.

**D6 — Claude structured-output drain: `ClaudeSDKClient` + `receive_response()` + `ResultMessage.structured_output` (§8.1).** ✅ (spec-decided).
- *Why:* `receive_response()` stops at `ResultMessage`; `receive_messages()` would block. `ClaudeSDKClient` (not one-shot `query()`) is required for `interrupt()`/early-break teardown. Structured output is read from `ResultMessage.structured_output`, **not** a tool-use block.

---

## File Structure

Target layout (§15) — a focused Core + durability set, **not** the reference's ~50 files. Each file has one responsibility and one writer where it holds durable state (I1).

```
pyproject.toml                 # project + tool config (§16); deps per Global Constraints
scripts/
  check_docstrings.py          # Rule-21 enforcement (§16)
  ci.sh                        # ruff + ruff-format --check + pyright + docstrings + pytest
src/forge_mcp/
  __init__.py  __main__.py     # package + `python -m forge_mcp` → cli.main
  cli.py                       # typer: `forge serve`, `forge check` (§4.4)
  server.py                    # FastMCP instance + run_forge tool (§4.1)
  config.py                    # env + fallbacks (§10.4); run-dir naming helpers (§12)
  models.py                    # RunForgeInput, RunResult, GapSummary, Plan/PlanSet, Eval/Triage (§14)
  schemas/__init__.py          # json_schema envelopes for Claude structured output (§8.1/§14)
  check.py                     # the `forge check` / preflight check set (§10.3, §4.4)
  skills.py                    # per-engine skill discovery probes (§10.3)
  gitguard.py                  # read-only git-mutation detector (§9)
  sandbox.py                   # directory-copy isolation, manifest, change-detect, merge (§7)
  verifier.py                  # deterministic verification gate, per-plan + run-level (§6.5)
  state.py                     # durable single-writer atomic writers (§13)
  artifacts.py                 # run-dir layout + light atomic writers (§11)
  ids.py                       # run-dir/timestamp matcher (§12)
  lockfile.py                  # per-target lock, no adoption (§12, D1)
  convergence.py               # PURE non-progress policy, per-plan + run-level (§6.7)
  triage.py                    # PURE gap classification + citation gate (§5.3)
  prompts/                     # planner_system.md, generator_system.md,
                               #   evaluator_system.md, evaluator_triage.md, remediation.md
  drivers/
    __init__.py
    _claude.py  _codex.py      # the two SDK seams (§8) — SOLE SDK import sites
    planner.py generator.py evaluator.py   # the three stages
  orchestrator/
    __init__.py
    engine.py                  # thin conductor: lock→plan→[wave: schedule→execute→merge→amend]→verify→finalize
    scheduler.py               # DAG waves, bounded concurrency, gather(return_exceptions=True) (§7.1)
    phases.py                  # the per-plan iteration loop (§3.2)
    statemachine.py            # run-level state (§3.3)
    plan_state.py              # per-plan durable state (§3.3)
    amend.py                   # orchestrator-owned spec amendment at wave boundaries (§5.3)
    lifecycle.py               # terminal honesty: completed/incomplete/failed cleanup (§6.4)
tests/
  conftest.py  fakes.py        # Protocol fakes for ClaudeRunner/CodexRunner (§17)
  test_*.py                    # per-module unit tests (TDD; written first in each task)
  test_sdk_contract.py         # introspects installed SDKs, not slow-gated (§8.4)
  test_e2e_real_clis.py        # @slow real-CLI e2e, excluded from default CI (§17)
```

---

## Concurrency Wave Map

Tasks are grouped into **waves**. **Within a wave, every task is independent** (disjoint files, dependencies already satisfied) and may be executed concurrently by separate agents/developers. A wave is a barrier: start wave _N+1_ only after wave _N_'s tasks are merged. Each task's `Interfaces → Consumes` block names the exact upstream signatures it needs, so a concurrent implementer never has to read a sibling's source.

| Wave | Concurrent tasks | Theme | Depends on |
|---|---|---|---|
| **0** | T1 | Project scaffold (serial, blocking) | — |
| **1** | T2 models · T3 ids · T4 convergence · T5 gitguard · T6 state · T7 verifier · T8 sandbox-manifest · T9 prompts | Leaf modules | T1 |
| **2** | T10 config · T11 artifacts · T12 triage · T13 sandbox-merge · T14 claude-seam · T15 codex-seam | Second-order modules | their wave-1 inputs |
| **3** | T16 lockfile · T17 planner · T18 generator · T19 evaluator · T20 statemachine · T21 plan_state · T22 skills | Stages + state machines | wave-2 |
| **4** | T23 lifecycle · T24 amend · T25 check | Boundary logic + doctor | wave-3 |
| **5** | T26 phases | Per-plan iteration loop | T18,T19,T7,T5,T4,T13,T21,T12,T11 |
| **6** | T27 scheduler | DAG waves + fan-out | T26,T13,T21 |
| **7** | T28 engine | Conductor | T27,T17,T24,T20,T23,T16,T11,T10,T7 |
| **8** | T29 server · T30 cli | MCP + CLI surface | T28,T25 |
| **9** | T31 e2e | `@slow` real-CLI smoke | all |

**Total: 31 tasks.** Waves 1–4 are wide (6–8 concurrent tasks); waves 5–8 narrow as the orchestrator composes the leaves. Every task ends with an independently testable, independently reviewable deliverable and a commit.

---

## Wave 0 — Project scaffold (serial, blocking)

### Task 1: Project scaffold & CI gate

**Files:**
- Create: `pyproject.toml`
- Create: `scripts/check_docstrings.py`
- Create: `scripts/ci.sh`
- Create: `src/forge_mcp/__init__.py`, `src/forge_mcp/__main__.py`
- Create: `src/forge_mcp/drivers/__init__.py`, `src/forge_mcp/orchestrator/__init__.py`, `src/forge_mcp/schemas/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable `forge-mcp` package (editable), the `forge` console entry point stub, the Rule-21 `scripts/check_docstrings.py` (importable `check_file(Path)->list[str]`, `main(argv)->int`), and `scripts/ci.sh`. Every later task assumes `uv sync` works and `python -c "import forge_mcp"` succeeds.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scaffold.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_imports():
    """Design: §15 the package must import without the SDKs installed.
    Implementation: import forge_mcp and assert __version__ exists.
    Example: pytest tests/test_scaffold.py::test_package_imports.
    """
    import forge_mcp

    assert isinstance(forge_mcp.__version__, str)


def test_docstring_checker_flags_missing():
    """Design: §16 Rule-21 enforcement must reject a non-trivial def with no docstring.
    Implementation: run check_file over a temp file lacking the three sections.
    Example: returns a non-empty defect list.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_docstrings

    tmp = ROOT / "tests" / "_tmp_bad.py"
    tmp.write_text("def f(x):\n    return x + 1\n")
    try:
        assert check_docstrings.check_file(tmp)  # missing docstring -> defects
    finally:
        tmp.unlink()


def test_ci_script_executable():
    """Design: §17 the CI gate is one script.
    Implementation: assert scripts/ci.sh exists and is executable.
    Example: returns True for the bundled script.
    """
    ci = ROOT / "scripts" / "ci.sh"
    assert ci.exists() and (ci.stat().st_mode & 0o111)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scaffold.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'forge_mcp'`.

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "forge-mcp"
version = "0.1.0"
description = "Stdio MCP server orchestrating a Planner -> Generator -> Evaluator loop"
requires-python = ">=3.11"
license = {text = "MIT"}
dependencies = [
  "mcp[cli] >=1.12,<2",
  "claude-agent-sdk >=0.1.74,<1",
  "openai-codex >=0.144,<1",
  "pydantic >=2.7,<3",
  "typer >=0.12",
  "psutil >=5.9",
]

[project.optional-dependencies]
dev = ["pytest >=8", "pytest-asyncio >=0.23", "ruff >=0.5", "pyright >=1.1.350"]

[project.scripts]
forge = "forge_mcp.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/forge_mcp"]

[tool.hatch.build.targets.wheel.force-include]
"src/forge_mcp/prompts" = "forge_mcp/prompts"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]

[tool.pyright]
include = ["src", "tests", "scripts"]
typeCheckingMode = "basic"
pythonVersion = "3.11"

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
  "slow: real-CLI end-to-end (excluded by default)",
  "mcp: FastMCP transport tests",
  "driver: drivers exercised with mocked runners",
]
testpaths = ["tests"]
addopts = "-m 'not slow'"
```

> **Note (Global Constraint):** `filelock` is deliberately absent (Decision D1). Do not add it.

- [ ] **Step 4: Create `scripts/check_docstrings.py`**

Implement the Rule-21 checker exactly as the house style requires: a non-trivial `def`/`async def` (body more than `pass`/`...`/a docstring) must have a docstring whose text contains `Design:`, `Implementation:`, `Example:`, each followed by ≥5 non-whitespace characters; files under any `fixtures/` segment are exempt; also reject the boilerplate string `"Exercise behavior pinned by the forge-mcp implementation plan."`. Public API: `_is_trivial(body)`, `_label_ok(doc,label)`, `check_file(path)->list[str]`, `walk(roots)->list[Path]`, `main(argv)->int` (exit 1 on any defect, 2 on no args). Use `ast` only — no imports of the project. (This is the verified reference shape; reproduce it.)

- [ ] **Step 5: Create the package skeleton**

```python
# src/forge_mcp/__init__.py
"""forge-mcp: stdio MCP server driving a Planner -> Generator -> Evaluator loop."""

from __future__ import annotations

__version__ = "0.1.0"
```

```python
# src/forge_mcp/__main__.py
"""`python -m forge_mcp` entry point delegating to the typer CLI (§4.4)."""

from __future__ import annotations

from forge_mcp.cli import main

if __name__ == "__main__":
    main()
```

Create empty `src/forge_mcp/drivers/__init__.py`, `src/forge_mcp/orchestrator/__init__.py`, `src/forge_mcp/schemas/__init__.py`, and a `tests/conftest.py` that prepends `scripts/` to `sys.path` so `import check_docstrings` works:

```python
# tests/conftest.py
"""Shared pytest fixtures and path wiring (§17)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
```

> `cli.py` does not exist yet (Task 30). `__main__.py` imports it lazily at call time, so `import forge_mcp` still succeeds. The `test_package_imports` test imports the package, not the CLI.

- [ ] **Step 6: Create `scripts/ci.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright
uv run python scripts/check_docstrings.py src tests scripts
uv run pytest
```

Then `chmod +x scripts/ci.sh`.

- [ ] **Step 7: Sync env and run tests to verify they pass**

Run: `uv sync --extra dev && uv run pytest tests/test_scaffold.py -q`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml scripts/ src/forge_mcp/ tests/
git commit -m "build: project scaffold, Rule-21 docstring checker, CI gate"
```

---

## Wave 1 — Leaf modules (T2–T9 concurrent)

### Task 2: `models.py` — Pydantic models & schema anchors

**Files:**
- Create: `src/forge_mcp/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (pydantic only).
- Produces:
  - `RunForgeInput(target_dir:str, design_doc_path:str|None=None, design_doc_content:str|None=None, max_iterations:int=10, max_runtime_minutes:int=600)` — `extra="forbid"`, with an `@model_validator(mode="after")` enforcing **exactly one** of `design_doc_path`/`design_doc_content`.
  - `RunResult(status:Literal["completed","incomplete","failed"], run_dir:str, iterations:int, unresolved_gaps:list[GapSummary]=[], failure_kind:str|None=None, stop_reason:str|None=None, verified:bool, summary:str)` — `extra="forbid"`.
  - `GapSummary(title:str, severity:str, design_doc_section:str)` — `extra="forbid"`.
  - `Plan(id:str, depends_on:list[str]=[], surface:Literal["backend","frontend"], file_scope:list[str]=[], verification_command:str|None=None, body:str)`.
  - `PlanSet(plans:list[Plan], run_verification_command:str|None=None)`.
  - `EvalGap(title:str, severity:str, design_doc_section:str, current_state:str, expected_state:str, suggested_fix:str)` — `title` whitespace-canonicalized by a validator.
  - `EvalResult(no_gaps:bool, gaps:list[EvalGap]=[], summary:str)`.
  - `GapTriage(gap_title:str, design_fault:bool, fault_kind:Literal["contradiction","infeasibility","deprecated_dependency","ambiguity","other"]|None, cited_sections:list[str]=[], explanation:str, proposed_amendment:ProposedAmendment|None=None)` — validator: `design_fault ⇒ fault_kind set ∧ non-empty cited_sections`; coerce stray `fault_kind→None` when `design_fault` is False.
  - `ProposedAmendment(cited_sections:list[str], before:str, after:str, rationale:str)`.
  - `TriageResult(triages:list[GapTriage]=[])`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_mcp.models import (
    EvalGap, GapSummary, GapTriage, ProposedAmendment, RunForgeInput, RunResult,
)


def test_run_forge_input_xor_path_or_content():
    """Design: §4.3 exactly one of design_doc_path/content.
    Implementation: both set -> ValidationError; neither -> ValidationError; one -> ok.
    Example: RunForgeInput(target_dir='/r', design_doc_path='/r/d.md').
    """
    RunForgeInput(target_dir="/r", design_doc_path="/r/d.md")
    RunForgeInput(target_dir="/r", design_doc_content="# hi")
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/r")
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/r", design_doc_path="/r/d.md", design_doc_content="x")


def test_run_forge_input_defaults():
    """Design: §4.3 tunable caps default to 10 / 600.
    Implementation: assert defaults.
    Example: RunForgeInput(...).max_runtime_minutes == 600.
    """
    i = RunForgeInput(target_dir="/r", design_doc_path="/r/d.md")
    assert i.max_iterations == 10 and i.max_runtime_minutes == 600


def test_run_forge_input_forbids_extra():
    """Design: §4.3 extra='forbid' keeps the tool-input schema tight.
    Implementation: an unknown field raises.
    Example: RunForgeInput(target_dir='/r', foo=1) -> ValidationError.
    """
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/r", design_doc_path="/r/d.md", foo=1)


def test_run_result_anchor_fields():
    """Design: §4.3/§17 RunResult field names are schema anchors.
    Implementation: construct and read each anchor field.
    Example: RunResult(status='incomplete', ...).
    """
    r = RunResult(
        status="incomplete", run_dir="/r/.harness/x", iterations=3,
        unresolved_gaps=[GapSummary(title="t", severity="high", design_doc_section="§7.4")],
        stop_reason="non-progress", verified=False, summary="1/2",
    )
    assert r.unresolved_gaps[0].design_doc_section == "§7.4"


def test_eval_gap_title_canonicalized():
    """Design: §5.3/§14 EvalGap.title whitespace-canonicalized for triage joins.
    Implementation: collapse internal runs and strip.
    Example: '  a   b ' -> 'a b'.
    """
    assert EvalGap(
        title="  missing   delete ", severity="high", design_doc_section="§7.4",
        current_state="x", expected_state="y", suggested_fix="z",
    ).title == "missing delete"


def test_gap_triage_design_fault_requires_kind_and_citation():
    """Design: §14 design_fault ⇒ fault_kind set ∧ non-empty cited_sections.
    Implementation: violating combo raises; non-fault coerces fault_kind to None.
    Example: GapTriage(design_fault=True, fault_kind=None, ...) -> ValidationError.
    """
    with pytest.raises(ValidationError):
        GapTriage(gap_title="g", design_fault=True, fault_kind=None,
                  cited_sections=["§7"], explanation="e")
    with pytest.raises(ValidationError):
        GapTriage(gap_title="g", design_fault=True, fault_kind="contradiction",
                  cited_sections=[], explanation="e")
    t = GapTriage(gap_title="g", design_fault=False, fault_kind="other",
                  cited_sections=[], explanation="e")
    assert t.fault_kind is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'forge_mcp.models'`.

- [ ] **Step 3: Implement `src/forge_mcp/models.py`**

Implement all models per the Interfaces block. Key details:
- Every model `extra="forbid"` for `RunForgeInput`/`RunResult`/`GapSummary` (anchors); internal models may use `extra="forbid"` too for tightness.
- `RunForgeInput._exactly_one_design_doc` — `@model_validator(mode="after")`: `bool(path) ^ bool(content)` else raise `ValueError`.
- `EvalGap` — `@field_validator("title")` returning `" ".join(v.split())`.
- `GapTriage` — `@model_validator(mode="after")`: if `not design_fault: self.fault_kind = None`; if `design_fault and (fault_kind is None or not cited_sections): raise ValueError`.
- Each non-trivial method carries a Rule-21 docstring (the validators count — give them three-section docstrings).
- `from __future__ import annotations` at top.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/models.py tests/test_models.py
git commit -m "feat(models): pydantic schema anchors and internal contract models (§14)"
```

---

### Task 3: `ids.py` — run-id / timestamp matcher

**Files:**
- Create: `src/forge_mcp/ids.py`
- Test: `tests/test_ids.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RUN_ID_RE: re.Pattern` matching `^\d{14}(-\d{2})?$` (§12).
  - `is_run_id(name:str)->bool`.
  - `format_run_id(dt_struct, *, uniquifier:int|None=None)->str` — given a 14-field time tuple (`time.struct_time` or a `(Y,M,D,h,m,s)` tuple), returns `YYYYMMDDHHMMSS`, optionally `-NN`. **No `Date.now()`/`datetime.now()` inside** — the timestamp source is injected by the caller (the orchestrator stamps it), keeping this module pure and testable.

> **Design note (§12):** the second-precision id and its regex MUST be threaded consistently across `config.create_run_dir`, the lock payload, the `run_dir` field, and any prune filter. This module is the single source of the shape.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ids.py
from __future__ import annotations

import time

from forge_mcp.ids import format_run_id, is_run_id


def test_is_run_id_accepts_second_precision_and_uniquifier():
    """Design: §12 run-id regex ^\\d{14}(-\\d{2})?$.
    Implementation: 14 digits, optional -NN.
    Example: is_run_id('20260623183102') is True.
    """
    assert is_run_id("20260623183102")
    assert is_run_id("20260623183102-01")
    assert not is_run_id("202606231831")      # minute precision rejected
    assert not is_run_id("deadbeef")          # old 8-hex shape rejected
    assert not is_run_id("20260623183102-1")  # uniquifier must be 2 digits


def test_format_run_id_round_trips():
    """Design: §12 the formatter and matcher agree.
    Implementation: format a known struct_time and re-match it.
    Example: format_run_id(struct) -> '20260623183102'.
    """
    st = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    assert format_run_id(st) == "20260623183102"
    assert format_run_id(st, uniquifier=1) == "20260623183102-01"
    assert is_run_id(format_run_id(st))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ids.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `src/forge_mcp/ids.py`**

```python
"""Run-id / timestamp shape — the single source of the run-dir naming format (§12)."""

from __future__ import annotations

import re
import time

RUN_ID_RE = re.compile(r"^\d{14}(-\d{2})?$")


def is_run_id(name: str) -> bool:
    """Return True if `name` is a valid second-precision run-id (§12).

    Design: §12 every run-id consumer (matcher, run_dir field, lock payload,
        prune filter) must share one shape; a mismatched filter silently never
        prunes. This regex is that shape.
    Implementation: full-match `^\\d{14}(-\\d{2})?$`.
    Example: is_run_id('20260623183102') returns True.
    """
    return bool(RUN_ID_RE.match(name))


def format_run_id(when: time.struct_time, *, uniquifier: int | None = None) -> str:
    """Format a time value as `YYYYMMDDHHMMSS[-NN]` (§12).

    Design: §12 second precision reduces collisions vs the example's minute
        precision; the source time is injected (not read here) so the module
        is pure and the matcher/formatter cannot drift.
    Implementation: strftime the 14-digit stamp; append a zero-padded 2-digit
        uniquifier on a same-second re-run.
    Example: format_run_id(struct_time(...2,18,31,2...)) -> '20260623183102'.
    """
    base = time.strftime("%Y%m%d%H%M%S", when)
    return f"{base}-{uniquifier:02d}" if uniquifier is not None else base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ids.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/ids.py tests/test_ids.py
git commit -m "feat(ids): second-precision run-id format and matcher (§12)"
```

---

### Task 4: `convergence.py` — pure non-progress policy

**Files:**
- Create: `src/forge_mcp/convergence.py`
- Test: `tests/test_convergence.py`

**Interfaces:**
- Consumes: nothing (pure, no I/O).
- Produces:
  - `fingerprint(items: Iterable[str]) -> frozenset[str]` — order-independent, ignores prose (caller passes the stable strings, e.g. `"title|severity"` or `"path|sorted(plan_ids)|kinds"`).
  - `Signal = Literal["none","NUDGE","EARLY_STOP"]` (internal labels, `[verify-against-installed]`).
  - `detect_non_progress(history: list[frozenset[str]], window: int = 2) -> Signal` per §6.7:
    - `len(history) < window` → `none`
    - latest fingerprint empty → `none`
    - `len(history) >= 2*window` and the last `2*window` all equal the latest → `EARLY_STOP`
    - elif the last `window` all equal the latest → `NUDGE`
    - else → `none`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_convergence.py
from __future__ import annotations

from forge_mcp.convergence import detect_non_progress, fingerprint


def fp(*xs):
    return fingerprint(xs)


def test_short_history_is_none():
    """Design: §6.7 history shorter than window -> none.
    Implementation: one entry, window 2.
    Example: detect_non_progress([fp('a')]) == 'none'.
    """
    assert detect_non_progress([fp("a")]) == "none"


def test_empty_latest_is_none():
    """Design: §6.7 an empty latest fingerprint never stops (no gaps = progress).
    Implementation: latest is empty frozenset.
    Example: returns 'none'.
    """
    assert detect_non_progress([fp("a"), fingerprint([])]) == "none"


def test_window_stable_is_nudge():
    """Design: §6.7 last `window` identical (but < 2*window) -> NUDGE.
    Implementation: 2 identical non-empty fingerprints, window 2.
    Example: returns 'NUDGE'.
    """
    assert detect_non_progress([fp("a"), fp("a")]) == "NUDGE"


def test_double_window_stable_is_early_stop():
    """Design: §6.7 last 2*window identical -> EARLY_STOP (honest stop).
    Implementation: 4 identical fingerprints, window 2.
    Example: returns 'EARLY_STOP'.
    """
    assert detect_non_progress([fp("a")] * 4) == "EARLY_STOP"


def test_changing_set_is_none():
    """Design: §6.7 a shrinking/changing gap-set is progress.
    Implementation: differing fingerprints -> none.
    Example: returns 'none'.
    """
    assert detect_non_progress([fp("a", "b"), fp("a")]) == "none"


def test_order_independent_fingerprint():
    """Design: §6.7 fingerprint ignores order.
    Implementation: same members different order compare equal.
    Example: fp('a','b') == fp('b','a').
    """
    assert fp("a", "b") == fp("b", "a")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_convergence.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `src/forge_mcp/convergence.py`**

```python
"""Pure oscillation / non-progress policy (no I/O), two scopes (§6.7)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Signal = Literal["none", "NUDGE", "EARLY_STOP"]


def fingerprint(items: Iterable[str]) -> frozenset[str]:
    """Reduce items to an order-independent fingerprint (§6.7).

    Design: §6.7 a stable, order-independent signature lets the detector compare
        successive gap/conflict/amendment sets while ignoring prose and ordering.
    Implementation: collect the stable strings into a frozenset.
    Example: fingerprint(['high|x', 'low|y']) == fingerprint(['low|y', 'high|x']).
    """
    return frozenset(items)


def detect_non_progress(history: list[frozenset[str]], window: int = 2) -> Signal:
    """Classify a fingerprint history as none / NUDGE / EARLY_STOP (§6.7).

    Design: §6.7 distinguishes an honest early stop (a stuck set repeated over a
        long window) from a one-off nudge, removing any incentive to burn the
        budget to the cap. `window` is a tunable policy constant.
    Implementation: too-short or empty-latest -> none; last 2*window all equal the
        latest -> EARLY_STOP; last window all equal -> NUDGE; else none.
    Example: detect_non_progress([fp]*4) returns 'EARLY_STOP'.
    """
    if len(history) < window:
        return "none"
    latest = history[-1]
    if not latest:
        return "none"
    if len(history) >= 2 * window and all(f == latest for f in history[-2 * window:]):
        return "EARLY_STOP"
    if all(f == latest for f in history[-window:]):
        return "NUDGE"
    return "none"
```

- [ ] **Step 4: Run tests to verify they pass** → `uv run pytest tests/test_convergence.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/convergence.py tests/test_convergence.py
git commit -m "feat(convergence): pure two-scope non-progress detector (§6.7)"
```

---

### Task 5: `gitguard.py` — read-only git-mutation detector

**Files:**
- Create: `src/forge_mcp/gitguard.py`
- Test: `tests/test_gitguard.py`

**Interfaces:**
- Consumes: nothing (subprocess + stdlib).
- Produces:
  - `capture_state(repo: Path) -> str | None` — a comparable string snapshot of the mutable git surface (`HEAD` commit, branch + tag refs, worktree set); `None` if `repo` is not a git repo.
  - `diff_state(base: str | None, end: str | None) -> str` — `""` iff unchanged; otherwise a non-empty human-readable diff.
  - `git_deny_matches(command: str) -> bool` — True if a Bash command string mutates git (`commit|push|branch|tag|rebase|reset|worktree` and `checkout -b`), used by the §8.1 hook and as a helper. (Defense-in-depth; the post-iteration snapshot diff is the real backstop.)
- Internals: `_run_git(repo, *args) -> str` using `subprocess.run(..., shell=False, timeout=…, check=False)`, catching `subprocess.TimeoutExpired` → treat as a non-mutating empty result (never propagate).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gitguard.py
from __future__ import annotations

import subprocess
from pathlib import Path

from forge_mcp.gitguard import capture_state, diff_state, git_deny_matches


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_non_repo_is_none(tmp_path: Path):
    """Design: §9 capture returns None outside a git repo.
    Implementation: a bare temp dir.
    Example: capture_state(tmp) is None.
    """
    assert capture_state(tmp_path) is None


def test_commit_is_detected(tmp_path: Path):
    """Design: §9 a new commit changes HEAD -> non-empty diff.
    Implementation: snapshot, commit, snapshot, diff.
    Example: diff_state(base, end) != ''.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-qm", "one")
    base = capture_state(tmp_path)
    (tmp_path / "a.txt").write_text("2")
    _git(tmp_path, "commit", "-qam", "two")
    end = capture_state(tmp_path)
    assert diff_state(base, end) != ""


def test_no_change_is_empty(tmp_path: Path):
    """Design: §9 an unchanged repo yields an empty diff.
    Implementation: two snapshots with no mutation between.
    Example: diff_state(a, a) == ''.
    """
    _git(tmp_path, "init", "-q")
    s = capture_state(tmp_path)
    assert diff_state(s, capture_state(tmp_path)) == ""


def test_deny_matcher_catches_chained_and_alt_spellings():
    """Design: §9 the matcher is defense-in-depth over the full command string.
    Implementation: catch &&-chained and 'checkout -b' spellings.
    Example: git_deny_matches('ls && git commit -m x') is True.
    """
    assert git_deny_matches("ls && git commit -m x")
    assert git_deny_matches("git checkout -b feature")
    assert git_deny_matches("git push origin main")
    assert not git_deny_matches("git status")
    assert not git_deny_matches("grep -r commit .")
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL (module missing).

- [ ] **Step 3: Implement `src/forge_mcp/gitguard.py`**

Implementation guidance (§9):
- `_run_git(repo, *args)`: `subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10, check=False)`; on `subprocess.TimeoutExpired` return `""`. Return `stdout` (or `""` if returncode != 0 for the repo-detection probe).
- `capture_state`: probe `git rev-parse --is-inside-work-tree`; if not a repo → `None`. Otherwise concatenate, in a fixed order: `HEAD` (`git rev-parse HEAD`), all refs (`git show-ref` — branches+tags), and worktrees (`git worktree list --porcelain`). Join into one comparable string. `[verify-against-installed]` the exact porcelain — any capture that detects commit/branch/tag/worktree mutation is conformant.
- `diff_state(base, end)`: `"" if base == end else f"git state changed:\n--- base\n{base}\n--- end\n{end}"`.
- `git_deny_matches(command)`: lower-case, regex search for `\bgit\b` followed (anywhere in the same command string) by a mutating verb `commit|push|branch|tag|rebase|reset|worktree` **or** `checkout\s+-b`. Keep it a simple, honest best-effort matcher (the snapshot diff is the real guard).

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/gitguard.py tests/test_gitguard.py
git commit -m "feat(gitguard): read-only git-mutation snapshot + deny matcher (§9)"
```

---

### Task 6: `state.py` — durable single-writer atomic writers

**Files:**
- Create: `src/forge_mcp/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (§13):
  - `durable_replace(path: Path, data: bytes | str) -> None` — same-dir tempfile → `flush` → `os.fsync(fd)` → `chmod 0600` → `os.replace` → parent-dir fsync (best-effort, swallow `OSError`). For `state.json` (run + per-plan) and `spec.md`.
  - `durable_append(path: Path, text: str) -> None` — `open(path,"a")` → write → `flush` → `os.fsync(fd)`; first creation does a one-time parent-dir fsync. For `spec_amendments.md` (MUST NOT use replace — I2/I3).
  - `light_replace(path: Path, data: bytes | str) -> None` — tempfile + fd-fsync + chmod + `os.replace`, **no** parent-dir fsync. For fingerprints and ordinary artifacts.
  - `write_json(path, obj, *, durable: bool) -> None` — dumps `obj` (a `BaseModel` via `model_dump_json`, or a dict via `json.dumps`) then routes to `durable_replace`/`light_replace`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
from __future__ import annotations

import json
from pathlib import Path

from forge_mcp.state import durable_append, durable_replace, light_replace, write_json


def test_durable_replace_overwrites_atomically(tmp_path: Path):
    """Design: §13 whole-file replace is the state.json/spec.md writer.
    Implementation: write twice; the second fully replaces the first.
    Example: file content == last write.
    """
    p = tmp_path / "state.json"
    durable_replace(p, "first")
    durable_replace(p, "second")
    assert p.read_text() == "second"
    assert (p.stat().st_mode & 0o777) == 0o600


def test_durable_append_preserves_prior_entries(tmp_path: Path):
    """Design: §13/I3 the audit log must NOT be replaced wholesale.
    Implementation: two appends accumulate.
    Example: both lines present.
    """
    p = tmp_path / "spec_amendments.md"
    durable_append(p, "entry-1\n")
    durable_append(p, "entry-2\n")
    assert p.read_text() == "entry-1\nentry-2\n"


def test_light_replace_writes(tmp_path: Path):
    """Design: §13 light tier for fingerprints/artifacts.
    Implementation: bytes round-trip.
    Example: file content matches.
    """
    p = tmp_path / "fp.json"
    light_replace(p, b'["a|high"]')
    assert p.read_bytes() == b'["a|high"]'


def test_write_json_durable_and_light(tmp_path: Path):
    """Design: §13 write_json routes a dict/model to the chosen tier.
    Implementation: durable=True and False both produce valid JSON.
    Example: json.loads round-trips.
    """
    p = tmp_path / "x.json"
    write_json(p, {"k": 1}, durable=True)
    assert json.loads(p.read_text()) == {"k": 1}
    write_json(p, {"k": 2}, durable=False)
    assert json.loads(p.read_text()) == {"k": 2}
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/state.py`**

Implementation guidance (§13), all functions Rule-21 documented:
- A private `_atomic_replace(path, data, *, dir_fsync: bool)`: encode str→bytes; create tempfile in the same dir via `tempfile.mkstemp(dir=path.parent)`; `os.write` all bytes; `os.fsync(fd)`; `os.fchmod(fd, 0o600)`; `os.close(fd)`; `os.replace(tmp, path)`; if `dir_fsync`: open `path.parent` with `os.open(..., O_RDONLY)`, `os.fsync`, close, all wrapped `try/except OSError` (best-effort).
- `durable_replace` = `_atomic_replace(dir_fsync=True)`; `light_replace` = `_atomic_replace(dir_fsync=False)`.
- `durable_append`: if the file does not yet exist, create it durably (touch via `open(path,"x")`, then one parent-dir fsync); then `with open(path,"a") as f: f.write(text); f.flush(); os.fsync(f.fileno())`.
- `write_json`: if `hasattr(obj,"model_dump_json")`: `text = obj.model_dump_json()`; else `text = json.dumps(obj, sort_keys=True)`; route by `durable`.
- Clean up the tempfile on any exception (`try/except: os.unlink(tmp)` best-effort).

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/state.py tests/test_state.py
git commit -m "feat(state): durable/append/light atomic writers (§13)"
```

---

### Task 7: `verifier.py` — deterministic verification gate

**Files:**
- Create: `src/forge_mcp/verifier.py`
- Test: `tests/test_verifier.py`

**Interfaces:**
- Consumes: nothing (subprocess + stdlib).
- Produces (§6.5):
  - `@dataclass(frozen=True) VerifyOutcome: passed: bool, exit_code: int | None, timed_out: bool, output: str`.
  - `run_verification(command: str | None, cwd: Path, *, timeout: float = 600.0, output_limit: int = 65536) -> VerifyOutcome` — `command is None` → `VerifyOutcome(passed=True, exit_code=None, timed_out=False, output="")` (a declared-absent command passes). Otherwise run per Decision D3.
- The orchestrator (Tasks 26/28) writes `output` to `iteration-N/verify.txt` and reuses the cached outcome at completion (not re-run).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_verifier.py
from __future__ import annotations

from pathlib import Path

from forge_mcp.verifier import run_verification


def test_none_command_passes(tmp_path: Path):
    """Design: §6.5 verify_passed is True when no command is declared.
    Implementation: command=None.
    Example: passed True, timed_out False.
    """
    o = run_verification(None, tmp_path)
    assert o.passed and not o.timed_out and o.exit_code is None


def test_exit_zero_passes(tmp_path: Path):
    """Design: §6.5 passed = (exit_code == 0).
    Implementation: 'true'.
    Example: passed True.
    """
    assert run_verification("true", tmp_path).passed


def test_nonzero_exit_is_not_passed_no_exception(tmp_path: Path):
    """Design: §6.5 check=False -> a failing command is passed=False, not a raise.
    Implementation: 'false'.
    Example: passed False, exit_code != 0.
    """
    o = run_verification("false", tmp_path)
    assert not o.passed and o.exit_code != 0 and not o.timed_out


def test_timeout_sets_timed_out(tmp_path: Path):
    """Design: §6.5 timeout -> passed=False, timed_out=True (TimeoutExpired caught).
    Implementation: 'sleep 5' with timeout 0.2.
    Example: timed_out True.
    """
    o = run_verification("sleep 5", tmp_path, timeout=0.2)
    assert not o.passed and o.timed_out


def test_output_is_bounded(tmp_path: Path):
    """Design: §6.5 output is captured and bounded.
    Implementation: large stdout, small output_limit.
    Example: len(output) <= limit.
    """
    o = run_verification("for i in $(seq 1 1000); do echo line$i; done", tmp_path, output_limit=200)
    assert len(o.output) <= 200
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/verifier.py`** (Decision D3):

```python
"""Deterministic verification gate, per-plan and run-level (§6.5)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VerifyOutcome:
    """The cached result of one verification-command run (§6.5)."""

    passed: bool
    exit_code: int | None
    timed_out: bool
    output: str


def run_verification(
    command: str | None,
    cwd: Path,
    *,
    timeout: float = 600.0,
    output_limit: int = 65536,
) -> VerifyOutcome:
    """Run a verification command in `cwd` and classify the outcome (§6.5).

    Design: §6.5/D1 a declared-absent command passes vacuously; a present
        command gates the plan's sandbox (or, run-level, the merged target_dir).
        The command originates from the Planner, so shell=True is acceptable.
    Implementation: command is None -> passed. Else subprocess.run(shell=True,
        cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False);
        passed = (exit_code == 0); a subprocess.TimeoutExpired -> passed=False,
        timed_out=True. Output (stdout+stderr) is bounded to output_limit.
    Example: run_verification('pytest -q', sandbox).passed.
    """
    if command is None:
        return VerifyOutcome(passed=True, exit_code=None, timed_out=False, output="")
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(partial, bytes):  # text=True yields str, but be defensive
            partial = partial.decode(errors="replace")
        return VerifyOutcome(passed=False, exit_code=None, timed_out=True,
                             output=partial[:output_limit])
    output = (proc.stdout + proc.stderr)[:output_limit]
    return VerifyOutcome(passed=proc.returncode == 0, exit_code=proc.returncode,
                         timed_out=False, output=output)
```

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/verifier.py tests/test_verifier.py
git commit -m "feat(verifier): deterministic shell verification gate (§6.5)"
```

---

### Task 8: `sandbox.py` (part 1) — manifest capture & change detection

**Files:**
- Create: `src/forge_mcp/sandbox.py`
- Test: `tests/test_sandbox_manifest.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (§7.2/§7.3) — these types are consumed by Task 13 (merge):
  - `@dataclass(frozen=True) Entry: kind: Literal["file","symlink","dir"], digest: str | None, mode: int | None`.
  - `Manifest = dict[str, Entry]` (keyed by **relative** path).
  - `copy_sandbox(src: Path, dst: Path) -> None` — recursively copy `src`→`dst` **excluding `.git` and `.harness`**, copying symlinks **as links** (`os.symlink`, never dereferenced), preserving file modes.
  - `capture_manifest(root: Path) -> Manifest` — per §7.2: file → `sha256(bytes)` + `S_IMODE`; symlink → `sha256(os.readlink(...).encode())`, **no mode**; dir → no digest, `S_IMODE`.
  - `ChangeKind = Literal["added","deleted","modified","mode-changed"]`.
  - `@dataclass(frozen=True) Change: path: str, kind: ChangeKind, entry_kind: Literal["file","symlink","dir"], mode: int | None` (the re-stat'd post-edit `S_IMODE` for added/modified file/dir rows, per §7.3).
  - `detect_changes(root: Path, base: Manifest) -> list[Change]` — per §7.3, incl. type-flip → `modified`, byte-exact file compare, symlink via `lstat`/`readlink`, mode-changed for files & dirs only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sandbox_manifest.py
from __future__ import annotations

import os
import stat
from pathlib import Path

from forge_mcp.sandbox import capture_manifest, copy_sandbox, detect_changes


def test_copy_excludes_git_and_harness_keeps_symlinks(tmp_path: Path):
    """Design: §7.2 copy excludes .git/.harness, copies symlinks as links.
    Implementation: build a tree, copy, assert exclusions and link kind.
    Example: dst has no .git; link stays a link.
    """
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".harness").mkdir()
    (src / "pkg").mkdir()
    (src / "pkg" / "a.txt").write_text("hello")
    os.symlink("a.txt", src / "pkg" / "link")
    dst = tmp_path / "dst"
    copy_sandbox(src, dst)
    assert not (dst / ".git").exists() and not (dst / ".harness").exists()
    assert (dst / "pkg" / "a.txt").read_text() == "hello"
    assert (dst / "pkg" / "link").is_symlink()


def test_manifest_kinds_and_digests(tmp_path: Path):
    """Design: §7.2 file/symlink/dir manifest entries.
    Implementation: capture and inspect kinds.
    Example: file has digest+mode, symlink no mode, dir no digest.
    """
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_text("x")
    os.symlink("f", tmp_path / "d" / "s")
    m = capture_manifest(tmp_path)
    assert m["d"].kind == "dir" and m["d"].digest is None and m["d"].mode is not None
    assert m["d/f"].kind == "file" and m["d/f"].digest and m["d/f"].mode is not None
    assert m["d/s"].kind == "symlink" and m["d/s"].digest and m["d/s"].mode is None


def test_detect_add_modify_delete_mode_typeflip(tmp_path: Path):
    """Design: §7.3 added/deleted/modified/mode-changed + type-flip→modified.
    Implementation: snapshot, mutate, detect.
    Example: each kind present.
    """
    (tmp_path / "keep").write_text("k")
    (tmp_path / "mod").write_text("1")
    (tmp_path / "gone").write_text("g")
    (tmp_path / "exec").write_text("#!/bin/sh\n")
    base = capture_manifest(tmp_path)
    (tmp_path / "new").write_text("n")                 # added
    (tmp_path / "mod").write_text("2")                 # modified
    (tmp_path / "gone").unlink()                       # deleted
    os.chmod(tmp_path / "exec", 0o755)                 # mode-changed
    (tmp_path / "keep").unlink()                       # type-flip file->dir
    (tmp_path / "keep").mkdir()
    changes = {(c.path, c.kind) for c in detect_changes(tmp_path, base)}
    assert ("new", "added") in changes
    assert ("mod", "modified") in changes
    assert ("gone", "deleted") in changes
    assert ("exec", "mode-changed") in changes
    assert ("keep", "modified") in changes             # type-flip treated as modified


def test_dangling_symlink_does_not_raise(tmp_path: Path):
    """Design: §7.2 symlink digest hashes the target string, never dereferenced.
    Implementation: a dangling link captures cleanly.
    Example: capture succeeds; retarget detected as modified.
    """
    os.symlink("nowhere", tmp_path / "dl")
    base = capture_manifest(tmp_path)
    assert base["dl"].kind == "symlink"
    (tmp_path / "dl").unlink()
    os.symlink("elsewhere", tmp_path / "dl")
    assert ("dl", "modified") in {(c.path, c.kind) for c in detect_changes(tmp_path, base)}
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement manifest + change detection in `src/forge_mcp/sandbox.py`**

Implementation guidance (§7.2/§7.3), Rule-21 docstrings throughout:
- `copy_sandbox`: walk `src` with `os.walk(..., followlinks=False)`; prune `.git`/`.harness` at the top level; recreate dirs with their mode; for each entry, if `os.path.islink` → `os.symlink(os.readlink(s), d)`; elif file → `shutil.copy2` then preserve mode; (a `shutil.copytree(..., symlinks=True, ignore=ignore_names)` realization is also conformant — `ignore` must drop `.git`,`.harness` only at the root). Ensure `dst` parent exists.
- `_digest_file(p)` = `sha256(p.read_bytes()).hexdigest()`; `_digest_link(p)` = `sha256(os.readlink(p).encode()).hexdigest()`.
- `capture_manifest`: `os.walk(root, followlinks=False)` over **everything under root** (the sandbox has no `.git`/`.harness`); produce relative POSIX paths (`Path.relative_to(root).as_posix()`); a symlink encountered as a "dir" by `os.walk` must be classified by `os.path.islink` **first** (symlink to dir is a `symlink` entry, not recursed). Dirs → `Entry("dir", None, S_IMODE(lstat.st_mode))`; symlinks → `Entry("symlink", digest_link, None)`; files → `Entry("file", digest_file, S_IMODE(stat.st_mode))`.
- `detect_changes`: compute the current manifest, diff vs `base`. For each path: present-now-absent-base → `added`; absent-now-present-base → `deleted`; both present same kind & digest differ → `modified`; both present different kind → `modified` (type-flip; record `entry_kind` = the **new** kind); both present same kind same digest mode differ (files & dirs only) → `mode-changed`. For `added`/`modified` file/dir rows, set `mode` to the re-stat'd post-edit `S_IMODE`; symlink rows carry `mode=None`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/sandbox.py tests/test_sandbox_manifest.py
git commit -m "feat(sandbox): copy isolation, manifest capture, change detection (§7.2/§7.3)"
```

---

### Task 9: `prompts/` — the five stage prompt files

**Files:**
- Create: `src/forge_mcp/prompts/__init__.py`
- Create: `src/forge_mcp/prompts/planner_system.md`
- Create: `src/forge_mcp/prompts/generator_system.md`
- Create: `src/forge_mcp/prompts/evaluator_system.md`
- Create: `src/forge_mcp/prompts/evaluator_triage.md`
- Create: `src/forge_mcp/prompts/remediation.md`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_prompt(name: str) -> str` reading `prompts/<name>.md` from the package via `importlib.resources`; `PROMPT_NAMES: tuple[str, ...]`. Prompt wording is operational (`[verify-against-installed]`, §5) — not diffed literally — but each file MUST embody its stance.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts.py
from __future__ import annotations

import pytest

from forge_mcp.prompts import PROMPT_NAMES, load_prompt


@pytest.mark.parametrize("name", [
    "planner_system", "generator_system", "evaluator_system",
    "evaluator_triage", "remediation",
])
def test_each_prompt_loads_nonempty(name: str):
    """Design: §5 each stage has a loadable, non-trivial prompt.
    Implementation: load_prompt returns substantial text.
    Example: load_prompt('planner_system') -> str length > 200.
    """
    assert name in PROMPT_NAMES
    assert len(load_prompt(name)) > 200


def test_generator_prompt_forbids_git_and_references_skill_by_capability():
    """Design: §5.2/§10.1 Codex has no Skill tool; reference by capability; forbid git.
    Implementation: assert key stances appear.
    Example: 'git commit' forbidden phrasing present.
    """
    text = load_prompt("generator_system").lower()
    assert "git" in text and "commit" in text
    assert "skill tool" not in text  # Codex references skills by behavior, not a tool
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Author the prompts** following the §5 complex-prompt order (task context → rules → one worked example → input → task → output format), each carrying its stance:
  - `planner_system.md` — role: decompose to product/architecture (not premature impl detail); plan only what the design requires; record ambiguities as open questions; **forbidden from git mutations**; emit a PlanSet via the plan-writing skill; output is structured JSON.
  - `generator_system.md` — role: implement the contract with the simplest sufficient code; strict scope discipline (build nothing beyond the contract); honest non-convergence (never fake done); the "do not wrap up early" budget rule and "build nothing beyond the contract" scope rule **stated to not conflict**; **forbid git mutations** (defense-in-depth); reference the plan-execution / frontend-design skill **by capability** (Codex has no Skill tool).
  - `evaluator_system.md` — role: skeptical external judge; diff generated code against the frozen `spec.md`, not general review; output `EvalResult`; carry exactly one good-gap example (precise §-citation, location, remedy) and one weak-gap anti-example.
  - `evaluator_triage.md` — classify each gap spec-issue vs implementation-gap; the citation gate (every `cited_sections` entry a non-trivial ≥20-char verbatim substring of `spec.md`); design-fault rows carry a `proposed_amendment`; output `TriageResult`.
  - `remediation.md` — write the next remediation contract for implementation gaps (uses the plan-writing skill); nudge text appended when convergence returns `NUDGE`.
- `__init__.py`:

```python
"""Stage prompt loader (§5)."""

from __future__ import annotations

from importlib.resources import files

PROMPT_NAMES = (
    "planner_system", "generator_system", "evaluator_system",
    "evaluator_triage", "remediation",
)


def load_prompt(name: str) -> str:
    """Load a stage prompt by name from the package resources (§5).

    Design: §5 prompts are file-based handoff content, versioned with the code
        and shipped in the wheel (pyproject force-include).
    Implementation: read prompts/<name>.md via importlib.resources.
    Example: load_prompt('planner_system') -> the planner system prompt text.
    """
    return (files("forge_mcp.prompts") / f"{name}.md").read_text(encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/prompts/ tests/test_prompts.py
git commit -m "feat(prompts): five stage system/triage/remediation prompts (§5)"
```

---

## Wave 2 — second-order modules (T10–T15 concurrent)

### Task 10: `config.py` — environment, fallbacks, run-dir naming

**Files:**
- Create: `src/forge_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `forge_mcp.ids` (`format_run_id`, `is_run_id`).
- Produces (§10.4/§12):
  - `claude_config_dir() -> Path` — `$CLAUDE_CONFIG_DIR` else `~/.claude`.
  - `claude_bin() -> Path` — `$FORGE_CLAUDE_BIN` else `shutil.which("claude")` else `~/.local/bin/claude`, resolved absolute before use.
  - `codex_bin() -> Path` — `$FORGE_CODEX_BIN` else `shutil.which("codex")` else `~/.npm-global/bin/codex`.
  - `create_run_dir(target_dir: Path, when: time.struct_time) -> Path` — ensures `<target_dir>/.harness/`, writes the self-ignoring `.gitignore` (`*`, `O_EXCL`, never overwritten), and creates `<target_dir>/.harness/<run_id>/` at `0700`; appends a `-NN` uniquifier on same-second collision (uses `ids.format_run_id`). Returns the run dir.
  - `CONCURRENCY_CAP: int = 4` (tunable, `[verify-against-installed]`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
from __future__ import annotations

import time
from pathlib import Path

from forge_mcp import config
from forge_mcp.ids import is_run_id


def test_env_overrides(monkeypatch, tmp_path):
    """Design: §10.4 env vars take precedence over PATH/default fallbacks.
    Implementation: set FORGE_CODEX_BIN and CLAUDE_CONFIG_DIR.
    Example: codex_bin() == the env path.
    """
    monkeypatch.setenv("FORGE_CODEX_BIN", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert config.codex_bin() == tmp_path / "codex"
    assert config.claude_config_dir() == tmp_path / "cc"


def test_create_run_dir_makes_gitignore_and_timestamp_dir(tmp_path):
    """Design: §11/§12 .harness is self-ignored; run dir is a valid run-id.
    Implementation: create_run_dir then inspect.
    Example: .harness/.gitignore == '*'; run dir name is a run-id.
    """
    when = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    rd = config.create_run_dir(tmp_path, when)
    assert (tmp_path / ".harness" / ".gitignore").read_text() == "*"
    assert is_run_id(rd.name) and rd.is_dir()


def test_same_second_rerun_uniquifies(tmp_path):
    """Design: §12 same-second re-run appends -NN.
    Implementation: two create_run_dir with identical struct_time.
    Example: second dir name ends with -01.
    """
    when = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    a = config.create_run_dir(tmp_path, when)
    b = config.create_run_dir(tmp_path, when)
    assert a.name != b.name and b.name.endswith("-01")
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/config.py`**

Guidance (§10.4/§12):
- `_env_path(name)` helper returning `Path(os.environ[name])` or `None`.
- `claude_bin`: env → `shutil.which(...)` → default path; return an absolute `Path`.
- `codex_bin`: env → `shutil.which(...)` → default path; return `Path`.
- `_ensure_harness_gitignore(harness)`: create `harness` (`exist_ok`); attempt `fd = os.open(harness/".gitignore", O_CREAT|O_EXCL|O_WRONLY, 0o600)`; on success `os.write(fd, b"*")`, close; on `FileExistsError` leave it (never overwrite — §7.2).
- `create_run_dir`: `harness = target_dir/".harness"`; ensure gitignore; loop uniquifier from `None,1,2,…`: `run_id = format_run_id(when, uniquifier=u)`; try `os.makedirs(harness/run_id, mode=0o700, exist_ok=False)`; on `FileExistsError` bump `u`; return on success.
- `CONCURRENCY_CAP = 4`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/config.py tests/test_config.py
git commit -m "feat(config): env fallbacks, run-dir naming, self-ignoring .harness (§10.4/§12)"
```

---

### Task 11: `artifacts.py` — run-dir layout & light writers

**Files:**
- Create: `src/forge_mcp/artifacts.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `forge_mcp.state` (`durable_replace`, `durable_append`, `light_replace`, `write_json`).
- Produces (§11) — a thin layout helper the orchestrator uses for paths and writes:
  - `@dataclass(frozen=True) RunLayout` with computed properties for every §11 path: `root`, `state_json`, `run_log`, `conflict_fingerprint`, `inputs_design`, `design_fingerprint`, `spec_md`, `spec_amendments`, `spec_fingerprint`, `planset_json`, `plan_md(id)`, `plan_dir(id)`, `plan_state(id)`, `plan_manifest(id)`, `plan_merge(id)`, `iteration_dir(id, n)`, and per-iteration files (`contract`, `summary`, `eval`, `triage`, `gap_fingerprint`, `verify_txt`, `git_violation`).
  - `RunLayout.for_run(run_dir: Path) -> RunLayout`.
  - `init_run_layout(run_dir, design_text: str, *, design_fingerprint: str) -> RunLayout` — creates `inputs/`, writes immutable `inputs/design.md`, `design.fingerprint`, the orchestrator-owned `spec.md` (= design at init) + `spec.fingerprint` (= design fingerprint), and an empty `spec_amendments.md` (durably).
  - `ensure_iteration_dir(layout, plan_id, n) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_artifacts.py
from __future__ import annotations

import hashlib

from forge_mcp.artifacts import RunLayout, init_run_layout


def test_layout_paths(tmp_path):
    """Design: §11 the layout exposes every run-dir path.
    Implementation: a few representative paths resolve under run_dir.
    Example: iteration file under plans/<id>/iteration-N/.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.spec_md == tmp_path / "spec.md"
    assert lay.iteration_dir("p1", 2).name == "iteration-2"
    assert lay.eval("p1", 2).parent == lay.iteration_dir("p1", 2)


def test_init_run_layout_freezes_design_and_seeds_spec(tmp_path):
    """Design: §3.1/§11 design.md is immutable; spec.md starts equal to design.
    Implementation: init then read files.
    Example: spec.md == design.md content at init; spec_amendments empty.
    """
    text = "# design\nbody\n"
    fp = hashlib.sha256(text.encode()).hexdigest()
    lay = init_run_layout(tmp_path, text, design_fingerprint=fp)
    assert lay.inputs_design.read_text() == text
    assert lay.spec_md.read_text() == text
    assert lay.design_fingerprint.read_text() == fp
    assert lay.spec_fingerprint.read_text() == fp
    assert lay.spec_amendments.read_text() == ""
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/artifacts.py`** per §11 layout; use `durable_replace` for `state.json`/`spec.md`, `durable_append`-create for `spec_amendments.md`, `light_replace` for fingerprints. `ensure_iteration_dir` uses `os.makedirs(..., 0o700, exist_ok=True)`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/artifacts.py tests/test_artifacts.py
git commit -m "feat(artifacts): run-dir layout + init freeze of design/spec (§11)"
```

---

### Task 12: `triage.py` — pure gap classification & citation gate

**Files:**
- Create: `src/forge_mcp/triage.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `forge_mcp.models` (`EvalGap`, `GapTriage`, `TriageResult`).
- Produces (§5.3, pure — no I/O):
  - `CITATION_MIN_CHARS: int = 20` (tunable, `[verify-against-installed]`).
  - `is_valid_citation(text: str, spec: str, *, min_chars: int = CITATION_MIN_CHARS) -> bool` — True iff `len(text.strip()) >= min_chars` **and** `text` is a verbatim substring of `spec`.
  - `passes_citation_gate(triage: GapTriage, spec: str) -> bool` — True iff `triage.design_fault` and `triage.fault_kind` set and **every** `cited_sections` entry passes `is_valid_citation`.
  - `effective_code_bug_titles(gaps: list[EvalGap], triages: list[GapTriage], spec: str) -> set[str]` — the full post-synthesize gap set left-joined to triage by whitespace-canonicalized title; a gap counts as a **non-demotable code-bug** unless its triage row passes the citation gate (a gap with no triage row, and every synthesized gap, counts as a code-bug).
  - `amendment_target_present(before: str, spec: str) -> bool` — `before` is a verbatim substring of `spec` (the orchestrator's re-check at AMEND, §5.3 step 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_triage.py
from __future__ import annotations

from forge_mcp.models import EvalGap, GapTriage
from forge_mcp.triage import (
    effective_code_bug_titles, is_valid_citation, passes_citation_gate,
)

SPEC = "## §7.4 Merge\nThe orchestrator unions each completed plan's change-set into target_dir.\n"


def gap(title):
    return EvalGap(title=title, severity="high", design_doc_section="§7.4",
                   current_state="c", expected_state="e", suggested_fix="f")


def test_citation_gate_requires_long_verbatim_substring():
    """Design: §5.3 cited text must be a >=20-char verbatim substring of spec.
    Implementation: a real long substring passes; a short/absent one fails.
    Example: is_valid_citation('The orchestrator unions each completed plan', SPEC).
    """
    assert is_valid_citation("The orchestrator unions each completed plan", SPEC)
    assert not is_valid_citation("§7.4", SPEC)                  # too short
    assert not is_valid_citation("a 30 character non-substring!!", SPEC)  # not in spec


def test_design_fault_passes_only_with_valid_citations():
    """Design: §5.3 design_fault rows demote unless every citation is valid.
    Implementation: valid vs invalid citation.
    Example: passes_citation_gate True only when verbatim.
    """
    good = GapTriage(gap_title="g", design_fault=True, fault_kind="contradiction",
                     cited_sections=["The orchestrator unions each completed plan"],
                     explanation="x")
    bad = GapTriage(gap_title="g", design_fault=True, fault_kind="contradiction",
                    cited_sections=["nonexistent verbatim text here!!"], explanation="x")
    assert passes_citation_gate(good, SPEC)
    assert not passes_citation_gate(bad, SPEC)


def test_effective_code_bugs_excludes_validated_design_faults():
    """Design: §6.5 count only gaps whose triage is NOT a validated design fault.
    Implementation: one validated fault (excluded), one untriaged gap (counted).
    Example: only the untriaged title remains.
    """
    gaps = [gap("real bug"), gap("spec is wrong")]
    triages = [GapTriage(gap_title="spec is wrong", design_fault=True,
                         fault_kind="contradiction",
                         cited_sections=["The orchestrator unions each completed plan"],
                         explanation="x")]
    assert effective_code_bug_titles(gaps, triages, SPEC) == {"real bug"}
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/triage.py`** per the Interfaces block, pure functions only. Title joins use `" ".join(t.split())` canonicalization to match `EvalGap.title`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/triage.py tests/test_triage.py
git commit -m "feat(triage): pure citation gate + code-bug classification (§5.3)"
```

---

### Task 13: `sandbox.py` (part 2) — merge, cumulative overlap, conflict state machine

**Files:**
- Modify: `src/forge_mcp/sandbox.py`
- Test: `tests/test_sandbox_merge.py`

**Interfaces:**
- Consumes: Task 8's `Change`, `Manifest`, `Entry` (same module).
- Produces (§7.4):
  - `@dataclass Conflict: path: str, plan_ids: list[str], kinds: list[str]` with `fingerprint() -> str` = `f"{path}|{','.join(sorted(plan_ids))}|{','.join(sorted(kinds))}"` (§6.7 conflict fingerprint shape).
  - `@dataclass MergeResult: applied_paths: list[str], conflicts: list[Conflict]`.
  - `class WriterMap` — cumulative `path -> first_writing_plan_id` across all merged waves; `record(path, plan_id)`, `writer_of(path) -> str | None`.
  - `detect_conflicts(changes_by_plan: dict[str, list[Change]], writer_map: WriterMap, is_dependent: Callable[[str, str], bool]) -> list[Conflict]` per §7.4: same-wave siblings on the same leaf path (modify/modify, modify/delete, delete/add, identical-content add/add); later-wave non-dependent vs earlier writer; directory-prefix relations (a deleted dir vs add/modify under it by a non-dependent). **Exemptions auto-merge:** delete/delete (one deletion), directory add/add (no digest), divergent added-dir mode bits (last-writer-wins). A build-on-dependency change is not a conflict.
  - `apply_merge(target: Path, sandboxes: dict[str, Path], changes_by_plan: dict[str, list[Change]], conflicting_paths: set[str]) -> MergeResult` — applies non-conflicting changes by §7.4 kind order (dirs parents-first → file/symlink add+modify → mode-changes → deletions → deepest-first empty-dir prune), with the remove-before-recreate rule for symlink retarget and type-flips, missing-path-tolerant deletes, under `umask(0o077)` (explicit `chmod` to recorded post-edit mode).
  - `resolve_conflict_winner(plan_a: str, plan_b: str, file_scope: dict[str, list[str]]) -> tuple[str, str]` — returns `(winner, loser)` by narrower/owning `file_scope` then lower `id`; the caller adds `depends_on(loser→winner)` and **never** an edge that creates a cycle (keep the existing order if the reverse edge exists).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sandbox_merge.py
from __future__ import annotations

import os
from pathlib import Path

from forge_mcp.sandbox import (
    Change, WriterMap, apply_merge, capture_manifest, detect_changes,
    detect_conflicts, resolve_conflict_winner,
)


def _no_dep(a: str, b: str) -> bool:
    return False  # no plan depends on any other in these tests


def _changes(root: Path, mutate) -> list[Change]:
    base = capture_manifest(root)
    mutate(root)
    return detect_changes(root, base)


def test_disjoint_files_merge_clean(tmp_path):
    """Design: §7.4 disjoint adds from two plans union cleanly.
    Implementation: two sandboxes add different files; merge applies both.
    Example: target has both files, no conflicts.
    """
    target = tmp_path / "t"; target.mkdir()
    sa = tmp_path / "a"; sb = tmp_path / "b"
    for s, f in ((sa, "a.txt"), (sb, "b.txt")):
        s.mkdir(); ca = _changes(s, lambda r, f=f: (r / f).write_text("x"))
        globals().setdefault("_c", {})[s] = ca
    ca = _changes(sa, lambda r: (r / "a.txt").write_text("x"))  # recompute cleanly
    cb = _changes(sb, lambda r: (r / "b.txt").write_text("y"))
    wm = WriterMap()
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, wm, _no_dep)
    assert conflicts == []
    res = apply_merge(target, {"pa": sa, "pb": sb}, {"pa": ca, "pb": cb}, set())
    assert (target / "a.txt").exists() and (target / "b.txt").exists()
    assert res.conflicts == []


def test_same_path_modify_modify_is_conflict_no_apply(tmp_path):
    """Design: §7.4/I9 two plans modifying one path -> conflict, apply none.
    Implementation: both edit the same file.
    Example: conflict reported; winner chosen by id.
    """
    sa = tmp_path / "a"; sb = tmp_path / "b"
    for s in (sa, sb):
        s.mkdir(); (s / "f.txt").write_text("base")
    ca = _changes(sa, lambda r: (r / "f.txt").write_text("A"))
    cb = _changes(sb, lambda r: (r / "f.txt").write_text("B"))
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep)
    assert len(conflicts) == 1 and conflicts[0].path == "f.txt"
    winner, loser = resolve_conflict_winner("pa", "pb", {"pa": [], "pb": []})
    assert (winner, loser) == ("pa", "pb")  # lower id wins on a scope tie


def test_delete_delete_auto_merges(tmp_path):
    """Design: §7.4/I9 delete/delete agrees on absence -> not a conflict.
    Implementation: both delete the same file.
    Example: conflicts empty.
    """
    sa = tmp_path / "a"; sb = tmp_path / "b"
    for s in (sa, sb):
        s.mkdir(); (s / "f.txt").write_text("x")
    ca = _changes(sa, lambda r: (r / "f.txt").unlink())
    cb = _changes(sb, lambda r: (r / "f.txt").unlink())
    assert detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep) == []


def test_executable_bit_preserved_through_merge(tmp_path):
    """Design: §7.4 explicit chmod carries the executable bit under umask 0o077.
    Implementation: add an executable file; merge; check mode.
    Example: target file is 0o755.
    """
    target = tmp_path / "t"; target.mkdir()
    sa = tmp_path / "a"; sa.mkdir()
    def add_exec(r):
        p = r / "run.sh"; p.write_text("#!/bin/sh\n"); os.chmod(p, 0o755)
    ca = _changes(sa, add_exec)
    old = os.umask(0o077)
    try:
        apply_merge(target, {"pa": sa}, {"pa": ca}, set())
    finally:
        os.umask(old)
    assert (os.stat(target / "run.sh").st_mode & 0o111)


def test_cumulative_cross_wave_conflict(tmp_path):
    """Design: §7.4 a later non-dependent plan touching an earlier-written path conflicts.
    Implementation: writer map already has the path from a prior wave.
    Example: conflict detected for the second plan.
    """
    sb = tmp_path / "b"; sb.mkdir(); (sb / "f.txt").write_text("base")
    cb = _changes(sb, lambda r: (r / "f.txt").write_text("B"))
    wm = WriterMap(); wm.record("f.txt", "pa")  # earlier wave wrote it
    conflicts = detect_conflicts({"pb": cb}, wm, _no_dep)
    assert len(conflicts) == 1 and "pa" in conflicts[0].plan_ids
```

> Note: the first test's helper churn is illustrative; the executing engineer should simplify to two clean `_changes` calls. Keep the assertions.

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement merge + overlap + conflict in `src/forge_mcp/sandbox.py`**

Translate §7.4 precisely. Critical points (do not skip):
- **Apply order (1)-(4)** with the **remove-before-recreate** rule: symlink add/modify → `os.unlink(dst)` (if `lexists`) then `os.symlink`; type-flip → delete-old-kind (`os.unlink`/`shutil.rmtree`) then add-new-kind; file→dir flip → `os.unlink` the leaf **before** `os.makedirs`. Regular-file modify truncates in place via `open(dst,"wb")`.
- **Added dirs** via `os.makedirs(path, exist_ok=True)` then `os.chmod` to the change's recorded post-edit mode (else umask loses the bits). Sibling-already-created dir is a no-op.
- **File/symlink add+modify**: byte-copy then `os.chmod` to the change's recorded mode (carries the exec bit under umask 0o077). Symlinks: recreate via `os.symlink` (no chmod).
- **Deletions** missing-path-tolerant (`os.path.lexists` guard or ignore `FileNotFoundError`).
- **Prune** deepest-first: remove a dir only if `os.listdir(d) == []` **and** it is not an intentionally-added `dir` in any change-set; after all adds/modifies/deletes.
- **Conflict detection** is over **leaf** file/symlink paths only for add/add; directory add/add is never a content conflict; directory-prefix: a deleted dir vs any add/modify under it by a non-dependent. delete/delete exempt. Cumulative via `WriterMap`. A change whose other writer is the path's transitive dependency is not a conflict (`is_dependent(later, earlier)`).
- **Winner selection** by narrower/owning `file_scope` (fewer/more-specific globs) then lower `id`.

- [ ] **Step 4: Run tests to verify they pass** → `uv run pytest tests/test_sandbox_merge.py -q` → PASS. (Also re-run `tests/test_sandbox_manifest.py` to confirm no regression.)

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/sandbox.py tests/test_sandbox_merge.py
git commit -m "feat(sandbox): change-kind merge, cumulative overlap, conflict state machine (§7.4)"
```

---

### Task 14: `drivers/_claude.py` — Claude SDK seam + contract test (Claude half)

**Files:**
- Create: `src/forge_mcp/drivers/_claude.py`
- Create: `src/forge_mcp/schemas/__init__.py` (envelope helper)
- Create/Modify: `tests/test_sdk_contract.py` (Claude assertions)
- Test: `tests/test_claude_seam.py`

**Interfaces:**
- Consumes: `forge_mcp.gitguard.git_deny_matches`; `forge_mcp.config` (`claude_bin`, `claude_config_dir`); `forge_mcp.models` (for schema envelopes via `schemas`).
- Produces (§8.1/§8.3):
  - `schemas.envelope(bare: dict) -> dict` = `{"type":"json_schema","schema":bare}` (idempotent — don't double-wrap).
  - `build_options(*, skills="all", setting_sources=("user","project","local"), system, output_format=None, cwd=None, add_dirs=None, disallowed_tools=(), cli_path=None, hooks=None, stderr=None) -> ClaudeAgentOptions` — the single chokepoint (§8.1). **Lazy `import` of `claude_agent_sdk` inside the function.**
  - `git_deny_hooks() -> dict` — `{"PreToolUse": [HookMatcher(matcher="Bash", hooks=[deny_cb])]}` where `deny_cb` returns `{}` to pass or the deny envelope `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":<str>}}` when `git_deny_matches(command)`.
  - `@dataclass StructuredResult: structured_output: dict | None, text: str, session_id: str | None`.
  - `class ClaudeDriver` implementing the `ClaudeRunner` Protocol (`last_session_id`, `async run(*, prompt, options) -> StructuredResult`, `async interrupt()`, `async aclose()`), using `ClaudeSDKClient` + `receive_response()` per Decision D6, with transient-error retry (`ConnectionError|BrokenPipeError|CLIConnectionError`; **never** `TimeoutError`/`CancelledError`; schema-mismatch retry once).
  - The `ClaudeRunner` `Protocol` itself (in `_claude.py` or a shared `drivers/_protocols.py`).

- [ ] **Step 0 (REQUIRED): Re-introspect the installed SDK (§8.4)**

Run and record the real shapes before writing the seam:
```bash
uv run python -c "import claude_agent_sdk as c, inspect; print(c.__version__); \
print([n for n in dir(c) if 'Option' in n or 'Client' in n or 'Hook' in n or 'Connection' in n])"
```
Confirm `ClaudeAgentOptions`, `ClaudeSDKClient`, `HookMatcher`, `CLIConnectionError`, `ResultMessage.structured_output`, the `skills`/`tools`/`setting_sources`/`system_prompt`/`permission_mode` fields, and the init `SystemMessage.data["session_id"]`/`["skills"]` keys. Treat installed-version equivalents as conformant.

- [ ] **Step 1: Write the failing tests** (seam logic that does **not** require a live CLI — hook matcher + envelope + Protocol shape)

```python
# tests/test_claude_seam.py
from __future__ import annotations

import asyncio

from forge_mcp.drivers import _claude
from forge_mcp.schemas import envelope


def test_envelope_is_idempotent():
    """Design: §8.1 output_format must be {type:json_schema, schema:<bare>}, wrapped once.
    Implementation: wrapping an already-enveloped schema is a no-op.
    Example: envelope(envelope(s)) == envelope(s).
    """
    bare = {"type": "object", "properties": {}}
    once = envelope(bare)
    assert once == {"type": "json_schema", "schema": bare}
    assert envelope(once) == once


def test_git_deny_hook_blocks_mutation_passes_safe():
    """Design: §8.1/§9 the PreToolUse hook denies git-mutating Bash, passes the rest.
    Implementation: call the deny callback with a git commit and a safe command.
    Example: commit -> deny envelope; status -> {}.
    """
    hooks = _claude.git_deny_hooks()
    cb = hooks["PreToolUse"][0].hooks[0]
    deny = asyncio.run(cb({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, "id", None))
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = asyncio.run(cb({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}, "id", None))
    assert ok == {}
```

```python
# tests/test_sdk_contract.py  (Claude assertions; importable-gated, NOT slow)
from __future__ import annotations

import importlib.util

import pytest

claude_present = importlib.util.find_spec("claude_agent_sdk") is not None


@pytest.mark.skipif(not claude_present, reason="claude-agent-sdk not installed")
def test_claude_symbols_exist():
    """Design: §8.4 the seam's symbols must exist in the installed SDK.
    Implementation: import and getattr the load-bearing names.
    Example: ClaudeAgentOptions, ClaudeSDKClient, HookMatcher present.
    """
    import inspect

    import claude_agent_sdk as c

    assert hasattr(c, "ClaudeAgentOptions")
    assert hasattr(c, "ClaudeSDKClient")
    assert hasattr(c, "HookMatcher")
    sig = inspect.signature(c.ClaudeSDKClient.__init__)
    assert "options" in sig.parameters
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement the seam** per §8.1 verified facts. `build_options` sets `permission_mode="bypassPermissions"`, `tools={"type":"preset","preset":"claude_code"}`, `system_prompt=system`, `skills=skills`, `setting_sources=list(setting_sources)`, idempotent `output_format` envelope, plus `cwd`/`add_dirs`/`disallowed_tools`/`cli_path`/`hooks`/`stderr`. `ClaudeDriver.run` drains `receive_response()`, keeps last non-`None` `structured_output`, concatenates `TextBlock.text`, captures `session_id` from the init `SystemMessage`, classifies transients (NOT `TimeoutError`/`CancelledError`), retries schema-mismatch once with a pinned suffix and transients ≤3 with backoff; `interrupt()` is getattr-guarded best-effort.

- [ ] **Step 4: Run tests to verify they pass** → `uv run pytest tests/test_claude_seam.py tests/test_sdk_contract.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/drivers/_claude.py src/forge_mcp/schemas/__init__.py tests/test_claude_seam.py tests/test_sdk_contract.py
git commit -m "feat(drivers): Claude SDK seam, git-deny hook, schema envelope + contract test (§8.1)"
```

---

### Task 15: `drivers/_codex.py` — Codex SDK seam + contract test (Codex half)

**Files:**
- Create: `src/forge_mcp/drivers/_codex.py`
- Modify: `tests/test_sdk_contract.py` (Codex assertions)
- Test: `tests/test_codex_seam.py`

**Interfaces:**
- Consumes: `forge_mcp.config.codex_bin`.
- Produces (§8.2/§8.3):
  - `build_codex_config(*, codex_bin: str, cwd: Path, env: dict | None = None)` → `CodexConfig(codex_bin=codex_bin, cwd=str(cwd), env=env or {})` (**lazy import** of `openai_codex`).
  - `@dataclass(frozen=True) CodexEvent: kind: str, payload: dict`.
  - `class CodexDriver` implementing `CodexRunner` (`last_thread_id` property, `generate(*, instructions, config, run_log_path=None) -> AsyncIterator[CodexEvent]`, `async interrupt()`, `async aclose()`) — per §8.2: `AsyncCodex(config=config)`, **stderr tee installed BEFORE `__aenter__`, fail-soft (Decision D4)**, `thread_start(sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all, cwd=…)`, `thread.turn(TextInput(text=instructions), cwd=…, approval_mode=ApprovalMode.deny_all)`, stream `.method`/`.payload` with a `model_dump → dict → {}` triple fallback, idempotent close. Transient classification: `ConnectionError|BrokenPipeError|TransportClosedError|is_retryable_error(exc)`; **never** `TimeoutError`/`CancelledError`.
  - `CodexRunner` `Protocol`.

- [ ] **Step 0 (REQUIRED): Re-introspect the installed `0.1.0b2` wheel (§8.2/§8.4)**

```bash
uv run --with 'openai-codex>=0.1.0b2,<0.2' python -c "import openai_codex as o, inspect; print(o.__version__); \
print(inspect.signature(o.AsyncCodex.thread_start)); \
from openai_codex import Sandbox, ApprovalMode; print(list(Sandbox), list(ApprovalMode))"
```
Confirm against the **`0.1.0b2`** dist-info (not `0.131.0a4`): `CodexConfig`, `Sandbox.full_access`, `ApprovalMode.deny_all`, `TransportClosedError` in `openai_codex.errors`, no public `sandbox_policy=` on `turn()`. Record the exact private stderr-deque attribute chain for the tee.

- [ ] **Step 1: Write the failing tests** (logic not needing a live binary — config build + event dataclass + tee fail-soft)

```python
# tests/test_codex_seam.py
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from forge_mcp.drivers import _codex

codex_present = importlib.util.find_spec("openai_codex") is not None


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_build_codex_config(tmp_path: Path):
    """Design: §8.2 the launch config class is CodexConfig (NOT AppServerConfig).
    Implementation: build and assert cwd threaded.
    Example: build_codex_config(codex_bin='codex', cwd=tmp).cwd == str(tmp).
    """
    cfg = _codex.build_codex_config(codex_bin="codex", cwd=tmp_path)
    assert str(tmp_path) in str(cfg.cwd)


def test_codex_event_payload_fallback():
    """Design: §8.2 payload uses a model_dump -> dict -> {} fallback.
    Implementation: _dump on an object lacking model_dump returns {} or dict.
    Example: _dump(object()) == {}.
    """
    assert _codex._dump(object()) == {}
    assert _codex._dump({"a": 1}) == {"a": 1}
```

```python
# tests/test_sdk_contract.py  (append Codex assertions)
import importlib.util
import pytest

codex_present = importlib.util.find_spec("openai_codex") is not None


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_codex_symbols_exist():
    """Design: §8.4 re-introspect the installed Codex SDK symbols.
    Implementation: assert the 0.1.0b2-line shapes (or installed equivalents).
    Example: Sandbox.full_access, ApprovalMode.deny_all present.
    """
    import inspect

    import openai_codex as o
    from openai_codex import ApprovalMode, Sandbox

    assert hasattr(Sandbox, "full_access")
    assert hasattr(ApprovalMode, "deny_all")
    params = inspect.signature(o.AsyncCodex.thread_start).parameters
    assert "sandbox" in params and "approval_mode" in params
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement the seam** per §8.2 verified facts and Decision D4 (fail-soft tee). `_dump(payload)` = `payload.model_dump()` if available, elif `isinstance(payload, dict)` → it, else `{}`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/drivers/_codex.py tests/test_codex_seam.py tests/test_sdk_contract.py
git commit -m "feat(drivers): Codex SDK seam (0.1.0b2), full-access never-ask, fail-soft tee (§8.2)"
```

---

## Wave 3 — stages, state machines, lock, skills (T16–T22 concurrent)

### Task 16: `lockfile.py` — per-target lock, no adoption (Decision D1)

**Files:**
- Create: `src/forge_mcp/lockfile.py`
- Test: `tests/test_lockfile.py`

**Interfaces:**
- Consumes: `psutil`; stdlib.
- Produces (§12, **option a** per Decision D1):
  - `@dataclass(frozen=True) LockPayload: pid: int, run_id: str, started_at: str, target_dir: str, create_time: float`.
  - `class TargetLock` — `acquire(target_dir: Path, run_id: str, started_at: str) -> None` (writes `<target_dir>/.harness/run.lock` via `os.open(O_CREAT|O_EXCL|O_WRONLY, 0o600)`, `os.write` the JSON payload, fsync, close; on `FileExistsError` runs **liveness-first stale recovery**); `release() -> None` (idempotent); context-manager sugar.
  - Stale recovery: steal only on an unreadable payload, a dead pid (`psutil.pid_exists` false / `kill(pid,0)` raises), or a `create_time` mismatch (PID-reuse guard via `psutil.Process(pid).create_time()`). The steal is serialized by an `O_CREAT|O_EXCL` guard `run.lock.steal` carrying its own `{pid, create_time}`; re-confirm staleness, `os.replace` a temp payload over `run.lock`, remove the guard. **Never steal a live healthy run.** **No `adopt_run_id`** (no resume).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lockfile.py
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forge_mcp.lockfile import TargetLock


def test_acquire_writes_payload_and_blocks_second(tmp_path: Path):
    """Design: §12 the lock serializes runs per target and carries a payload.
    Implementation: first acquire writes run.lock; a second acquire on the live
        lock raises (cannot steal a healthy holder).
    Example: run.lock content is the JSON payload.
    """
    a = TargetLock()
    a.acquire(tmp_path, run_id="20260623183102", started_at="t0")
    data = json.loads((tmp_path / ".harness" / "run.lock").read_text())
    assert data["pid"] == os.getpid() and data["run_id"] == "20260623183102"
    with pytest.raises(Exception):
        TargetLock().acquire(tmp_path, run_id="20260623183103", started_at="t1")
    a.release()


def test_release_is_idempotent(tmp_path: Path):
    """Design: §12 release() is idempotent.
    Implementation: double release does not raise; a fresh acquire then works.
    Example: second acquire succeeds after release.
    """
    a = TargetLock()
    a.acquire(tmp_path, run_id="20260623183102", started_at="t0")
    a.release()
    a.release()
    TargetLock().acquire(tmp_path, run_id="20260623183104", started_at="t2")


def test_steals_dead_holder(tmp_path: Path):
    """Design: §12 liveness-first stale recovery steals a dead holder's lock.
    Implementation: write a run.lock with an impossible pid; acquire steals it.
    Example: acquire succeeds and rewrites the payload.
    """
    harness = tmp_path / ".harness"; harness.mkdir()
    (harness / "run.lock").write_text(json.dumps(
        {"pid": 2**30, "run_id": "old", "started_at": "x",
         "target_dir": str(tmp_path), "create_time": 0.0}))
    lock = TargetLock()
    lock.acquire(tmp_path, run_id="20260623183105", started_at="t3")
    assert json.loads((harness / "run.lock").read_text())["run_id"] == "20260623183105"
    lock.release()
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/lockfile.py`** per §12 option (a). Use `os.getpid()` and `psutil.Process(os.getpid()).create_time()` for the payload; `_holder_is_alive(payload)` returns False on missing pid, `not psutil.pid_exists`, or `create_time` mismatch (degrade to pid-liveness where `create_time` unavailable). Implement the `O_EXCL` steal-guard critical section exactly as §12 specifies (re-confirm staleness, `os.replace` temp over `run.lock`, remove guard; on guard `FileExistsError` apply the same liveness check to the guard). No mtime steal trigger unless pid cannot be probed, and then only with a threshold `> max_runtime_minutes`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/lockfile.py tests/test_lockfile.py
git commit -m "feat(lockfile): crash-safe O_EXCL per-target lock, PID-reuse-safe steal (§12)"
```

---

### Task 17: `drivers/planner.py` — Planner stage + Protocol fakes

**Files:**
- Create: `src/forge_mcp/drivers/planner.py`
- Create: `tests/fakes.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `ClaudeRunner` (Task 14), `forge_mcp.models` (`PlanSet`, `Plan`), `forge_mcp.prompts.load_prompt`, `forge_mcp.schemas.envelope`.
- Produces:
  - `tests/fakes.py`: `FakeClaudeRunner(scripted: list[StructuredResult])` and `FakeCodexRunner(events: list[CodexEvent])` implementing the Protocols, plus a `runtime_checkable` assertion helper. (Reused by Tasks 18/19/23/24/26/27/28.)
  - `async run_planner(runner: ClaudeRunner, *, spec_text: str, plan_schema: dict, cwd: Path) -> PlanSet` — builds options (skills enabling the plan-writing skill, system = `planner_system`, `output_format = envelope(plan_schema)`, git-deny hook), queries with the spec, parses `structured_output` into `PlanSet`. Records ambiguities as plan open-questions (in plan bodies), never resolves silently.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planner.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.planner import run_planner
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_planner_parses_planset(tmp_path: Path):
    """Design: §5.1 the Planner emits a structured PlanSet from spec.md.
    Implementation: a fake runner returns a PlanSet dict; run_planner parses it.
    Example: PlanSet with 2 plans and a dependency edge.
    """
    payload = {
        "plans": [
            {"id": "p1", "depends_on": [], "surface": "backend",
             "file_scope": ["src/**"], "verification_command": "pytest -q", "body": "plan 1"},
            {"id": "p2", "depends_on": ["p1"], "surface": "frontend",
             "file_scope": ["web/**"], "verification_command": None, "body": "plan 2"},
        ],
        "run_verification_command": "pytest -q",
    }
    runner = FakeClaudeRunner([structured(payload)])
    ps = await run_planner(runner, spec_text="# design", plan_schema={"type": "object"}, cwd=tmp_path)
    assert [p.id for p in ps.plans] == ["p1", "p2"]
    assert ps.plans[1].depends_on == ["p1"]
    assert ps.run_verification_command == "pytest -q"
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement `tests/fakes.py`** (the fakes return scripted `StructuredResult`/`CodexEvent` objects; `structured(payload)` builds a `StructuredResult(structured_output=payload, text="", session_id="fake")`) and `src/forge_mcp/drivers/planner.py` (`run_planner` builds options via the Claude seam and validates `PlanSet(**runner_result.structured_output)`).

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/drivers/planner.py tests/fakes.py tests/test_planner.py
git commit -m "feat(planner): structured PlanSet stage + Protocol fakes (§5.1)"
```

---

### Task 18: `drivers/generator.py` — Generator stage

**Files:**
- Create: `src/forge_mcp/drivers/generator.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: `CodexRunner` (Task 15), `forge_mcp.prompts.load_prompt`, `forge_mcp.config.codex_bin`.
- Produces:
  - `async run_generator(runner: CodexRunner, *, contract_text: str, sandbox: Path, surface: str, run_log_path: Path | None = None) -> list[CodexEvent]` — builds the Codex config rooted at `sandbox`, composes the system prompt (`generator_system`) + the contract, selects the skill **by capability** (backend→plan-execution, frontend→frontend-design) referenced in the instructions, streams events to exhaustion, returns the collected events. Generator runs full-access in the sandbox; no git is possible there.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.drivers.generator import run_generator
from tests.fakes import FakeCodexRunner


@pytest.mark.driver
async def test_generator_streams_events(tmp_path: Path):
    """Design: §5.2 the Generator runs one full-access turn in the sandbox.
    Implementation: a fake runner yields two events; run_generator collects them.
    Example: returns both events in order.
    """
    sandbox = tmp_path / "sb"; sandbox.mkdir()
    runner = FakeCodexRunner([CodexEvent(kind="item.started", payload={}),
                              CodexEvent(kind="turn.completed", payload={"ok": True})])
    events = await run_generator(runner, contract_text="do X", sandbox=sandbox, surface="backend")
    assert [e.kind for e in events] == ["item.started", "turn.completed"]
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/drivers/generator.py`** — `run_generator` consumes the async iterator from `runner.generate(...)` and accumulates events; the instructions reference the skill by capability (no "Skill tool" wording, §10.1).

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/drivers/generator.py tests/test_generator.py
git commit -m "feat(generator): full-access Codex turn in sandbox, capability-referenced skills (§5.2)"
```

---

### Task 19: `drivers/evaluator.py` — Evaluator stage (eval + triage)

**Files:**
- Create: `src/forge_mcp/drivers/evaluator.py`
- Test: `tests/test_evaluator.py`

**Interfaces:**
- Consumes: `ClaudeRunner`, `forge_mcp.models` (`EvalResult`, `TriageResult`), `forge_mcp.triage` (citation gate for post-processing demotion), `forge_mcp.prompts`, `forge_mcp.schemas.envelope`.
- Produces:
  - `async run_evaluator(runner, *, spec_text, sandbox: Path, eval_schema: dict, cwd: Path) -> EvalResult` — code-review skill, diff sandbox code vs frozen `spec.md`; parses `EvalResult`.
  - `async run_triage(runner, *, spec_text, eval_result: EvalResult, triage_schema: dict, cwd: Path) -> TriageResult` — only if gaps; classifies each gap; applies the citation gate so a row that fails demotes to an implementation gap (uses `forge_mcp.triage.passes_citation_gate`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evaluator.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.evaluator import run_evaluator, run_triage
from forge_mcp.models import EvalResult
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_evaluator_parses_gaps(tmp_path: Path):
    """Design: §5.3 the Evaluator emits EvalResult diffing code vs spec.
    Implementation: fake returns one gap; run_evaluator parses it.
    Example: no_gaps False, one EvalGap.
    """
    payload = {"no_gaps": False, "summary": "1 gap", "gaps": [
        {"title": "missing X", "severity": "high", "design_doc_section": "§7.4",
         "current_state": "absent", "expected_state": "present", "suggested_fix": "add X"}]}
    runner = FakeClaudeRunner([structured(payload)])
    res = await run_evaluator(runner, spec_text="s", sandbox=tmp_path,
                              eval_schema={"type": "object"}, cwd=tmp_path)
    assert not res.no_gaps and res.gaps[0].title == "missing X"


@pytest.mark.driver
async def test_triage_demotes_uncited_design_fault(tmp_path: Path):
    """Design: §5.3 a design_fault with an invalid citation demotes to a code-bug.
    Implementation: fake triage claims a design fault citing absent text.
    Example: the row no longer passes the citation gate.
    """
    spec = "real spec text that is long enough to cite verbatim here."
    ev = EvalResult(no_gaps=False, summary="x", gaps=[])
    payload = {"triages": [{"gap_title": "missing X", "design_fault": True,
                            "fault_kind": "contradiction",
                            "cited_sections": ["not in the spec at all here!!"],
                            "explanation": "e"}]}
    runner = FakeClaudeRunner([structured(payload)])
    tr = await run_triage(runner, spec_text=spec, eval_result=ev,
                          triage_schema={"type": "object"}, cwd=tmp_path)
    from forge_mcp.triage import passes_citation_gate
    assert not passes_citation_gate(tr.triages[0], spec)
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/drivers/evaluator.py`** per §5.3. Parse `EvalResult`/`TriageResult` from `structured_output`. (The orchestrator, not this stage, applies amendments — §5.3/I4.)

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/drivers/evaluator.py tests/test_evaluator.py
git commit -m "feat(evaluator): skeptical eval + triage stages with citation gate (§5.3)"
```

---

### Task 20: `orchestrator/statemachine.py` — run-level state

**Files:**
- Create: `src/forge_mcp/orchestrator/statemachine.py`
- Test: `tests/test_statemachine.py`

**Interfaces:**
- Consumes: `forge_mcp.state.write_json`, `forge_mcp.artifacts.RunLayout`.
- Produces (§3.3):
  - `RunStatePayload(BaseModel, extra="forbid")` — `state: Literal[...]`, `last_phase: str | None`, `last_updated_at: str`, plus run metadata (`run_dir`, `iterations`, `wave`).
  - States: `init → planning → (scheduling → executing → merging → amending)* → verifying → finalizing → {completed|incomplete|failed}`. The per-wave cycle repeats; terminal states do not advance `last_phase`.
  - `class RunStateMachine` — sole writer of run-level `state.json`; `transition(to: str, *, now: str) -> None` validates the legal edge, updates `last_phase` (non-terminal only), `model_validate`s, and durably writes via `RunLayout.state_json`. Illegal transition raises.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_statemachine.py
from __future__ import annotations

import json

import pytest

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.statemachine import RunStateMachine


def test_legal_path_and_wave_cycle(tmp_path):
    """Design: §3.3 the run advances init->planning->wave-cycle->verifying->finalizing->terminal.
    Implementation: drive a legal sequence including a repeated wave cycle.
    Example: state.json reflects each transition.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in ["planning", "scheduling", "executing", "merging", "amending",
               "scheduling", "executing", "merging", "amending",
               "verifying", "finalizing", "completed"]:
        sm.transition(to, now="t")
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "completed"


def test_illegal_transition_raises(tmp_path):
    """Design: §3.3 illegal edges are rejected (single source of legal ordering).
    Implementation: jump init->verifying.
    Example: raises.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    with pytest.raises(Exception):
        sm.transition("verifying", now="t")
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/statemachine.py`** with an explicit adjacency map of legal edges (`init→{planning}`, `planning→{scheduling}`, `scheduling→{executing}`, `executing→{merging}`, `merging→{amending}`, `amending→{scheduling,verifying}`, `verifying→{finalizing}`, `finalizing→{completed,incomplete,failed}`). `failed` is reachable from any non-terminal (orchestrator-internal error). Terminal states don't advance `last_phase`. Durable write each transition.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/statemachine.py tests/test_statemachine.py
git commit -m "feat(orchestrator): run-level state machine, single-writer durable (§3.3)"
```

---

### Task 21: `orchestrator/plan_state.py` — per-plan durable state

**Files:**
- Create: `src/forge_mcp/orchestrator/plan_state.py`
- Test: `tests/test_plan_state.py`

**Interfaces:**
- Consumes: `forge_mcp.state.write_json`, `forge_mcp.artifacts.RunLayout`.
- Produces (§3.3):
  - `PlanStatePayload(BaseModel, extra="forbid")` — `plan_id`, `sandbox_path`, `state: Literal["generating","verifying","evaluating","triaging","remediating","awaiting_amendment","done","incomplete","failed"]`, `iteration`, `last_completed_iteration`, `last_updated_at`, optional `stop_reason`.
  - `class PlanState` — sole writer of `plans/<id>/state.json`; `set_state(state, *, now)`, `bump_iteration(now)`, `record_completed(n, now)`. **`merge_status` is NOT here** — it lives only in the orchestrator-owned `merge.json` (I1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_state.py
from __future__ import annotations

import json

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.plan_state import PlanState


def test_plan_state_writes_own_file_no_merge_status(tmp_path):
    """Design: §3.3/I1 per-plan state is single-writer and excludes merge_status.
    Implementation: set states and assert the file; assert no merge_status field.
    Example: state.json under plans/p1/.
    """
    ps = PlanState(RunLayout.for_run(tmp_path), plan_id="p1", sandbox_path="/sb")
    ps.set_state("generating", now="t")
    ps.bump_iteration(now="t")
    ps.set_state("done", now="t")
    data = json.loads((tmp_path / "plans" / "p1" / "state.json").read_text())
    assert data["state"] == "done" and data["iteration"] == 1
    assert "merge_status" not in data
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/plan_state.py`** — durable whole-file replace to `RunLayout.plan_state(plan_id)`; `os.makedirs` the plan dir on first write.

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/plan_state.py tests/test_plan_state.py
git commit -m "feat(orchestrator): per-plan durable state, merge_status kept out (§3.3/I1)"
```

---

### Task 22: `skills.py` — per-engine skill discovery probes

**Files:**
- Create: `src/forge_mcp/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `forge_mcp.config` (`claude_config_dir`, `codex_bin`); the Claude seam (for the deterministic Claude-side probe).
- Produces (§10.3):
  - `@dataclass(frozen=True) SkillProbe: label: str, status: Literal["OK","WARN","FAIL"], detail: str`.
  - `probe_codex_skills(*, codex_home: Path) -> list[SkillProbe]` — inspect `~/.codex` plugin cache / `~/.codex/skills/` for the required ids (plan-execution, frontend-design); `FAIL` if a required Codex skill is not discoverable (this legitimately FAILs until the operator installs them — §10.3). Pure filesystem; no live binary needed for the unit test.
  - `async probe_claude_skills(*, runner, required: tuple[str, ...], timeout: float) -> list[SkillProbe]` — open a real Claude session, read the init `SystemMessage.data["skills"]`; `FAIL` if a required Claude skill (writing-plans, code-review) is absent. (Unit-tested with a fake runner that surfaces a skills list.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skills.py
from __future__ import annotations

from pathlib import Path

from forge_mcp.skills import probe_codex_skills


def test_codex_probe_fails_when_skill_absent(tmp_path: Path):
    """Design: §10.3 the Codex probe FAILs when a required skill is not discoverable.
    Implementation: an empty ~/.codex yields FAIL rows.
    Example: status FAIL for plan-execution.
    """
    (tmp_path / "skills").mkdir()
    probes = probe_codex_skills(codex_home=tmp_path)
    assert any(p.status == "FAIL" for p in probes)


def test_codex_probe_ok_when_present(tmp_path: Path):
    """Design: §10.3 a present skill dir yields OK.
    Implementation: create the expected skill folders.
    Example: status OK when both ids discoverable.
    """
    for sid in ("executing-plans", "frontend-design"):
        (tmp_path / "skills" / sid).mkdir(parents=True)
    probes = probe_codex_skills(codex_home=tmp_path)
    assert all(p.status != "FAIL" for p in probes)
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/skills.py`** per §10.3. The Codex probe checks `codex_home/"skills"/<id>` and the plugin cache; the required-id set is `[verify-against-installed]` candidates referenced by capability.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/skills.py tests/test_skills.py
git commit -m "feat(skills): per-engine skill discovery probes (§10.3)"
```

---

## Wave 4 — boundary logic + doctor (T23–T25 concurrent)

### Task 23: `orchestrator/lifecycle.py` — terminal honesty & result projection

**Files:**
- Create: `src/forge_mcp/orchestrator/lifecycle.py`
- Test: `tests/test_lifecycle.py`

**Interfaces:**
- Consumes: `forge_mcp.models` (`RunResult`, `GapSummary`, `EvalGap`, `Plan`), `forge_mcp.orchestrator.plan_state.PlanStatePayload`.
- Produces (§6.4/§4.3, pure projection — no I/O):
  - `synthesized_gap_for_failed_plan(plan_id: str, reason: str) -> GapSummary` — a failure gap (sentinel `design_doc_section`) for a `failed` plan with no gap set, so it is never silently absent.
  - `project_unresolved_gaps(non_completed: list[PlanReport]) -> list[GapSummary]` where `PlanReport` carries each non-completed plan's **freshest full post-synthesize** gap set (eval gaps ∪ synthesized git/verify gaps) — projected to `GapSummary` rows, per-plan freshest, **not** aggregated across iterations, **not** collapsed across plans (§4.3/§6.4/I7).
  - `build_run_result(*, status, run_dir, iterations, non_completed, stop_reason, verified, summary, failure_kind=None) -> RunResult`.
  - `@dataclass PlanReport: plan_id: str, terminal_state: str, gaps: list[EvalGap], synthesized: list[GapSummary], failure_reason: str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lifecycle.py
from __future__ import annotations

from forge_mcp.models import EvalGap, GapSummary
from forge_mcp.orchestrator.lifecycle import (
    PlanReport, build_run_result, project_unresolved_gaps,
    synthesized_gap_for_failed_plan,
)


def _gap(t):
    return EvalGap(title=t, severity="high", design_doc_section="§7.4",
                   current_state="c", expected_state="e", suggested_fix="f")


def test_failed_plan_gets_synthesized_gap():
    """Design: §6.4 a failed plan with no gaps contributes a synthesized failure gap.
    Implementation: build the synthesized GapSummary.
    Example: title names the plan; section is a sentinel.
    """
    g = synthesized_gap_for_failed_plan("p2", "crashed: OSError")
    assert "p2" in g.title and g.design_doc_section


def test_unresolved_gaps_are_per_plan_freshest_not_collapsed():
    """Design: §4.3/I7 union over non-completed plans, per-plan freshest, not collapsed.
    Implementation: two plans each with a distinct gap.
    Example: two GapSummary rows.
    """
    reports = [
        PlanReport("p1", "incomplete", [_gap("g1")], [], None),
        PlanReport("p2", "incomplete", [_gap("g2")],
                   [GapSummary(title="verify failed", severity="high", design_doc_section="§6.5")], None),
    ]
    rows = project_unresolved_gaps(reports)
    titles = {r.title for r in rows}
    assert {"g1", "g2", "verify failed"} <= titles


def test_build_run_result_incomplete():
    """Design: §4.3 a non-convergent run is 'incomplete' with stop_reason + gaps.
    Implementation: build and read anchors.
    Example: status incomplete, verified False.
    """
    r = build_run_result(status="incomplete", run_dir="/r", iterations=5,
                         non_completed=[PlanReport("p1", "incomplete", [_gap("g1")], [], None)],
                         stop_reason="non-progress", verified=False, summary="1/2")
    assert r.status == "incomplete" and r.stop_reason == "non-progress"
    assert r.unresolved_gaps[0].title == "g1"
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/lifecycle.py`** — pure projection per §6.4/§4.3. `GapSummary` projection copies `{title, severity, design_doc_section}` from each `EvalGap`/synthesized gap. `failure_kind` is set **only** for `status="failed"` (orchestrator-internal error), never for per-plan failure or non-convergence.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/lifecycle.py tests/test_lifecycle.py
git commit -m "feat(orchestrator): honest terminal projection of unresolved gaps (§6.4)"
```

---

### Task 24: `orchestrator/amend.py` — orchestrator-owned spec amendment

**Files:**
- Create: `src/forge_mcp/orchestrator/amend.py`
- Test: `tests/test_amend.py`

**Interfaces:**
- Consumes: `forge_mcp.triage` (`is_valid_citation`, `amendment_target_present`), `forge_mcp.models` (`GapTriage`, `ProposedAmendment`), `forge_mcp.state` (`durable_replace`, `durable_append`), `forge_mcp.artifacts.RunLayout`, `forge_mcp.convergence` (amendment-churn fingerprints).
- Produces (§5.3 AMEND, single-threaded at wave boundary):
  - `@dataclass AmendOutcome: applied: list[ProposedAmendment], rejected: list[ProposedAmendment], new_spec: str, new_fingerprint: str, churn_fingerprint: frozenset[str]`.
  - `apply_amendments(layout: RunLayout, *, spec_text: str, spec_fingerprint: str, proposed: list[tuple[str, GapTriage]], now: str) -> AmendOutcome` — for each `(plan_id, triage)` in order: **re-run the citation gate** against the **then-current** spec AND verify `proposed_amendment.before` is still a verbatim substring; if it passes, apply (replace `before`→`after` in spec), **bump the fingerprint**, durably append a structured entry to `spec_amendments.md`; if it rejects, drop it (gap demotes to implementation gap, re-queue next wave) — **both applied and rejected** proposals feed the churn fingerprint (`sorted("cited_sections|fault_kind")`). `inputs/design.md` is never touched (I4).
  - `sections_touched(applied: list[ProposedAmendment]) -> set[str]` — the cited sections, for INVALIDATE.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_amend.py
from __future__ import annotations

from forge_mcp.artifacts import RunLayout
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.orchestrator.amend import apply_amendments

SPEC = "Alpha section. The widget must flush before close. Omega section."


def _triage(before, after):
    return GapTriage(gap_title="g", design_fault=True, fault_kind="contradiction",
                     cited_sections=["The widget must flush before close"], explanation="e",
                     proposed_amendment=ProposedAmendment(
                         cited_sections=["The widget must flush before close"],
                         before=before, after=after, rationale="r"))


def test_valid_amendment_applies_bumps_fingerprint_appends_log(tmp_path):
    """Design: §5.3 a cited, present-before amendment applies, bumps fp, logs.
    Implementation: apply one valid amendment.
    Example: spec text changed; spec_amendments.md non-empty.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(lay, spec_text=SPEC, spec_fingerprint="fp0",
                           proposed=[("p1", _triage("flush before close", "flush and fsync before close"))],
                           now="t")
    assert "fsync" in out.new_spec and out.new_fingerprint != "fp0"
    assert len(out.applied) == 1 and lay.spec_amendments.read_text() != ""


def test_amendment_with_absent_before_is_rejected(tmp_path):
    """Design: §5.3 step 2 a 'before' no longer verbatim is rejected (demote).
    Implementation: before text not in spec.
    Example: rejected list has the proposal; spec unchanged.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(lay, spec_text=SPEC, spec_fingerprint="fp0",
                           proposed=[("p1", _triage("nonexistent target text", "x"))],
                           now="t")
    assert out.applied == [] and len(out.rejected) == 1 and out.new_spec == SPEC


def test_rejected_amendment_still_counts_toward_churn(tmp_path):
    """Design: §6.7 rejected proposals still feed amendment churn.
    Implementation: a rejected proposal yields a non-empty churn fingerprint.
    Example: churn_fingerprint non-empty.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    out = apply_amendments(lay, spec_text=SPEC, spec_fingerprint="fp0",
                           proposed=[("p1", _triage("nonexistent target text", "x"))],
                           now="t")
    assert len(out.churn_fingerprint) >= 1
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/amend.py`** per §5.3 steps 1–5. Apply serially; each apply re-reads the now-current spec (so a later amendment sees an earlier one's edit); bump the fingerprint via `hashlib.sha256(new_spec.encode()).hexdigest()`; append `{iteration, plan_id, fault_kind, cited_sections, before, after, rationale}` via `durable_append`; write the new spec via `durable_replace`. Churn fingerprint over **both** applied and rejected (§6.7).

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/amend.py tests/test_amend.py
git commit -m "feat(amend): orchestrator-owned spec amendment, re-cite gate, churn fp (§5.3)"
```

---

### Task 25: `check.py` — `forge check` / preflight check set

**Files:**
- Create: `src/forge_mcp/check.py`
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `forge_mcp.config`, `forge_mcp.skills`, `forge_mcp.gitguard`, the drivers (for SDK-contract introspection), `shutil`/`subprocess`.
- Produces (§4.4/§10.3):
  - `@dataclass(frozen=True) Check: label: str, status: Literal["OK","WARN","FAIL"], detail: str`.
  - `run_checks(target_dir: Path | None) -> list[Check]` — target/harness writable; `git` available; Claude CLI + auth resolvable; Codex binary + `openai_codex` import + `codex --version` smoke; SDK contract introspection (§8.4); per-engine skill discovery (§10.3); disk-space `WARN` below a threshold.
  - `any_fail(checks: list[Check]) -> bool`. `forge serve` reuses `run_checks` and maps any `FAIL` to a tagged error before starting.

- [ ] **Step 1: Write the failing tests** (deterministic checks only; SDK/binary checks tolerate absence as WARN/FAIL rows)

```python
# tests/test_check.py
from __future__ import annotations

from pathlib import Path

from forge_mcp.check import Check, any_fail, run_checks


def test_run_checks_returns_rows_and_detects_unwritable(tmp_path: Path):
    """Design: §4.4 each check returns (label, status, detail); writability is checked.
    Implementation: run against a writable dir; assert a writable-target OK row exists.
    Example: at least one OK row; any_fail computed.
    """
    checks = run_checks(tmp_path)
    assert checks and all(isinstance(c, Check) for c in checks)
    assert any("writ" in c.label.lower() for c in checks)
    _ = any_fail(checks)


def test_any_fail_true_on_fail_row():
    """Design: §4.4 any_fail is True iff a FAIL row exists.
    Implementation: synthetic rows.
    Example: returns True.
    """
    assert any_fail([Check("x", "OK", ""), Check("y", "FAIL", "broken")])
    assert not any_fail([Check("x", "OK", ""), Check("y", "WARN", "low disk")])
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/check.py`** per §4.4/§10.3. Each probe is isolated in a `try/except` and returns a row (never raises out of `run_checks`). The git check uses `shutil.which("git")`; the Codex smoke shells `codex --version` with a short timeout; the SDK-contract check imports the required seams' symbols (FAIL if SDK absent). Skill probes delegate to `forge_mcp.skills`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/check.py tests/test_check.py
git commit -m "feat(check): forge check / preflight doctor, per-engine probes (§4.4/§10.3)"
```

---

## Wave 5 — the per-plan iteration loop (T26)

### Task 26: `orchestrator/phases.py` — per-plan iteration loop

**Files:**
- Create: `src/forge_mcp/orchestrator/phases.py`
- Test: `tests/test_phases.py`

**Interfaces:**
- Consumes: `drivers.generator.run_generator`, `drivers.evaluator.run_evaluator`/`run_triage`, `verifier.run_verification`, `gitguard` (`capture_state`/`diff_state`), `convergence.detect_non_progress`/`fingerprint`, `sandbox` (manifest/change-detect), `orchestrator.plan_state.PlanState`, `triage.effective_code_bug_titles`, `artifacts.RunLayout`, `forge_mcp.models`.
- Produces (§3.2 + §6.5):
  - `@dataclass PlanLoopResult: terminal_state: Literal["done","incomplete","failed","awaiting_amendment"], iterations: int, last_gaps: list[EvalGap], synthesized: list[GapSummary], proposed_amendment: tuple[str, GapTriage] | None, stop_reason: str | None, change_set: list[Change] | None`.
  - `async run_plan_loop(*, layout, plan: Plan, sandbox: Path, spec_text: str, claude_runner, codex_runner, schemas, max_iterations: int, base_git_state) -> PlanLoopResult` — drives the §3.2 phase order per iteration: `iter_generating → iter_verifying → iter_evaluating → iter_triaging → synthesize (git + verify gaps, before fingerprint) → fingerprint (gap_fingerprint.json) → iter_done → amendment-needed? (stop wave) → completion? (§6.5 four-conjunct) → non-progress? (per-plan EARLY_STOP) → cap? → iter_remediating`.
- Key gates (§6.5):
  - `effective_no_gaps = (no remaining code-bug gaps over the full post-synthesize set, left-joined to triage) AND (eval.no_gaps OR triage_ran)`.
  - `verify_passed = (command is None) OR last_verification.passed`.
  - `no_git_violation = (this iteration's gitguard diff is empty)`.
  - `no_pending_amend = (no validated design-fault amendment awaits)`.
  - `plan_completed = all four`.

- [ ] **Step 1: Write the failing tests** (drive with fakes; assert the gate semantics)

```python
# tests/test_phases.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.artifacts import RunLayout
from forge_mcp.models import Plan
from forge_mcp.orchestrator.phases import run_plan_loop
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured
from forge_mcp.drivers._codex import CodexEvent


def _plan(**kw):
    base = dict(id="p1", depends_on=[], surface="backend", file_scope=["**"],
                verification_command=None, body="do X")
    base.update(kw)
    return Plan(**base)


@pytest.mark.driver
async def test_clean_iteration_completes(tmp_path: Path):
    """Design: §6.5 no gaps + no verify cmd + no git violation -> completed.
    Implementation: evaluator returns no_gaps; loop completes in one iteration.
    Example: terminal_state 'done'.
    """
    sandbox = tmp_path / "sb"; sandbox.mkdir()
    claude = FakeClaudeRunner([structured({"no_gaps": True, "summary": "ok", "gaps": []})])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=RunLayout.for_run(tmp_path), plan=_plan(), sandbox=sandbox,
        spec_text="s", claude_runner=claude, codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3, base_git_state=None)
    assert res.terminal_state == "done"


@pytest.mark.driver
async def test_verify_failure_synthesizes_blocking_gap(tmp_path: Path):
    """Design: §6.5 a failing verification synthesizes a non-demotable gap, blocks completion.
    Implementation: verification_command 'false' with no eval gaps -> not done.
    Example: terminal_state is 'incomplete' (cap/non-progress) with a verify gap.
    """
    sandbox = tmp_path / "sb"; sandbox.mkdir()
    claude = FakeClaudeRunner([structured({"no_gaps": True, "summary": "ok", "gaps": []})] * 3)
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 3)
    res = await run_plan_loop(
        layout=RunLayout.for_run(tmp_path), plan=_plan(verification_command="false"),
        sandbox=sandbox, spec_text="s", claude_runner=claude, codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1, base_git_state=None)
    assert res.terminal_state != "done"
    assert any("verif" in g.title.lower() or g.design_doc_section == "§6.5"
               for g in res.synthesized)
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/phases.py`** following §3.2 exactly:
  - Seed `contract.md` from the plan body (orchestrator passes iteration-1 contract; this loop reads it).
  - Each iteration: write `PlanState` transitions (`generating`→`verifying`→`evaluating`→`triaging`→`remediating`); run generator into the sandbox; run verification (cache `last_verification`); capture post-iteration git state and `diff_state(base, end)`; run evaluator → `eval.json`; if gaps, run triage → `triage.json`.
  - **synthesize before fingerprint:** append a high-severity gap for any git violation (sentinel `§9`) and any verification failure (sentinel `§6.5`) — both non-demotable.
  - Write `gap_fingerprint.json` = sorted `"title|severity"` over the full post-synthesize set.
  - Compute the four-conjunct gate. If a validated design-fault amendment was recorded → return `awaiting_amendment` with `proposed_amendment`. Else if completed → `done`. Else feed the per-plan `gap_fingerprint` history into `detect_non_progress`; `EARLY_STOP` → `incomplete` (stop_reason); else if `iteration == max_iterations` → `incomplete` (cap); else write the next remediation contract (nudged if `NUDGE`) and continue.
  - On any unhandled per-plan exception, catch internally and return `failed` (I8 — never propagate).
  - On `done`, return the sandbox `change_set` (via `detect_changes`) for the merge.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/phases.py tests/test_phases.py
git commit -m "feat(phases): per-plan iteration loop with four-conjunct completion gate (§3.2/§6.5)"
```

---

## Wave 6 — DAG waves & fan-out (T27)

### Task 27: `orchestrator/scheduler.py` — waves, bounded concurrency, failure isolation

**Files:**
- Create: `src/forge_mcp/orchestrator/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `forge_mcp.models` (`Plan`, `PlanSet`), `forge_mcp.config.CONCURRENCY_CAP`, `forge_mcp.sandbox` (`copy_sandbox`, `capture_manifest`), `forge_mcp.orchestrator.phases.run_plan_loop`, `asyncio`.
- Produces (§7.1/§7.2/I8/I14):
  - `ready_plans(planset: PlanSet, merged: set[str], pending: set[str]) -> list[str]` — plan ids whose **all transitive deps are merged** and which are still pending (the next wave, before the cap is applied).
  - `async run_wave(plan_ids: list[str], *, runner_factory, target_dir: Path, layout, concurrency: int) -> dict[str, PlanLoopResult]` — **lazily** creates each plan's sandbox + manifest **now** (post-merge, I14) from the current `target_dir`, then fans out with a **semaphore-bounded `asyncio.gather(*tasks, return_exceptions=True)`** (Decision D2 — never `TaskGroup`). Each plan coroutine self-contains exceptions and resolves to a per-plan terminal `PlanLoopResult` (I8); a raised exception is mapped to a `failed` result, never propagated to cancel siblings.
  - `has_cycle(planset: PlanSet) -> bool` and `add_conflict_edge(planset, loser, winner) -> bool` (returns False / keeps the existing order if it would create a cycle — §7.4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scheduler.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forge_mcp.models import Plan, PlanSet
from forge_mcp.orchestrator.scheduler import add_conflict_edge, has_cycle, ready_plans, run_wave


def _ps(*edges):
    plans = {}
    for pid, deps in edges:
        plans[pid] = Plan(id=pid, depends_on=list(deps), surface="backend",
                          file_scope=["**"], verification_command=None, body="x")
    return PlanSet(plans=list(plans.values()), run_verification_command=None)


def test_ready_plans_respects_transitive_deps():
    """Design: §7.1 a wave is ready plans whose all transitive deps are merged.
    Implementation: p2 depends on p1; ready before merge is [p1].
    Example: ready_plans == ['p1'].
    """
    ps = _ps(("p1", []), ("p2", ["p1"]))
    assert ready_plans(ps, merged=set(), pending={"p1", "p2"}) == ["p1"]
    assert ready_plans(ps, merged={"p1"}, pending={"p2"}) == ["p2"]


def test_conflict_edge_never_creates_cycle():
    """Design: §7.4 a conflict edge that would cycle is refused (keep existing order).
    Implementation: p1->p2 exists; adding p2->p1 is refused.
    Example: add_conflict_edge returns False and the graph stays acyclic.
    """
    ps = _ps(("p1", []), ("p2", ["p1"]))   # p2 depends on p1
    assert add_conflict_edge(ps, loser="p1", winner="p2") is False
    assert not has_cycle(ps)


@pytest.mark.driver
async def test_failure_isolation_one_plan_raises(monkeypatch, tmp_path: Path):
    """Design: §7.1/I8 a raising plan does not cancel siblings (gather return_exceptions).
    Implementation: patch run_plan_loop so p1 raises, p2 returns done.
    Example: p2's result is present and terminal.
    """
    from forge_mcp.orchestrator import scheduler

    async def fake_loop(*, plan, **kw):
        if plan.id == "p1":
            raise RuntimeError("boom")

        class R:
            terminal_state = "done"; iterations = 1; last_gaps = []; synthesized = []
            proposed_amendment = None; stop_reason = None; change_set = []
        return R()

    monkeypatch.setattr(scheduler, "run_plan_loop", fake_loop)
    target = tmp_path / "t"; target.mkdir()
    results = await run_wave(["p1", "p2"], runner_factory=lambda: (None, None),
                             target_dir=target, layout=None, concurrency=2)
    assert results["p2"].terminal_state == "done"
    assert results["p1"].terminal_state == "failed"
```

> The scheduler test stubs `run_plan_loop` and sandbox creation via monkeypatch so it stays a pure scheduling/isolation test. If `run_wave` creates sandboxes eagerly, guard the test plans with trivial bodies and a real `target_dir`.

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/scheduler.py`** per §7.1/§7.2. `_transitive_deps` via DFS; `has_cycle` via coloring DFS; `run_wave` wraps each plan coroutine so it catches `BaseException` except `asyncio.CancelledError` and returns a `failed` `PlanLoopResult`; the only cancel path is the run-level `wait_for` (handled in the engine, not here). Sandbox creation is lazy and post-merge (copy from the current `target_dir`).

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): DAG waves, semaphore-bounded gather, failure isolation (§7.1/I8)"
```

---

## Wave 7 — the conductor (T28)

### Task 28: `orchestrator/engine.py` — the run conductor

**Files:**
- Create: `src/forge_mcp/orchestrator/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: nearly everything — `lockfile.TargetLock`, `config.create_run_dir`/`CONCURRENCY_CAP`, `artifacts.init_run_layout`/`RunLayout`, `drivers.planner.run_planner`, `scheduler` (`ready_plans`/`run_wave`/conflict edges), `sandbox` (`detect_conflicts`/`apply_merge`/`WriterMap`/`resolve_conflict_winner`), `amend.apply_amendments`, `verifier.run_verification` (run-level post-merge), `convergence.detect_non_progress` (run-level conflict + amendment-churn), `statemachine.RunStateMachine`, `lifecycle.build_run_result`, `forge_mcp.models`.
- Produces (§3.1):
  - `class Orchestrator` with `async run(*, target_dir, design_text, design_fingerprint, max_iterations, max_runtime_minutes, claude_runner, codex_runner, when) -> RunResult` implementing the full §3.1 flow: acquire lock → create run dir → `init_run_layout` (freeze design→spec) → PLAN → **repeat WAVES** (schedule → execute → merge w/ cumulative overlap + conflict SM → amend → invalidate) until all plans terminal / runtime cap / run-level non-progress → VERIFY-RUN (post-merge run-level command) → finalize. Terminal cleanup runs in a `finally` (mark state, close drivers, release lock, **no git ops**) and re-raises `CancelledError`.
  - `RunResult.verified` is True only if every completed plan's per-plan gate passed **and** the run-level post-merge gate passed (else `verified=False` with an honest note).

- [ ] **Step 1: Write the failing tests** (single-plan happy path + a forced conflict, driven by fakes; the run-level `asyncio.wait_for` cap is exercised in the server test, Task 29)

```python
# tests/test_engine.py
from __future__ import annotations

import time
from pathlib import Path

import pytest

from forge_mcp.orchestrator.engine import Orchestrator
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured
from forge_mcp.drivers._codex import CodexEvent

WHEN = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))


@pytest.mark.driver
async def test_single_plan_run_completes_and_merges(tmp_path: Path):
    """Design: §3.1 a one-plan run plans, executes, merges, verifies, finalizes 'completed'.
    Implementation: planner returns one plan that creates a file; evaluator no_gaps.
    Example: RunResult.status == 'completed' and the file lands in target_dir.
    """
    target = tmp_path / "repo"; target.mkdir()
    planset = {"plans": [{"id": "p1", "depends_on": [], "surface": "backend",
                          "file_scope": ["**"], "verification_command": None,
                          "body": "create out.txt"}],
               "run_verification_command": None}
    # planner result, then evaluator no_gaps for the single iteration:
    claude = FakeClaudeRunner([structured(planset),
                               structured({"no_gaps": True, "summary": "ok", "gaps": []})])

    # the fake generator "implements" by writing into the sandbox via a hook:
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})],
                            on_generate=lambda sandbox: (Path(sandbox) / "out.txt").write_text("done"))

    orch = Orchestrator()
    result = await orch.run(target_dir=target, design_text="# design", design_fingerprint="fp",
                            max_iterations=2, max_runtime_minutes=600,
                            claude_runner=claude, codex_runner=codex, when=WHEN)
    assert result.status == "completed"
    assert (target / "out.txt").read_text() == "done"
    assert result.verified is False  # no run-level command declared -> honest False


@pytest.mark.driver
async def test_run_is_fresh_new_dir_each_call(tmp_path: Path):
    """Design: §6.6/I12 every call is a fresh timestamped run (no resume).
    Implementation: two runs produce two distinct run dirs.
    Example: run_dir paths differ.
    """
    target = tmp_path / "repo"; target.mkdir()
    planset = {"plans": [{"id": "p1", "depends_on": [], "surface": "backend",
                          "file_scope": ["**"], "verification_command": None, "body": "noop"}],
               "run_verification_command": None}
    def mk():
        return (FakeClaudeRunner([structured(planset),
                                  structured({"no_gaps": True, "summary": "ok", "gaps": []})]),
                FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})]))
    orch = Orchestrator()
    c1, x1 = mk(); r1 = await orch.run(target_dir=target, design_text="d", design_fingerprint="fp",
                                       max_iterations=1, max_runtime_minutes=600,
                                       claude_runner=c1, codex_runner=x1, when=WHEN)
    c2, x2 = mk(); r2 = await orch.run(target_dir=target, design_text="d", design_fingerprint="fp",
                                       max_iterations=1, max_runtime_minutes=600,
                                       claude_runner=c2, codex_runner=x2, when=WHEN)
    assert r1.run_dir != r2.run_dir
```

> The fake `FakeCodexRunner(on_generate=…)` callback lets the test inject sandbox file writes so the merge has something to apply. Add this hook to `tests/fakes.py` (extend Task 17's fake).

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/orchestrator/engine.py`** per §3.1. Thread the `when: time.struct_time` through to `create_run_dir` (no `datetime.now()` inside the engine — injected for testability). Maintain the cumulative `WriterMap`, `conflict_fingerprint.json` history, and the in-process amendment-churn history; feed both run-level histories into `detect_non_progress` and stop `incomplete` on `EARLY_STOP` (`stop_reason` "unresolvable cross-plan overlap" / "amendment thrash"). INVALIDATE plans whose `file_scope`/`surface` intersects an amended section or a merge conflict. Terminal cleanup in `finally`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/orchestrator/engine.py tests/test_engine.py tests/fakes.py
git commit -m "feat(engine): wave-structured run conductor, merge/amend/verify/finalize (§3.1)"
```

---

## Wave 8 — MCP & CLI surface (T29–T30 concurrent)

### Task 29: `server.py` — FastMCP server & `run_forge` tool

**Files:**
- Create: `src/forge_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `forge_mcp.models.RunForgeInput`/`RunResult`, `forge_mcp.orchestrator.engine.Orchestrator`, `forge_mcp.config`, `forge_mcp.check`, `mcp.server.fastmcp` (`FastMCP`, `Context`).
- Produces (§4.1/§4.2/D5):
  - `mcp = FastMCP(name="forge-mcp")` and `@mcp.tool() async def run_forge(target_dir, design_doc_path=None, design_doc_content=None, max_iterations=10, max_runtime_minutes=600, *, ctx) -> RunResult` — **spread params** (flat schema whose top-level names are the `RunForgeInput` anchors). In-body: construct `RunForgeInput(**params)` (runs the xor); resolve design text (read path or use content); compute the fingerprint; enforce the cap with `asyncio.wait_for(orchestrator.run(...), timeout=max_runtime_minutes*60)` → `TimeoutError` finalizes `incomplete`; a `CancelledError` runs terminal cleanup and re-raises. `ctx` carries advisory progress only — correctness never depends on it.
  - `serve() -> None` — runs preflight `check.run_checks` (map `FAIL` → tagged error), then `mcp.run()` (stdio default).
  - The `run_forge` schema's top-level property names match `RunForgeInput` exactly (anchor, §17).

- [ ] **Step 1: Write the failing tests** (call the tool function directly with fakes + a fake `ctx`; assert schema anchors and the timeout→incomplete path)

```python
# tests/test_server.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp import server


def test_tool_input_schema_top_level_names_are_anchors():
    """Design: §4.1/§17 spread params -> flat schema whose names are RunForgeInput fields.
    Implementation: read the FastMCP tool input schema; assert top-level keys.
    Example: target_dir, design_doc_path, max_runtime_minutes present at top level.
    """
    schema = server.run_forge_input_schema()  # helper returning the tool's inputSchema
    props = set(schema["properties"])
    assert {"target_dir", "design_doc_path", "design_doc_content",
            "max_iterations", "max_runtime_minutes"} <= props


@pytest.mark.driver
async def test_timeout_finalizes_incomplete(monkeypatch, tmp_path: Path):
    """Design: §4.1 the runtime cap (asyncio.wait_for) finalizes 'incomplete', never raises.
    Implementation: patch the orchestrator to sleep past a tiny cap.
    Example: RunResult.status == 'incomplete'.
    """
    import asyncio

    async def slow_run(**kw):
        await asyncio.sleep(5)

    monkeypatch.setattr(server.Orchestrator, "run", staticmethod(slow_run))

    class Ctx:  # advisory-only; correctness must not depend on it
        async def report_progress(self, *a, **k): ...
        async def info(self, *a, **k): ...

    res = await server._run_forge_impl(
        target_dir=str(tmp_path), design_doc_content="# d",
        max_iterations=1, max_runtime_minutes=0.001, ctx=Ctx())  # cap ~ 60ms
    assert res.status == "incomplete"
```

> Expose two small testable seams: `run_forge_input_schema()` returning the tool's input schema, and `_run_forge_impl(...)` containing the tool body (the `@mcp.tool()`-decorated `run_forge` just delegates to it). This keeps the body unit-testable without a live MCP transport.

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/server.py`** per §4.1. `_run_forge_impl` wraps `asyncio.wait_for`; on `TimeoutError` build an `incomplete` `RunResult` via `lifecycle.build_run_result` (stop_reason "runtime cap"). Set `os.umask(0o077)` for the run and restore in `finally`. The `@mcp.tool()` `run_forge` delegates to `_run_forge_impl`.

- [ ] **Step 4: Run tests to verify they pass** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/server.py tests/test_server.py
git commit -m "feat(server): FastMCP run_forge tool, flat anchor schema, runtime cap (§4.1)"
```

---

### Task 30: `cli.py` — `forge serve` / `forge check`

**Files:**
- Create: `src/forge_mcp/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `typer`, `forge_mcp.server.serve`, `forge_mcp.check.run_checks`/`any_fail`.
- Produces (§4.4):
  - `app = typer.Typer()`; `@app.command() def serve()` → `server.serve()`; `@app.command() def check(target_dir: str | None = None)` → prints each `(label, status, detail)` row and exits non-zero on any `FAIL`.
  - `main()` — `app()` entry point (the `forge` console script and `python -m forge_mcp`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from __future__ import annotations

from typer.testing import CliRunner

from forge_mcp.cli import app

runner = CliRunner()


def test_check_command_runs_and_reports(tmp_path):
    """Design: §4.4 `forge check` prints rows and sets a non-zero exit on FAIL.
    Implementation: invoke check on a writable dir; assert it runs and prints labels.
    Example: output contains a status token.
    """
    result = runner.invoke(app, ["check", "--target-dir", str(tmp_path)])
    assert result.exit_code in (0, 1)  # 1 if an environment probe FAILs (e.g. SDK absent)
    assert any(tok in result.stdout for tok in ("OK", "WARN", "FAIL"))
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement `src/forge_mcp/cli.py`** — `check` calls `run_checks`, prints rows, `raise typer.Exit(1)` on `any_fail`; `serve` delegates to `server.serve()`; `main = app`.

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/forge_mcp/cli.py tests/test_cli.py
git commit -m "feat(cli): forge serve / forge check subcommands (§4.4)"
```

---

## Wave 9 — real-CLI end-to-end (T31)

### Task 31: `tests/test_e2e_real_clis.py` — `@slow` smoke

**Files:**
- Create: `tests/test_e2e_real_clis.py`
- Create: `examples/tiny-feature.md` (a minimal design doc fixture)

**Interfaces:**
- Consumes: the whole package (`server._run_forge_impl` or `Orchestrator.run`) against the **real** `claude`/`codex` binaries.
- Produces: one end-to-end run, `@pytest.mark.slow` so it is excluded from default CI (`addopts = "-m 'not slow'"`). Asserts a `RunResult` with a terminal `status` and a populated `run_dir` under `.harness/`. This is the only test that exercises live drivers (§8.4/§17).

- [ ] **Step 1: Write the test (skipped by default)**

```python
# tests/test_e2e_real_clis.py
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge_mcp.orchestrator.engine import Orchestrator
from forge_mcp.drivers._claude import ClaudeDriver
from forge_mcp.drivers._codex import CodexDriver

pytestmark = pytest.mark.slow


@pytest.mark.skipif(not (shutil.which("claude") and shutil.which("codex")),
                    reason="real claude/codex binaries required")
async def test_tiny_feature_end_to_end(tmp_path: Path):
    """Design: §17 one live end-to-end run against real CLIs, excluded from default CI.
    Implementation: drive Orchestrator.run with real drivers over a tiny design.
    Example: returns a terminal RunResult under a .harness run dir.
    """
    import time

    target = tmp_path / "scratch-app"; target.mkdir()
    design = (Path(__file__).resolve().parents[1] / "examples" / "tiny-feature.md").read_text()
    import hashlib
    result = await Orchestrator().run(
        target_dir=target, design_text=design,
        design_fingerprint=hashlib.sha256(design.encode()).hexdigest(),
        max_iterations=2, max_runtime_minutes=20,
        claude_runner=ClaudeDriver(), codex_runner=CodexDriver(),
        when=time.localtime())
    assert result.status in {"completed", "incomplete", "failed"}
    assert ".harness" in result.run_dir
```

- [ ] **Step 2: Verify it is collected but skipped by default**

Run: `uv run pytest tests/test_e2e_real_clis.py -q` → `1 skipped` (or `deselected`) under the default `-m 'not slow'`.
Run (opt-in): `uv run pytest -m slow tests/test_e2e_real_clis.py -q` → runs if binaries present.

- [ ] **Step 3: Author `examples/tiny-feature.md`** — a minimal, single-plan design (e.g. "create `out.txt` containing the line `hello forge`; verification: `test -f out.txt`").

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_real_clis.py examples/tiny-feature.md
git commit -m "test(e2e): @slow real-CLI smoke run, excluded from default CI (§17)"
```

---

## Final Integration Gate

After all 31 tasks merge, run the full CI gate once and confirm green:

- [ ] `uv run ruff check src tests scripts` → clean
- [ ] `uv run ruff format --check src tests scripts` → clean
- [ ] `uv run pyright` → no errors
- [ ] `uv run python scripts/check_docstrings.py src tests scripts` → no defects (Rule-21)
- [ ] `uv run pytest` → all pass (slow deselected)
- [ ] `uv run pytest tests/test_sdk_contract.py -q` → passes against the installed SDKs (not slow-gated, §8.4)
- [ ] Spot-check the §18 invariants I1–I14 against the merged code (single-writer files, no `TaskGroup`, no `git commit/push/...`, no `resume`, lazy SDK imports, fresh run dir per call).

---

## Self-Review (plan author's checklist, run against the spec)

**Spec coverage.** Every §maps to a task: §3.1 run flow → T28; §3.2 per-plan loop → T26; §3.3 state → T20/T21; §4.1–4.4 surface → T29/T30/T25; §5.1–5.3 stages → T17/T18/T19 (+ triage T12, amend T24); §6.1–6.4 N1–N4 → T26/T28/T23; §6.5 verify gate → T7/T26/T28; §6.6 no resume → T28 (fresh-run test); §6.7 non-progress → T4 (+ run-level in T28); §7.1 scheduling/I8 → T27; §7.2/7.3 sandbox/manifest → T8; §7.4 merge/conflict → T13; §7.5 git-deny relationship → T5/T14; §8.1 Claude seam → T14; §8.2 Codex seam → T15; §8.3 Protocols → T14/T15; §8.4 drift discipline → T14/T15 Step 0 + `test_sdk_contract`; §9 git-deny → T5/T14; §10 skills/check → T22/T25/T9; §11 artifacts → T11; §12 ids/lock → T3/T16; §13 durable writes → T6; §14 schemas → T2; §15 modules → all; §16 conventions → T1 (`check_docstrings`, ruff/pyright); §17 testing strategy → every task's TDD + T31; §18 invariants → Final Integration Gate spot-check.

**Placeholder scan.** No "TBD"/"handle edge cases"/"similar to Task N" — each task carries real test code and concrete implementation guidance grounded in the spec's verified sketches (reproduced for the load-bearing seams: build_options, verifier, writers, the git-deny hook).

**Type consistency.** `PlanLoopResult` (T26) is consumed unchanged by T27/T28; `Change`/`Manifest`/`Entry` (T8) by T13/T26; `RunResult`/`GapSummary`/`RunForgeInput` anchors (T2) by T28/T29; `VerifyOutcome` (T7) by T26; `StructuredResult`/`CodexEvent` (T14/T15) by the stages and fakes (T17). The `RunLayout` (T11) path API is the single source consumed by T20/T21/T24/T26/T28.

**No future / out-of-scope work.** Every non-goal in §2 (cost accounting, browser verification, cross-run resume, MCP resource/subscription/task surface, cross-run learning, extra filesystem metadata) is **absent** from the task list by construction — nothing is deferred; all in-scope work is covered by T1–T31.
