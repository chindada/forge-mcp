# Single-Plan, Direct-Edit Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the forge-mcp harness from a multi-plan concurrent DAG (copy-sandbox + merge engine) into a single-plan loop whose Generator edits `target_dir` directly — autonomously, with internet access — exactly as specified in `docs/specs/0003-single-plan-direct-edit-harness.md`.

**Architecture:** One run plans exactly one `Plan`, then iterates a single direct-edit loop (`run_plan_loop` on `target_dir`: Codex `workspace-write` generate → optional verify → fresh-Claude evaluate → triage → in-loop spec amendment) until the plan is `done` or stops honestly, then finalizes an honest `RunResult`. The entire DAG / wave / scheduler, copy-sandbox, manifest-diff, merge engine, cross-plan conflict resolution, and the git-state backstop are **deleted**.

**Tech Stack:** Python 3.12, Pydantic v2, `claude-agent-sdk == 0.2.108`, `openai_codex == 0.1.0b2`, pytest, ruff, pyright, `uv` for tooling.

## Global Constraints

These apply to **every** task (copied verbatim from `docs/specs/0003`):

- **Single-plan premise.** Exactly one `Plan` per run; the Generator edits `target_dir` in place. No concurrency, no DAG, no copy-isolation, no merge, no resume.
- **Git-state does not gate completion** ("if the evaluator can pass, it passes"). No stage commits; the Generator leaves edits uncommitted for the human's own git.
- **Codex runtime:** `sandbox=Sandbox.workspace_write`, `approval_mode=ApprovalMode.deny_all` (the never-ask policy — there is **no** `ApprovalMode.never` member), inline `config={"sandbox_workspace_write": {"network_access": True}}`, `cwd` None-guard preserved.
- **Claude stages unchanged in posture:** `permission_mode="bypassPermissions"` + `git_deny_hooks()`. Not made filesystem-read-only (explicit non-goal).
- **Pinned SDK versions:** `claude-agent-sdk == 0.2.108`, `openai_codex == 0.1.0b2`.
- **Three-section docstrings (Rule 21):** every non-trivial `def`/method carries a docstring containing `Design:`, `Implementation:`, and `Example:`, each with ≥ 5 non-whitespace chars — enforced by `scripts/check_docstrings.py`. (Pydantic models / trivial stubs are exempt.)
- **Success gate (`scripts/ci.sh`):** `ruff check` + `ruff format --check` + `pyright` + `scripts/check_docstrings.py` + the full `pytest` suite must be green.
- **No out-of-scope work.** Every change traces to the single-plan premise, the direct-edit Generator, or the autonomous-networked Generator. The broader generator-prompt overhaul and any `disallowed_tools` hardening are excluded.

## File Structure

- **Modify (production):** `models.py`, `drivers/_codex.py`, `drivers/generator.py`, `drivers/evaluator.py`, `drivers/planner.py`, `orchestrator/amend.py`, `orchestrator/plan_state.py`, `artifacts.py`, `orchestrator/statemachine.py`, `orchestrator/lifecycle.py`, `orchestrator/phases.py`, `orchestrator/engine.py`, `gitguard.py`, `config.py`, `prompts/planner_system.md`, `prompts/generator_system.md`.
- **Delete:** `src/forge_mcp/orchestrator/scheduler.py`, `src/forge_mcp/sandbox.py`, `tests/test_scheduler.py`, `tests/test_sandbox_merge.py`, `tests/test_sandbox_manifest.py`.
- **Tests touched:** `test_models`, `test_codex_seam`, `test_sdk_contract`, `test_generator`, `test_evaluator`, `test_planner`, `test_amend`, `test_plan_state`, `test_artifacts`, `test_statemachine`, `test_lifecycle`, `test_phases`, `test_engine`, `test_gitguard`, `test_config`, `test_prompts`, `test_e2e_real_clis`.

## Task ordering & the transient-red window (read this first)

Tasks are ordered per **spec §11**: rewrite the modules that import the doomed symbols **first**, then delete `scheduler.py`/`sandbox.py`, then run the full gate. Because this is a tightly-coupled refactor, **each task's Step-4 runs only that task's own test file** (which passes after the task). The **full `pytest` suite and `pyright` go green only at the final task (Task 18)** — a transient red window across the import graph is *inherent* to the refactor and is exactly what spec §11 step (4) accounts for. Do **not** try to make the whole suite green before the engine/phases/deletion tasks land. Commit after every task.

---

### Task 1: Collapse Plan to {surface, verification_command, body} and delete PlanSet

**Files:**
- Modify: `src/forge_mcp/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: none — models.py is leaf; Pydantic BaseModel only
- Produces: class Plan(BaseModel, extra='forbid') with fields surface: Literal['backend','frontend']; verification_command: str | None = None; body: str. PlanSet REMOVED (no longer importable from forge_mcp.models). Unchanged exports kept: RunForgeInput, GapSummary, RunResult, EvalGap, EvalResult, ProposedAmendment, GapTriage, TriageResult.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_models.py`:

```python
# tests/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

import forge_mcp.models as models
from forge_mcp.models import (
    EvalGap,
    GapSummary,
    GapTriage,
    Plan,
    RunForgeInput,
    RunResult,
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
        RunForgeInput(target_dir="/r", design_doc_path="/r/d.md", foo=1)  # type: ignore[call-arg]


def test_run_result_anchor_fields():
    """Design: §4.3/§17 RunResult field names are schema anchors.
    Implementation: construct and read each anchor field.
    Example: RunResult(status='incomplete', ...).
    """
    r = RunResult(
        status="incomplete",
        run_dir="/r/.harness/x",
        iterations=3,
        unresolved_gaps=[GapSummary(title="t", severity="high", design_doc_section="§7.4")],
        stop_reason="non-progress",
        verified=False,
        summary="1/2",
    )
    assert r.unresolved_gaps[0].design_doc_section == "§7.4"


def test_eval_gap_title_canonicalized():
    """Design: §5.3/§14 EvalGap.title whitespace-canonicalized for triage joins.
    Implementation: collapse internal runs and strip.
    Example: '  a   b ' -> 'a b'.
    """
    assert (
        EvalGap(
            title="  missing   delete ",
            severity="high",
            design_doc_section="§7.4",
            current_state="x",
            expected_state="y",
            suggested_fix="z",
        ).title
        == "missing delete"
    )


def test_gap_triage_design_fault_requires_kind_and_citation():
    """Design: §14 design_fault ⇒ fault_kind set ∧ non-empty cited_sections.
    Implementation: violating combo raises; non-fault coerces fault_kind to None.
    Example: GapTriage(design_fault=True, fault_kind=None, ...) -> ValidationError.
    """
    with pytest.raises(ValidationError):
        GapTriage(
            gap_title="g",
            design_fault=True,
            fault_kind=None,
            cited_sections=["§7"],
            explanation="e",
        )
    with pytest.raises(ValidationError):
        GapTriage(
            gap_title="g",
            design_fault=True,
            fault_kind="contradiction",
            cited_sections=[],
            explanation="e",
        )
    t = GapTriage(
        gap_title="g", design_fault=False, fault_kind="other", cited_sections=[], explanation="e"
    )
    assert t.fault_kind is None


def test_gap_triage_non_fault_omitting_fault_kind_defaults_none():
    """Design: §14 a non-fault triage need not supply fault_kind.
    Implementation: omit fault_kind entirely; validator leaves/sets it None.
    Example: GapTriage(gap_title='g', design_fault=False, explanation='e').fault_kind is None.
    """
    t = GapTriage(gap_title="g", design_fault=False, cited_sections=[], explanation="e")
    assert t.fault_kind is None


def test_run_result_forbids_extra():
    """Design: §4.3 extra='forbid' anchor RunResult rejects unknown fields.
    Implementation: an unknown field raises ValidationError.
    Example: RunResult(..., foo=1) -> ValidationError.
    """
    with pytest.raises(ValidationError):
        RunResult(
            status="completed",
            run_dir="/r",
            iterations=1,
            verified=True,
            summary="ok",
            foo=1,  # type: ignore[call-arg]
        )


def test_gap_summary_forbids_extra():
    """Design: §4.3 extra='forbid' anchor GapSummary rejects unknown fields.
    Implementation: an unknown field raises ValidationError.
    Example: GapSummary(..., foo=1) -> ValidationError.
    """
    with pytest.raises(ValidationError):
        GapSummary(title="t", severity="high", design_doc_section="§7.4", foo=1)  # type: ignore[call-arg]


def test_plan_collapsed_to_three_fields():
    """Design: §3/§15 Plan collapses to {surface, verification_command, body}.
    Implementation: construct with only the three surviving fields; read each back;
        verification_command defaults to None when omitted.
    Example: Plan(surface='backend', body='# contract').verification_command is None.
    """
    p = Plan(surface="backend", verification_command="pytest -q", body="# contract")
    assert p.surface == "backend"
    assert p.verification_command == "pytest -q"
    assert p.body == "# contract"
    p2 = Plan(surface="frontend", body="# c")
    assert p2.verification_command is None


def test_plan_drops_id_depends_on_file_scope():
    """Design: §3 the DAG fields id/depends_on/file_scope are removed.
    Implementation: extra='forbid' rejects each dropped field; the model also has no
        such attributes in its field set.
    Example: Plan(surface='backend', body='b', id='x') -> ValidationError.
    """
    assert set(Plan.model_fields) == {"surface", "verification_command", "body"}
    for stray in ({"id": "x"}, {"depends_on": []}, {"file_scope": []}):
        with pytest.raises(ValidationError):
            Plan(surface="backend", body="b", **stray)  # type: ignore[arg-type]


def test_plan_surface_is_closed_literal():
    """Design: §3/§15 surface is a closed Literal['backend','frontend'].
    Implementation: an out-of-set surface value raises ValidationError.
    Example: Plan(surface='mobile', body='b') -> ValidationError.
    """
    with pytest.raises(ValidationError):
        Plan(surface="mobile", body="b")  # type: ignore[arg-type]


def test_plan_json_schema_has_no_planset_keys():
    """Design: §3 Plan.model_json_schema() is the new Planner output contract.
    Implementation: the schema's properties are exactly the three surviving fields.
    Example: set(Plan.model_json_schema()['properties']) == {'surface','verification_command','body'}.
    """
    props = set(Plan.model_json_schema()["properties"])
    assert props == {"surface", "verification_command", "body"}


def test_planset_is_deleted():
    """Design: §3 PlanSet is deleted entirely (one plan, no collection).
    Implementation: the symbol must no longer be importable from forge_mcp.models.
    Example: hasattr(forge_mcp.models, 'PlanSet') is False.
    """
    assert not hasattr(models, "PlanSet")
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

In src/forge_mcp/models.py, the surgical model-section changes are:
1. Plan class (current lines 77-85): DELETE fields `id: str` (line 80), `depends_on: list[str] = []` (line 81), `file_scope: list[str] = []` (line 83). KEEP `surface`, `verification_command`, `body`. Replace the one-line docstring (line 78) with the three-section §15 docstring (Pydantic models are docstring-exempt, but the §15 sketch supplies one — keep it).
2. PlanSet class (current lines 88-92): DELETE the entire class including its `plans: list[Plan]` and `run_verification_command: str | None = None` fields and the comment-free block between Plan and EvalGap.
No imports change (Literal, BaseModel, field_validator, model_validator all still used by surviving models). NOTE: downstream importers planner.py:9, engine.py:30, scheduler.py:23 still import PlanSet today — they are rewritten/deleted by their own tasks (spec §11 sequencing); this models.py task only removes the definition. The full replacement file content is given in new_code.

Apply:

```python
"""Pydantic models and schema anchors for forge-mcp.

Public schema anchors (RunForgeInput, RunResult, GapSummary) use extra='forbid'
so the MCP tool schema stays tight. Internal models follow the same convention
for consistency.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Public schema anchors — field names must not be renamed
# ---------------------------------------------------------------------------


class RunForgeInput(BaseModel, extra="forbid"):
    """MCP tool input: drives a single forge run."""

    target_dir: str
    design_doc_path: str | None = None
    design_doc_content: str | None = None
    max_iterations: int = 10
    max_runtime_minutes: int = 600

    @model_validator(mode="after")
    def _exactly_one_design_doc(self) -> RunForgeInput:
        """Enforce XOR of design_doc_path / design_doc_content.

        Design: §4.3 the caller must supply exactly one design document
            source so the orchestrator always has a canonical starting
            point without ambiguity.
        Implementation: evaluate bool(path) XOR bool(content); raise
            ValueError for both-set or neither-set.
        Example: RunForgeInput(target_dir='/r', design_doc_path='/d.md') ok;
            RunForgeInput(target_dir='/r') raises ValueError.
        """
        has_path = bool(self.design_doc_path)
        has_content = bool(self.design_doc_content)
        if has_path and has_content:
            raise ValueError(
                "Provide exactly one of design_doc_path or design_doc_content, not both."
            )
        if not has_path and not has_content:
            raise ValueError("Exactly one of design_doc_path or design_doc_content is required.")
        return self


class GapSummary(BaseModel, extra="forbid"):
    """Summarised unresolved gap surfaced in RunResult."""

    title: str
    severity: str
    design_doc_section: str


class RunResult(BaseModel, extra="forbid"):
    """Result returned by the run_forge MCP tool."""

    status: Literal["completed", "incomplete", "failed"]
    run_dir: str
    iterations: int
    unresolved_gaps: list[GapSummary] = []
    failure_kind: str | None = None
    stop_reason: str | None = None
    verified: bool
    summary: str


# ---------------------------------------------------------------------------
# Internal models — schema anchors not required but names follow the brief
# ---------------------------------------------------------------------------


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


class EvalGap(BaseModel, extra="forbid"):
    """A gap between current state and design spec, as found by the Evaluator."""

    title: str
    severity: str
    design_doc_section: str
    current_state: str
    expected_state: str
    suggested_fix: str

    @field_validator("title")
    @classmethod
    def _canonicalize_title(cls, v: str) -> str:
        """Collapse all whitespace runs in title to single spaces.

        Design: §5.3/§14 gap titles are used as join keys between the
            Evaluator and the Triage stage, so they must be in a canonical
            form regardless of how the LLM formatted them.
        Implementation: split on any whitespace then rejoin with a single
            space, which strips leading/trailing and collapses internal runs.
        Example: '  missing   delete ' becomes 'missing delete'.
        """
        return " ".join(v.split())


class EvalResult(BaseModel, extra="forbid"):
    """Full output from one Evaluator turn."""

    no_gaps: bool
    gaps: list[EvalGap] = []
    summary: str


class ProposedAmendment(BaseModel, extra="forbid"):
    """A concrete edit to the design document that resolves a triage gap."""

    cited_sections: list[str]
    before: str
    after: str
    rationale: str


class GapTriage(BaseModel, extra="forbid"):
    """Triage verdict for a single EvalGap."""

    gap_title: str
    design_fault: bool
    fault_kind: (
        Literal["contradiction", "infeasibility", "deprecated_dependency", "ambiguity", "other"]
        | None
    ) = None
    cited_sections: list[str] = []
    explanation: str
    proposed_amendment: ProposedAmendment | None = None

    @model_validator(mode="after")
    def _validate_design_fault_fields(self) -> GapTriage:
        """Coerce fault_kind to None when not a design fault; enforce required fields when it is.

        Design: §14 a non-design-fault triage must not carry a fault_kind
            (it is meaningless); a design-fault triage must have both a
            fault_kind and at least one cited section so reviewers can
            locate the flaw.
        Implementation: coerce the non-fault case first (fault_kind → None),
            then validate the fault case (fault_kind set and cited_sections
            non-empty); order matters so a stray fault_kind is stripped
            before the fault branch checks it.
        Example: GapTriage(design_fault=False, fault_kind='other', ...).fault_kind is None.
        """
        if not self.design_fault:
            self.fault_kind = None
            return self
        if self.fault_kind is None or not self.cited_sections:
            raise ValueError(
                "When design_fault is True, fault_kind must be set and "
                "cited_sections must be non-empty."
            )
        return self


class TriageResult(BaseModel, extra="forbid"):
    """Collection of triage verdicts for all gaps in one Evaluator turn."""

    triages: list[GapTriage] = []
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/models.py' 'tests/test_models.py'
git commit -m "refactor(models): collapse Plan to {surface,verification_command,body}; delete PlanSet"
```

---

### Task 2: Switch Codex thread to workspace_write + network config (§5)

**Files:**
- Modify: `src/forge_mcp/drivers/_codex.py`
- Test: `tests/test_codex_seam.py`
- Test: `tests/test_sdk_contract.py`

**Interfaces:**
- Consumes: openai_codex: Sandbox.workspace_write, ApprovalMode.deny_all, AsyncCodex, TextInput; CodexConfig.cwd (str|None)
- Produces: CodexDriver._generate_impl unchanged signature: async generator over CodexEvent; thread now started with sandbox=Sandbox.workspace_write, approval_mode=ApprovalMode.deny_all, config={'sandbox_workspace_write': {'network_access': True}}, cwd None-guard preserved

- [ ] **Step 1: Write the failing test**

Write to `tests/test_codex_seam.py`, `tests/test_sdk_contract.py`:

```python
from __future__ import annotations

import asyncio
import importlib.util
import types
from collections import deque
from pathlib import Path

import pytest

from forge_mcp.drivers import _codex

codex_present = importlib.util.find_spec("openai_codex") is not None


def _fake_codex_with_deque(maxlen: int = 400) -> types.SimpleNamespace:
    """Build a fake Codex object exposing _client._sync._stderr_lines as a deque.

    Design: §8.2 the stderr-tee tests need the SDK's private 3-object attribute
        chain without importing the real SDK; SimpleNamespace mirrors it.
    Implementation: nest SimpleNamespaces holding a bounded deque at the leaf.
    Example: _fake_codex_with_deque()._client._sync._stderr_lines.maxlen == 400.
    """
    sync = types.SimpleNamespace(_stderr_lines=deque(maxlen=maxlen))
    return types.SimpleNamespace(_client=types.SimpleNamespace(_sync=sync))


class _RecordingThread:
    """Fake Codex thread recording the turn() call and yielding no notifications.

    Design: §5 the seam test must observe the sandbox/approval/config args the
        driver passes to thread_start and turn() without the real SDK; this
        double records them and returns an empty stream so _generate_impl runs
        to completion.
    Implementation: turn() stores its kwargs and returns a handle whose stream()
        is an empty async generator; id is a constant.
    Example: thread = _RecordingThread(); await thread.turn(...) sets turn_kwargs.
    """

    def __init__(self) -> None:
        """Initialise with a stable id and empty recorded-kwargs slots.

        Design: §5 the recorder must expose .id (read into last_thread_id) and
            start with no recorded turn kwargs.
        Implementation: set id to a constant and turn_kwargs to None.
        Example: _RecordingThread().id == 'fake-thread'.
        """
        self.id = "fake-thread"
        self.turn_kwargs: dict | None = None

    async def turn(self, _text: object, **kwargs: object) -> object:
        """Record the turn kwargs and return a handle over an empty stream.

        Design: §5 the test asserts approval_mode on the turn; record it.
        Implementation: store kwargs; return a handle whose stream() yields nothing.
        Example: await thread.turn(TextInput(...), approval_mode=x) records x.
        """
        self.turn_kwargs = kwargs

        async def _empty():
            """Yield no notifications.

            Design: §5 an empty turn stream lets _generate_impl finish cleanly.
            Implementation: an async generator with a guarded yield never reached.
            Example: ``async for _ in _empty(): ...`` iterates zero times.
            """
            if False:
                yield None

        return types.SimpleNamespace(stream=_empty)


class _RecordingCodex:
    """Fake AsyncCodex recording thread_start kwargs (§5 seam probe).

    Design: §5 the test must capture the sandbox/approval/cwd/config the driver
        passes to thread_start; this async-context double records them.
    Implementation: __aenter__ returns self; thread_start stores kwargs and
        returns a _RecordingThread; close is a no-op.
    Example: codex = _RecordingCodex(); the driver records start_kwargs on it.
    """

    def __init__(self, *, config: object) -> None:
        """Store the launch config and init empty recorded slots.

        Design: §5 AsyncCodex is constructed with config=cfg; mirror that arg.
        Implementation: keep config; set start_kwargs/thread to None.
        Example: _RecordingCodex(config=cfg).config is cfg.
        """
        self.config = config
        self.start_kwargs: dict | None = None
        self.thread = _RecordingThread()

    async def __aenter__(self) -> "_RecordingCodex":
        """Enter the async context returning self.

        Design: §5 the driver does ``async with AsyncCodex(...) as ctx``.
        Implementation: return self.
        Example: ``async with _RecordingCodex(config=c) as ctx: ...``.
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Exit the async context as a no-op.

        Design: §5 nothing to release in the double.
        Implementation: return None.
        Example: context exit is a no-op.
        """
        return None

    async def thread_start(self, **kwargs: object) -> _RecordingThread:
        """Record thread_start kwargs and return the recording thread.

        Design: §5 the test asserts sandbox/approval_mode/config/cwd here.
        Implementation: store kwargs; return the pre-built _RecordingThread.
        Example: await codex.thread_start(sandbox=s) records s.
        """
        self.start_kwargs = kwargs
        return self.thread

    async def close(self) -> None:
        """Close as a no-op.

        Design: §5 aclose() delegates here; the double has nothing to free.
        Implementation: return None.
        Example: await codex.close() completes immediately.
        """
        return None


def test_stderr_tee_installed_and_tees_to_run_log(tmp_path: Path):
    """Design: §8.2 the tee swaps the SDK's bounded deque for a teeing deque that
        mirrors each appended line into run.log while preserving maxlen.
    Implementation: install over a fake deque(maxlen=400), append a line, assert it
        reached run.log and stayed in the buffer.
    Example: appending 'boom' writes 'boom' to run.log and keeps it buffered.
    """
    fake = _fake_codex_with_deque(maxlen=400)
    driver = _codex.CodexDriver()
    log = tmp_path / "run.log"
    driver._try_install_stderr_tee(fake, log)

    swapped = fake._client._sync._stderr_lines
    assert isinstance(swapped, _codex._StderrTeeDeque)
    assert swapped.maxlen == 400  # SDK's bounded behaviour preserved
    swapped.append("boom")
    assert driver._stderr_tee is not None
    driver._stderr_tee.close()
    assert "boom" in log.read_text()
    assert "boom" in swapped  # still buffered in memory


def test_stderr_tee_failsoft_when_chain_missing(tmp_path: Path):
    """Design: §8.2 a missing/wrong attribute chain degrades to no-tee, never raises.
    Implementation: install over a bare object lacking the deque chain; expect a
        warning and no installed tee.
    Example: _try_install_stderr_tee(object(), run.log) warns and installs nothing.
    """
    driver = _codex.CodexDriver()
    with pytest.warns(UserWarning):
        driver._try_install_stderr_tee(object(), tmp_path / "run.log")
    assert driver._stderr_tee is None


def test_stderr_tee_noop_without_run_log():
    """Design: §8.2 with no run_log_path there is no sink, so the deque is untouched.
    Implementation: install with run_log_path=None and assert the original deque
        object is left in place and no tee is recorded.
    Example: _try_install_stderr_tee(fake, None) leaves the SDK deque as-is.
    """
    fake = _fake_codex_with_deque()
    original = fake._client._sync._stderr_lines
    driver = _codex.CodexDriver()
    driver._try_install_stderr_tee(fake, None)
    assert fake._client._sync._stderr_lines is original
    assert driver._stderr_tee is None


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


def test_is_transient_classification():
    """Design: §8.2 transient errors are retryable; timeout/cancel never are.
    Implementation: builtin cases classify without the SDK installed.
    Example: is_transient(ConnectionError()) is True; TimeoutError() is False.
    """
    assert _codex.is_transient(ConnectionError()) is True
    assert _codex.is_transient(BrokenPipeError()) is True
    assert _codex.is_transient(TimeoutError()) is False
    assert _codex.is_transient(asyncio.CancelledError()) is False
    assert _codex.is_transient(OSError()) is False


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_thread_start_uses_workspace_write_and_network_config(monkeypatch, tmp_path: Path):
    """Design: §5 the driver must start the Codex thread under workspace_write (NOT
        full_access) with approval_mode=deny_all, the cwd None-guard, and the
        inline network config enabling internet for workspace-write.
    Implementation: monkeypatch AsyncCodex with a recording double, drive
        _generate_impl to exhaustion, then assert the recorded thread_start and
        turn kwargs carry Sandbox.workspace_write, ApprovalMode.deny_all,
        config={'sandbox_workspace_write': {'network_access': True}}, and cwd=str(cwd).
    Example: after the empty turn, start_kwargs['sandbox'] is Sandbox.workspace_write.
    """
    import openai_codex
    from openai_codex import ApprovalMode, Sandbox

    cfg = _codex.build_codex_config(codex_bin="codex", cwd=tmp_path)
    captured: dict = {}

    def _factory(*, config):
        """Build and record the recording codex double.

        Design: §5 capture the constructed double so the test can read its
            recorded start/turn kwargs after the driver runs.
        Implementation: construct _RecordingCodex(config=config), store it.
        Example: _factory(config=cfg) returns a _RecordingCodex.
        """
        codex = _RecordingCodex(config=config)
        captured["codex"] = codex
        return codex

    monkeypatch.setattr(openai_codex, "AsyncCodex", _factory)

    driver = _codex.CodexDriver()

    async def _drive() -> None:
        """Consume the generator to exhaustion.

        Design: §5 running the turn populates the recorded kwargs.
        Implementation: async-for over _generate_impl, discarding events.
        Example: ``await _drive()`` leaves captured['codex'] populated.
        """
        async for _ in driver._generate_impl(instructions="hi", config=cfg):
            pass

    asyncio.run(_drive())

    codex = captured["codex"]
    start = codex.start_kwargs
    assert start is not None
    assert start["sandbox"] is Sandbox.workspace_write
    assert start["sandbox"] is not Sandbox.full_access
    assert start["approval_mode"] is ApprovalMode.deny_all
    assert start["config"] == {"sandbox_workspace_write": {"network_access": True}}
    assert start["cwd"] == str(tmp_path)
    assert codex.thread.turn_kwargs is not None
    assert codex.thread.turn_kwargs["approval_mode"] is ApprovalMode.deny_all
    assert codex.thread.turn_kwargs["cwd"] == str(tmp_path)
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_codex_seam.py tests/test_sdk_contract.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

No symbols deleted. Three surgical replacements only: (a) the thread_start call block at _codex.py:383-387 — `sandbox=Sandbox.full_access` → `sandbox=Sandbox.workspace_write` and add the line `config={"sandbox_workspace_write": {"network_access": True}},` after the cwd None-guard line (keep `approval_mode=ApprovalMode.deny_all` and the exact cwd None-guard); (b) the CodexDriver class docstring phrase 'full_access sandbox' → 'workspace_write sandbox' (+ network note); (c) the _generate_impl docstring phrase 'thread_start with full_access/deny_all' → 'thread_start with workspace_write/deny_all + network config'. Do NOT touch the turn() block beyond what already exists. Verify via `grep -n full_access src/forge_mcp/drivers/_codex.py` — after the edit it must return zero hits in this file.

ALSO in tests/test_sdk_contract.py::test_codex_symbols_exist (currently :29-42): add a workspace_write symbol probe and flip the full_access assertion to the new posture. Replace the body lines `assert hasattr(Sandbox, "full_access")` with `assert hasattr(Sandbox, "workspace_write")` and ADD `assert hasattr(Sandbox, "full_access")  # still a member; driver no longer uses it` immediately after, keeping `assert hasattr(ApprovalMode, "deny_all")` and the thread_start params assertion unchanged. (Per spec §5 these are symbol-existence probes; the behavioral pin lives in test_codex_seam.py's new test_thread_start_uses_workspace_write_and_network_config.)

Apply:

```python
BEFORE (src/forge_mcp/drivers/_codex.py:383-393):

                thread = await codex_ctx.thread_start(
                    sandbox=Sandbox.full_access,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(config.cwd) if config.cwd is not None else None,
                )
                self._last_thread_id = thread.id
                turn_handle = await thread.turn(
                    TextInput(text=instructions),
                    cwd=str(config.cwd) if config.cwd is not None else None,
                    approval_mode=ApprovalMode.deny_all,
                )

AFTER:

                thread = await codex_ctx.thread_start(
                    sandbox=Sandbox.workspace_write,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(config.cwd) if config.cwd is not None else None,
                    config={"sandbox_workspace_write": {"network_access": True}},
                )
                self._last_thread_id = thread.id
                turn_handle = await thread.turn(
                    TextInput(text=instructions),
                    cwd=str(config.cwd) if config.cwd is not None else None,
                    approval_mode=ApprovalMode.deny_all,
                )

NOTE — also update the docstring prose so check_docstrings/CI stays honest and accurate, two surgical edits (no behavior change):

1) CodexDriver class docstring (currently :251-264, Design line): replace
   "starts a thread with full_access sandbox and deny_all\n        approval mode; streams turn notifications as CodexEvents."
   with
   "starts a thread with workspace_write sandbox and deny_all\n        approval mode (network access on via the sandbox_workspace_write\n        config override); streams turn notifications as CodexEvents."

2) _generate_impl docstring (currently :363-373, Implementation line): replace
   "thread_start with full_access/deny_all; turn"
   with
   "thread_start with workspace_write/deny_all + network config; turn"

All other lines of the file are unchanged. The lazy import line
`from openai_codex import ApprovalMode, AsyncCodex, Sandbox, TextInput` at :375 is kept as-is (Sandbox is still used; only the member changes from full_access to workspace_write).
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_codex_seam.py tests/test_sdk_contract.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/drivers/_codex.py' 'tests/test_codex_seam.py' 'tests/test_sdk_contract.py'
git commit -m "refactor(codex): bound generator to workspace_write with network access\n\nPer spec 0003 \u00a75: switch the Codex thread start from Sandbox.full_access to\nSandbox.workspace_write and enable internet via the inline\nconfig={'sandbox_workspace_write': {'network_access': True}} override. Keep\nApprovalMode.deny_all (the never-ask policy) and the cwd None-guard. Pin the\nnew posture behaviorally in test_codex_seam.py and add a workspace_write\nsymbol probe to test_sdk_contract.py.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: run_generator: sandbox→target_dir, root Codex at the repo (§5)

**Files:**
- Modify: `src/forge_mcp/drivers/generator.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: forge_mcp.config.codex_bin() -> Path; forge_mcp.drivers._codex.build_codex_config(*, codex_bin: str, cwd: Path, env=None) -> CodexConfig; CodexRunner.generate(*, instructions, config, run_log_path); load_prompt('generator_system')
- Produces: async def run_generator(runner, *, contract_text: str, target_dir: Path, surface: str, run_log_path: Path | None = None) -> list[CodexEvent]  (param renamed sandbox→target_dir)

- [ ] **Step 1: Write the failing test**

Write to `tests/test_generator.py`:

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
    """Design: §5 the Generator runs one autonomous Codex turn editing target_dir.
    Implementation: a fake runner yields two events; run_generator collects them.
    Example: returns both events in order.
    """
    target_dir = tmp_path / "repo"
    target_dir.mkdir()
    runner = FakeCodexRunner(
        [
            CodexEvent(kind="item.started", payload={}),
            CodexEvent(kind="turn.completed", payload={"ok": True}),
        ]
    )
    events = await run_generator(
        runner, contract_text="do X", target_dir=target_dir, surface="backend"
    )
    assert [e.kind for e in events] == ["item.started", "turn.completed"]


@pytest.mark.driver
async def test_generator_roots_codex_at_target_dir(tmp_path: Path):
    """Design: §5 build_codex_config(cwd=target_dir) roots Codex at the repo so the
        direct edit is bounded to target_dir; the cwd reaching the runner must be it.
    Implementation: capture config.cwd via the FakeCodexRunner on_generate hook,
        which is called with str(config.cwd) before the first event is yielded.
    Example: on_generate sees str(target_dir).
    """
    target_dir = tmp_path / "repo"
    target_dir.mkdir()
    seen: list[str] = []
    runner = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=seen.append,
    )
    await run_generator(
        runner, contract_text="do X", target_dir=target_dir, surface="backend"
    )
    assert seen == [str(target_dir)]
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_generator.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Rename the parameter `sandbox: Path` → `target_dir: Path` in run_generator's signature (was generator.py:23). Update the sole internal use `cwd=sandbox` → `cwd=target_dir` in the build_codex_config call (was generator.py:43). Rewrite the module docstring (generator.py:1) and the run_generator docstring (Design/Implementation/Example) to the direct-edit, target_dir framing. No imports change: codex_bin, build_codex_config, CodexEvent, load_prompt all still used. The `# noqa: F401` on the CodexEvent lazy import is preserved (CodexEvent is used as the list annotation; the noqa matches the existing file). Verify via `grep -n sandbox src/forge_mcp/drivers/generator.py` — zero hits after the edit. Call-site update in phases.py (run_generator(... target_dir=...)) is owned by the phases-rewrite task, not this one.

Apply:

```python
"""Generator stage (§5): run one autonomous Codex turn that edits target_dir directly."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge_mcp.drivers._codex import CodexEvent, CodexRunner

# Surface → capability preface (no "Skill tool" wording per §10.1)
_SURFACE_PREFACE: dict[str, str] = {
    "backend": "Apply plan-execution capabilities to implement the contract below.",
    "frontend": "Apply frontend-design capabilities to implement the contract below.",
}
_DEFAULT_PREFACE = "Implement the contract below."


async def run_generator(
    runner: CodexRunner,
    *,
    contract_text: str,
    target_dir: Path,
    surface: str,
    run_log_path: Path | None = None,
) -> list[CodexEvent]:
    """Run one autonomous Codex turn that edits *target_dir* directly (§5.2 superseded).

    Design: §5 the Generator edits the repository in place under workspace-write
        with network access and no human approval, bounded to *target_dir* (writes
        outside it fail closed). There is no copy-sandbox and no change_set — the
        edits ARE the output, left in *target_dir* for the human's git to review.
    Implementation: build the Codex config rooted at *target_dir* via
        build_codex_config(codex_bin=codex_bin(), cwd=target_dir); compose
        instructions from the generator_system prompt, a surface-specific capability
        preface, and *contract_text*; stream the turn to exhaustion via ``async
        for`` and return the collected CodexEvents. The thread itself runs
        sandbox=workspace_write, approval_mode=deny_all, with the inline network
        config (see drivers/_codex.py).
    Example: ``await run_generator(runner, contract_text="do X", target_dir=p,
        surface="backend")`` returns all events emitted while editing *p*.
    """
    from forge_mcp.config import codex_bin
    from forge_mcp.drivers._codex import CodexEvent, build_codex_config  # noqa: F401
    from forge_mcp.prompts import load_prompt

    config = build_codex_config(codex_bin=str(codex_bin()), cwd=target_dir)
    preface = _SURFACE_PREFACE.get(surface, _DEFAULT_PREFACE)
    instructions = f"{load_prompt('generator_system')}\n\n{preface}\n\n{contract_text}"

    events: list[CodexEvent] = []
    async for event in runner.generate(
        instructions=instructions,
        config=config,
        run_log_path=run_log_path,
    ):
        events.append(event)
    return events
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_generator.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/drivers/generator.py' 'tests/test_generator.py'
git commit -m "refactor(generator): edit target_dir directly, rename sandbox param\n\nPer spec 0003 \u00a75: run_generator now takes target_dir: Path (was sandbox: Path)\nand roots Codex at the repo via build_codex_config(cwd=target_dir). The turn\nedits the repository in place under workspace-write; there is no copy-sandbox.\nUpdate test_generator.py to the target_dir signature and pin that the configured\ncwd reaches the runner.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: run_evaluator: drop unused sandbox param, name cwd in prompt (§6)

**Files:**
- Modify: `src/forge_mcp/drivers/evaluator.py`
- Test: `tests/test_evaluator.py`

**Interfaces:**
- Consumes: build_options, git_deny_hooks, run_log_tee from _claude; load_prompt; envelope; EvalResult, TriageResult models; claude_bin()
- Produces: async def run_evaluator(runner, *, spec_text: str, eval_schema: dict, cwd: Path, run_log_path: Path | None = None) -> EvalResult  (sandbox param removed). run_triage unchanged: (runner, *, spec_text, eval_result, triage_schema, cwd, run_log_path=None) -> TriageResult

- [ ] **Step 1: Write the failing test**

Write to `tests/test_evaluator.py`:

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
    """Design: §6 the Evaluator emits EvalResult diffing code in cwd vs the spec.
    Implementation: fake returns one gap; run_evaluator (no sandbox param) parses it.
    Example: no_gaps False, one EvalGap.
    """
    payload = {
        "no_gaps": False,
        "summary": "1 gap",
        "gaps": [
            {
                "title": "missing X",
                "severity": "high",
                "design_doc_section": "§7.4",
                "current_state": "absent",
                "expected_state": "present",
                "suggested_fix": "add X",
            }
        ],
    }
    runner = FakeClaudeRunner([structured(payload)])
    res = await run_evaluator(
        runner, spec_text="s", eval_schema={"type": "object"}, cwd=tmp_path
    )
    assert not res.no_gaps and res.gaps[0].title == "missing X"


@pytest.mark.driver
async def test_evaluator_has_no_sandbox_param():
    """Design: §6 run_evaluator drops the now-unused sandbox parameter; cwd alone
        names the directory the evaluator diffs against the spec.
    Implementation: introspect the signature and assert 'sandbox' is absent and
        'cwd' is present.
    Example: 'sandbox' not in signature(run_evaluator).parameters.
    """
    import inspect

    params = inspect.signature(run_evaluator).parameters
    assert "sandbox" not in params
    assert "cwd" in params


@pytest.mark.driver
async def test_triage_demotes_uncited_design_fault(tmp_path: Path):
    """Design: §6 a design_fault with an invalid citation demotes to a code-bug.
    Implementation: fake triage claims a design fault citing absent text.
    Example: the row no longer passes the citation gate.
    """
    spec = "real spec text that is long enough to cite verbatim here."
    ev = EvalResult(no_gaps=False, summary="x", gaps=[])
    payload = {
        "triages": [
            {
                "gap_title": "missing X",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": ["not in the spec at all here!!"],
                "explanation": "e",
            }
        ]
    }
    runner = FakeClaudeRunner([structured(payload)])
    tr = await run_triage(
        runner, spec_text=spec, eval_result=ev, triage_schema={"type": "object"}, cwd=tmp_path
    )
    from forge_mcp.triage import passes_citation_gate

    assert not passes_citation_gate(tr.triages[0], spec)
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_evaluator.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Delete the `sandbox: Path` parameter from run_evaluator's signature (was evaluator.py:18). In the prompt body, replace the `## Sandbox path\n\n{sandbox}` block and its surrounding wording (was evaluator.py:44-49) with a `## Project path\n\n{cwd}` block that diffs 'the code in the project above'. Rewrite the run_evaluator Design/Implementation/Example docstring to drop the sandbox mention. Do NOT touch run_triage (spec §6: run_triage already takes only cwd). Imports unchanged — `from pathlib import Path` is still used by the cwd annotation. Verify via `grep -n sandbox src/forge_mcp/drivers/evaluator.py` — zero hits after the edit. The phases.py call site (run_evaluator(... cwd=target_dir), with the sandbox kwarg removed) is owned by the phases-rewrite task.

Apply:

```python
"""Evaluator stage (§5.3): gap-finding eval pass and triage pass."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import EvalResult, TriageResult
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_evaluator(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    eval_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> EvalResult:
    """Run the Evaluator stage and return a validated EvalResult (§6).

    Design: §6 the Evaluator diffs the code under *cwd* against the frozen
        spec_text and emits a structured list of gaps; it is git-mutation-denied
        (PreToolUse git-deny hook) under bypassPermissions so it can read the
        repo but cannot commit. With the single-plan direct-edit harness the
        evaluated tree IS *cwd* (target_dir), so the now-unused sandbox parameter
        is gone — *cwd* alone names the directory.
    Implementation: build options with the evaluator_system prompt, the eval JSON
        schema as output_format, git-deny hooks, and the provided cwd; compose a
        prompt that names *cwd* and the frozen spec; call runner.run; validate the
        structured_output into an EvalResult.
    Example: ``await run_evaluator(runner, spec_text="# spec",
        eval_schema={...}, cwd=Path("/r"))`` returns an EvalResult.
    """
    options = build_options(
        system=load_prompt("evaluator_system"),
        output_format=envelope(eval_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    prompt = (
        f"## Frozen design spec\n\n{spec_text}\n\n"
        f"## Project path\n\n{cwd}\n\n"
        "Diff the code in the project above against the frozen design spec above and "
        "report all gaps."
    )
    result = await runner.run(prompt=prompt, options=options)
    return EvalResult(**(result.structured_output or {}))


async def run_triage(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    eval_result: EvalResult,
    triage_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> TriageResult:
    """Run the Triage stage and return a validated TriageResult (§5.3).

    Design: §5.3 the Triage stage classifies each gap from the eval pass as
        either a code bug or a design fault; a design fault requires verbatim
        citations from the frozen spec_text to pass the citation gate; the
        orchestrator (not this function) applies any amendments to spec.md.
    Implementation: build options with the evaluator_triage prompt, the triage
        JSON schema as output_format, git-deny hooks, and the provided cwd;
        compose a prompt that includes the frozen spec and the serialised gaps;
        call runner.run; validate the structured_output into a TriageResult.
    Example: ``await run_triage(runner, spec_text="# spec", eval_result=er,
        triage_schema={...}, cwd=Path("/r"))`` returns a TriageResult.
    """
    options = build_options(
        system=load_prompt("evaluator_triage"),
        output_format=envelope(triage_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    gaps_text = "\n".join(
        f"- {g.title} ({g.severity}): {g.current_state} → {g.expected_state}"
        for g in eval_result.gaps
    )
    prompt = (
        f"## Frozen design spec\n\n{spec_text}\n\n"
        f"## Gaps to triage\n\n{gaps_text or '(none)'}\n\n"
        "Classify each gap as a code bug or a design fault. "
        "For design faults, cite verbatim sections from the spec above."
    )
    result = await runner.run(prompt=prompt, options=options)
    return TriageResult(**(result.structured_output or {}))
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_evaluator.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/drivers/evaluator.py' 'tests/test_evaluator.py'
git commit -m "refactor(evaluator): drop unused sandbox param, name cwd in prompt\n\nPer spec 0003 \u00a76: with the single-plan direct-edit harness the evaluated tree\nIS cwd (target_dir), so run_evaluator's now-unused sandbox: Path parameter is\nremoved and the prompt names the project path via cwd. run_triage is unchanged\n(it already takes only cwd). Update test_evaluator.py to the new signature and\nassert sandbox is gone.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: run_planner: return one Plan (was PlanSet) (§3/§6)

**Files:**
- Modify: `src/forge_mcp/drivers/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: forge_mcp.models.Plan (collapsed model: {surface, verification_command, body}, extra='forbid') — produced by the models.py rewrite task; build_options, git_deny_hooks, run_log_tee from _claude; load_prompt('planner_system'); envelope; claude_bin()
- Produces: async def run_planner(runner, *, spec_text: str, plan_schema: dict, cwd: Path, run_log_path: Path | None = None) -> Plan  (return type/construction Plan, was PlanSet; output_format=envelope(plan_schema) unchanged). Caller passes plan_schema=Plan.model_json_schema().

- [ ] **Step 1: Write the failing test**

Write to `tests/test_planner.py`:

```python
# tests/test_planner.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.drivers.planner import run_planner
from forge_mcp.models import Plan
from tests.fakes import FakeClaudeRunner, structured


@pytest.mark.driver
async def test_planner_parses_single_plan(tmp_path: Path):
    """Design: §3/§6 the Planner emits exactly one Plan; run_planner validates the
        structured output into the collapsed Plan model {surface, verification_command,
        body} and returns it (no PlanSet, no plans list).
    Implementation: a fake runner returns one Plan dict; run_planner parses it and
        returns a Plan whose fields round-trip.
    Example: Plan(surface='backend', verification_command='pytest -q', body='plan 1').
    """
    payload = {
        "surface": "backend",
        "verification_command": "pytest -q",
        "body": "plan 1",
    }
    runner = FakeClaudeRunner([structured(payload)])
    plan = await run_planner(
        runner, spec_text="# design", plan_schema=Plan.model_json_schema(), cwd=tmp_path
    )
    assert isinstance(plan, Plan)
    assert plan.surface == "backend"
    assert plan.verification_command == "pytest -q"
    assert plan.body == "plan 1"


@pytest.mark.driver
async def test_planner_allows_no_verification_command(tmp_path: Path):
    """Design: §3 verification_command is optional (None ⇒ no completion gate);
        run_planner must accept a Plan that omits it and default to None.
    Implementation: a fake runner returns a Plan dict without verification_command;
        run_planner returns a Plan whose verification_command is None.
    Example: Plan(surface='frontend', body='x').verification_command is None.
    """
    payload = {"surface": "frontend", "body": "build the UI"}
    runner = FakeClaudeRunner([structured(payload)])
    plan = await run_planner(
        runner, spec_text="# design", plan_schema=Plan.model_json_schema(), cwd=tmp_path
    )
    assert plan.surface == "frontend"
    assert plan.verification_command is None
    assert plan.body == "build the UI"
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_planner.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Replace the import `from forge_mcp.models import PlanSet` with `from forge_mcp.models import Plan` (was planner.py:9). Change the return annotation `-> PlanSet` → `-> Plan` (was planner.py:21) and the construction `return PlanSet(**(result.structured_output or {}))` → `return Plan(**(result.structured_output or {}))` (was planner.py:44). Rewrite the module docstring (planner.py:1) and the run_planner Design/Implementation/Example docstring to the single-Plan framing. KEEP `output_format=envelope(plan_schema)` exactly as-is (spec §6). Verify via `grep -n PlanSet src/forge_mcp/drivers/planner.py` — zero hits after the edit. The collapsed Plan model (drop id/depends_on/file_scope) and the deletion of PlanSet land in the models.py rewrite task; this task depends on that model but only references the Plan symbol. The engine.py call site (run_planner → Plan, plan_schema=Plan.model_json_schema()) is owned by the engine-rewrite task.

Apply:

```python
"""Planner stage (§5.1 superseded by §3): convert a spec into a single structured Plan."""

from __future__ import annotations

from pathlib import Path

from forge_mcp.config import claude_bin
from forge_mcp.drivers._claude import ClaudeRunner, build_options, git_deny_hooks, run_log_tee
from forge_mcp.models import Plan
from forge_mcp.prompts import load_prompt
from forge_mcp.schemas import envelope


async def run_planner(
    runner: ClaudeRunner,
    *,
    spec_text: str,
    plan_schema: dict,
    cwd: Path,
    run_log_path: Path | None = None,
) -> Plan:
    """Run the Planner stage and return a single validated Plan (§5.1 superseded).

    Design: §3 the Planner reads the frozen spec and returns exactly one Plan; it
        is git-mutation-denied (PreToolUse git-deny hook) and runs under
        bypassPermissions so it can read the repo to ground the plan but cannot
        commit. The multi-plan PlanSet/DAG is gone — one plan, one tree.
    Implementation: build options with the planner_system prompt,
        output_format=envelope(plan_schema), git-deny hooks, and the provided cwd
        — the caller passes plan_schema=Plan.model_json_schema(); call runner.run
        with spec_text as the prompt; validate the structured_output into a Plan.
    Example: ``await run_planner(runner, spec_text="# spec",
        plan_schema=Plan.model_json_schema(), cwd=Path("/r"))`` returns one Plan.
    """
    options = build_options(
        system=load_prompt("planner_system"),
        output_format=envelope(plan_schema),
        hooks=git_deny_hooks(),
        cwd=str(cwd),
        cli_path=str(claude_bin()),
        stderr=run_log_tee(run_log_path) if run_log_path is not None else None,
    )
    result = await runner.run(prompt=spec_text, options=options)
    return Plan(**(result.structured_output or {}))
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_planner.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/drivers/planner.py' 'tests/test_planner.py'
git commit -m "refactor(planner): return one Plan instead of a PlanSet\n\nPer spec 0003 \u00a73/\u00a76: the Planner emits exactly one plan, so run_planner's\nreturn type and construction become Plan (was PlanSet); the caller passes\nplan_schema=Plan.model_json_schema(). output_format=envelope(plan_schema) is\nunchanged. Update test_planner.py to assert a single collapsed Plan and the\noptional verification_command default.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Change apply_amendments to take list[GapTriage] and drop plan_id from the amendment log entry

**Files:**
- Modify: `src/forge_mcp/orchestrator/amend.py`
- Test: `tests/test_amend.py`

**Interfaces:**
- Consumes: forge_mcp.triage.is_valid_citation(text, spec) -> bool; forge_mcp.triage.amendment_target_present(before, spec) -> bool; forge_mcp.convergence.fingerprint(items) -> frozenset[str]; forge_mcp.state.durable_replace(path, str); forge_mcp.state.light_replace(path, str); forge_mcp.state.durable_append(path, str); forge_mcp.artifacts.RunLayout with .spec_md/.spec_fingerprint/.spec_amendments; forge_mcp.models.GapTriage with .proposed_amendment/.fault_kind
- Produces: def apply_amendments(layout: RunLayout, *, spec_text: str, spec_fingerprint: str, proposed: list[GapTriage], now: str) -> AmendOutcome. dataclass AmendOutcome(applied: list[ProposedAmendment], rejected: list[ProposedAmendment], new_spec: str, new_fingerprint: str, churn_fingerprint: frozenset[str]). The spec_amendments.md JSON entry no longer contains a 'plan_id' key. Caller (phases.run_plan_loop, separate task) passes proposed=[row] where row is a GapTriage.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_amend.py`:

```python
from __future__ import annotations

import json

from forge_mcp.artifacts import RunLayout
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.orchestrator.amend import apply_amendments

SPEC = "Alpha section. The widget must flush before close. Omega section."


def _triage(before, after):
    """Build a design-fault GapTriage with a ProposedAmendment for test use.

    Design: §4 tests need a consistent triage fixture with a valid cited section
        for the in-loop single-row amendment signature.
    Implementation: hard-code gap_title, fault_kind, cited_sections; parameterise
        before/after so each test can supply the relevant amendment text.
    Example: _triage('old text', 'new text') returns a valid design-fault GapTriage.
    """
    return GapTriage(
        gap_title="g",
        design_fault=True,
        fault_kind="contradiction",
        cited_sections=["The widget must flush before close"],
        explanation="e",
        proposed_amendment=ProposedAmendment(
            cited_sections=["The widget must flush before close"],
            before=before,
            after=after,
            rationale="r",
        ),
    )


def _seed(tmp_path):
    """Create a RunLayout with an empty spec_amendments.md log.

    Design: §4/§10 amend writes the run-level spec_amendments.md; tests seed it
        empty so each appended entry is observable.
    Implementation: build RunLayout.for_run(tmp_path), make its parent dir, write
        an empty amendments file, and return the layout.
    Example: lay = _seed(tmp_path); lay.spec_amendments.read_text() == ''.
    """
    lay = RunLayout.for_run(tmp_path)
    lay.spec_amendments.parent.mkdir(parents=True, exist_ok=True)
    lay.spec_amendments.write_text("")
    return lay


def test_valid_amendment_applies_bumps_fingerprint_appends_log(tmp_path):
    """Design: §4 a cited, present-before amendment applies, bumps fp, logs.
    Implementation: apply one valid amendment passed as a bare GapTriage row.
    Example: spec text changed; spec_amendments.md non-empty.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("flush before close", "flush and fsync before close")],
        now="t",
    )
    assert "fsync" in out.new_spec and out.new_fingerprint != "fp0"
    assert len(out.applied) == 1 and lay.spec_amendments.read_text() != ""


def test_amendment_log_entry_has_no_plan_id(tmp_path):
    """Design: §11 the spec_amendments.md entry drops plan_id (there is no plan id).
    Implementation: apply a valid amendment, parse the appended JSON line, assert the
        keys are exactly the surviving structured fields and 'plan_id' is absent.
    Example: json.loads(entry).keys() does not contain 'plan_id'.
    """
    lay = _seed(tmp_path)
    apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("flush before close", "flush and fsync before close")],
        now="t",
    )
    line = lay.spec_amendments.read_text().strip()
    entry = json.loads(line)
    assert "plan_id" not in entry
    assert set(entry) == {
        "now",
        "fault_kind",
        "cited_sections",
        "before",
        "after",
        "rationale",
    }
    assert entry["now"] == "t" and entry["fault_kind"] == "contradiction"


def test_amendment_with_absent_before_is_rejected(tmp_path):
    """Design: §4 a 'before' no longer verbatim is rejected (demote).
    Implementation: before text not in spec; passed as a bare GapTriage row.
    Example: rejected list has the proposal; spec unchanged.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("nonexistent target text", "x")],
        now="t",
    )
    assert out.applied == [] and len(out.rejected) == 1 and out.new_spec == SPEC


def test_rejected_amendment_still_counts_toward_churn(tmp_path):
    """Design: §4/§6.7 rejected proposals still feed amendment churn.
    Implementation: a rejected proposal yields a non-empty churn fingerprint.
    Example: churn_fingerprint non-empty.
    """
    lay = _seed(tmp_path)
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[_triage("nonexistent target text", "x")],
        now="t",
    )
    assert len(out.churn_fingerprint) >= 1


def test_serial_amendments_apply_against_running_spec(tmp_path):
    """Design: §4 amendments apply serially so a later row sees the earlier edit.
    Implementation: row1 inserts 'fsync', row2 cites text only present after row1;
        both apply and the final spec carries both edits.
    Example: new_spec contains both 'fsync' and 'flushed-and-synced'.
    """
    lay = _seed(tmp_path)
    row1 = _triage("flush before close", "flush and fsync before close")
    row2 = _triage("flush and fsync before close", "flushed-and-synced before close")
    out = apply_amendments(
        lay,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        proposed=[row1, row2],
        now="t",
    )
    assert len(out.applied) == 2
    assert "flushed-and-synced" in out.new_spec and "flush before close" not in out.new_spec
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_amend.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

In src/forge_mcp/orchestrator/amend.py (current 122 lines):
1. Signature (current line 51): change `proposed: list[tuple[str, GapTriage]]` -> `proposed: list[GapTriage]`.
2. Loop header (current line 78): change `for plan_id, triage in proposed:` -> `for triage in proposed:` (drops the unpacked plan_id local).
3. JSON entry dict (current lines 96-105): DELETE the `"plan_id": plan_id,` key/value line (current line 97). Keep the remaining six keys (now, fault_kind, cited_sections, before, after, rationale) and `sort_keys=True`.
4. Docstrings: rewrite the AmendOutcome class docstring (lines 16-24) and the apply_amendments docstring (lines 54-71) from the §5.3 wave-boundary wording to the §4/§15 in-loop wording (given in new_code). The `_proposal_signature` docstring is unchanged.
Imports unchanged: hashlib, json, dataclass, RunLayout, fingerprint, GapTriage, ProposedAmendment, durable_append/durable_replace/light_replace, amendment_target_present/is_valid_citation all remain used. The full replacement file content is given in new_code.

Apply:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from forge_mcp.artifacts import RunLayout
from forge_mcp.convergence import fingerprint
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.state import durable_append, durable_replace, light_replace
from forge_mcp.triage import amendment_target_present, is_valid_citation


@dataclass
class AmendOutcome:
    """Result of apply_amendments — applied/rejected lists plus derived artifacts.

    Design: §4 the per-plan loop applies amendments in-loop; this dataclass
        carries the final spec state and churn signal for the caller to rebind
        spec_text/spec_fingerprint and feed the amendment-churn detector.
    Implementation: plain mutable dataclass; caller reads fields after the
        function returns.
    Example: AmendOutcome(applied=[...], rejected=[], new_spec='...', ...).
    """

    applied: list[ProposedAmendment]
    rejected: list[ProposedAmendment]
    new_spec: str
    new_fingerprint: str
    churn_fingerprint: frozenset[str]


def _proposal_signature(amendment: ProposedAmendment, fault_kind: str | None) -> str:
    """Return a stable per-proposal string for churn fingerprinting.

    Design: §6.7 the churn fingerprint must include both applied and rejected
        proposals so a repeatedly-rejected amendment still trips churn detection.
    Implementation: sort cited_sections for order-independence, join with '|',
        then append '|' and fault_kind (or '' if None).
    Example: _proposal_signature(pa, 'contradiction') -> 'Sec A|Sec B|contradiction'.
    """
    sections_key = "|".join(sorted(amendment.cited_sections))
    return f"{sections_key}|{fault_kind or ''}"


def apply_amendments(
    layout: RunLayout,
    *,
    spec_text: str,
    spec_fingerprint: str,
    proposed: list[GapTriage],
    now: str,
) -> AmendOutcome:
    """Apply validated design-fault amendments to spec.md in-loop (§4).

    Design: §4 the per-plan loop calls this with the validated row(s) for the
        current iteration; each amendment is re-validated against the then-current
        spec and applied serially, durably rewriting spec.md so the next iteration
        evaluates against it. Rejected proposals still feed the churn fingerprint.
    Implementation: iterate proposed in order; for each triage carrying a
        proposed_amendment, re-validate all cited_sections via is_valid_citation
        against the current spec AND verify the 'before' text is still present via
        amendment_target_present; on pass, replace the first occurrence of 'before'
        with 'after', recompute the SHA-256 fingerprint, durably write the updated
        spec (durable_replace) and fingerprint (light_replace), and append a
        structured entry (no plan_id field) to spec_amendments.md via durable_append;
        on failure, add to rejected only. All proposals (applied or rejected) feed
        churn_fingerprint. inputs/design.md is never touched (I4).
    Example: apply_amendments(lay, spec_text='old', spec_fingerprint='fp',
        proposed=[row], now=t) returns AmendOutcome whose new_spec contains the
        amended text.
    """
    current_spec = spec_text
    current_fp = spec_fingerprint
    applied: list[ProposedAmendment] = []
    rejected: list[ProposedAmendment] = []
    churn_keys: list[str] = []

    for triage in proposed:
        pa = triage.proposed_amendment
        if pa is None:
            continue

        churn_keys.append(_proposal_signature(pa, triage.fault_kind))

        citations_valid = all(is_valid_citation(c, current_spec) for c in pa.cited_sections)
        target_present = amendment_target_present(pa.before, current_spec)

        if citations_valid and target_present:
            new_spec = current_spec.replace(pa.before, pa.after, 1)
            new_fp = hashlib.sha256(new_spec.encode()).hexdigest()

            durable_replace(layout.spec_md, new_spec)
            light_replace(layout.spec_fingerprint, new_fp)

            entry = json.dumps(
                {
                    "now": now,
                    "fault_kind": triage.fault_kind,
                    "cited_sections": pa.cited_sections,
                    "before": pa.before,
                    "after": pa.after,
                    "rationale": pa.rationale,
                },
                sort_keys=True,
            )
            durable_append(layout.spec_amendments, entry + "\n")

            current_spec = new_spec
            current_fp = new_fp
            applied.append(pa)
        else:
            rejected.append(pa)

    return AmendOutcome(
        applied=applied,
        rejected=rejected,
        new_spec=current_spec,
        new_fingerprint=current_fp,
        churn_fingerprint=fingerprint(churn_keys),
    )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_amend.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/amend.py' 'tests/test_amend.py'
git commit -m "refactor(amend): apply_amendments takes list[GapTriage]; drop plan_id from amendment log"
```

---

### Task 7: plan_state.py — drop awaiting_amendment / sandbox_path / plan_id; write single run-level plan_state.json

**Files:**
- Modify: `src/forge_mcp/orchestrator/plan_state.py`
- Test: `tests/test_plan_state.py`

**Interfaces:**
- Consumes: RunLayout.plan_state_json (property, no args) and RunLayout.root from the rewritten artifacts.py (cluster A3 task 3); forge_mcp.state.write_json(path, model, *, durable).
- Produces: PlanStateValue = Literal['generating','verifying','evaluating','triaging','remediating','done','incomplete','failed'] (no 'awaiting_amendment'). PlanStatePayload(extra='forbid') fields {state, iteration:int, last_completed_iteration:int, last_updated_at:str, stop_reason:str|None} (NO plan_id, NO sandbox_path). PlanState.__init__(self, layout: RunLayout) -> None (no plan_id/sandbox_path kwargs); methods set_state(state, *, now:str), bump_iteration(now:str), record_completed(n:int, now:str).

- [ ] **Step 1: Write the failing test**

Write to `tests/test_plan_state.py`:

```python
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.plan_state import (
    PlanState,
    PlanStatePayload,
    PlanStateValue,
)


def test_plan_state_writes_run_level_file(tmp_path):
    """Design: §9 plan state is a single run-level plan_state.json (no plans/<id>/ nesting).
    Implementation: construct PlanState(layout) with no plan_id/sandbox_path, drive states.
    Example: <run_dir>/plan_state.json holds state='done', iteration=1.
    """
    ps = PlanState(RunLayout.for_run(tmp_path))
    ps.set_state("generating", now="t")
    ps.bump_iteration(now="t")
    ps.record_completed(1, now="t")
    ps.set_state("done", now="t")
    data = json.loads((tmp_path / "plan_state.json").read_text())
    assert data["state"] == "done"
    assert data["iteration"] == 1
    assert data["last_completed_iteration"] == 1


def test_payload_rejects_dropped_fields():
    """Design: §9 plan_id and sandbox_path are removed from the payload schema.
    Implementation: extra='forbid' rejects either key; the model has neither field.
    Example: passing plan_id raises ValidationError.
    """
    base = dict(
        state="generating",
        iteration=0,
        last_completed_iteration=0,
        last_updated_at="t",
    )
    PlanStatePayload(**base)  # accepted
    with pytest.raises(ValidationError):
        PlanStatePayload(plan_id="p1", **base)
    with pytest.raises(ValidationError):
        PlanStatePayload(sandbox_path="/sb", **base)


def test_awaiting_amendment_is_not_a_state():
    """Design: §9 'awaiting_amendment' is removed from PlanStateValue.
    Implementation: the Literal no longer admits it; the payload rejects it.
    Example: state='awaiting_amendment' raises ValidationError.
    """
    assert "awaiting_amendment" not in PlanStateValue.__args__
    with pytest.raises(ValidationError):
        PlanStatePayload(
            state="awaiting_amendment",
            iteration=0,
            last_completed_iteration=0,
            last_updated_at="t",
        )


def test_no_plans_subdir_created(tmp_path):
    """Design: §9/§10 the flat layout writes plan_state.json at the run root, not plans/<id>/.
    Implementation: construct PlanState and assert no plans/ directory appears.
    Example: only <run_dir>/plan_state.json exists after a write.
    """
    ps = PlanState(RunLayout.for_run(tmp_path))
    ps.set_state("generating", now="t")
    assert (tmp_path / "plan_state.json").is_file()
    assert not (tmp_path / "plans").exists()
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_plan_state.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

From src/forge_mcp/orchestrator/plan_state.py: (1) remove 'import os' (line 3) — os.makedirs is gone since there is no per-plan dir. (2) remove 'awaiting_amendment' from the PlanStateValue Literal (current line 17). (3) remove the 'plan_id: str' and 'sandbox_path: str' fields from PlanStatePayload (current lines 37-38). (4) PlanState.__init__: remove the 'plan_id' and 'sandbox_path' keyword-only params (current lines 61-63), remove 'self._plan_id = plan_id' (line 76), remove the 'os.makedirs(layout.plan_dir(plan_id), ...)' call (line 77), and remove plan_id/sandbox_path from the initial PlanStatePayload(...) construction (lines 78-85). (5) _persist: change 'write_json(self._layout.plan_state(self._plan_id), ...)' to 'write_json(self._layout.plan_state_json, ...)' (line 96). (6) update all docstrings that say 'plans/<id>/state.json' or reference plan_id/sandbox_path. Verified call sites that must already be updated by their own tasks before this lands: phases.py:221 'PlanState(layout, plan_id=plan.id, sandbox_path=str(sandbox))' (cluster A2 phases task) and phases.py:324 set_state('awaiting_amendment') (removed in the phases rewrite).

Apply:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from forge_mcp.artifacts import RunLayout
from forge_mcp.state import write_json

PlanStateValue = Literal[
    "generating",
    "verifying",
    "evaluating",
    "triaging",
    "remediating",
    "done",
    "incomplete",
    "failed",
]


class PlanStatePayload(BaseModel, extra="forbid"):
    """Durable plan-level state checkpoint written to <run_dir>/plan_state.json.

    Design: §9/§10 the run executes exactly one plan, so plan state is a single
        run-level file with no plan_id and no sandbox (the Generator edits
        target_dir in place — there is no copy-sandbox path to record).
    Implementation: Pydantic BaseModel with extra='forbid' to reject unknown
        fields; all fields required except stop_reason. 'awaiting_amendment' is
        gone from PlanStateValue because amendments are applied in-loop (§4).
    Example: PlanStatePayload(state='generating', iteration=0,
        last_completed_iteration=0, last_updated_at='t').
    """

    state: PlanStateValue
    iteration: int
    last_completed_iteration: int
    last_updated_at: str
    stop_reason: str | None = None


class PlanState:
    """Sole writer of the single run-level plan_state.json.

    Design: §9/§10 with one plan per run, plan state collapses to one file at the
        run root (was plans/<id>/state.json); a single object owns it to keep the
        write path single-writer and crash-safe.
    Implementation: holds a PlanStatePayload in memory; every mutation durably
        writes via write_json to layout.plan_state_json so the file always
        matches the latest call.
    Example: PlanState(layout).set_state('done', now='t').
    """

    def __init__(self, layout: RunLayout) -> None:
        """Initialise PlanState at the run root with state='generating'.

        Design: §9/§10 the run dir already exists (init_run_layout ran), so no
            per-plan directory needs creating; the first write lands directly at
            the run root. The constructor loses the plan_id and sandbox_path
            params of the multi-plan design.
        Implementation: store layout; build the initial PlanStatePayload with
            state='generating', iteration=0, last_completed_iteration=0, and an
            empty last_updated_at placeholder. No os.makedirs (the run root is
            already present).
        Example: PlanState(lay).payload would be state='generating', iteration=0.
        """
        self._layout = layout
        self._payload = PlanStatePayload(
            state="generating",
            iteration=0,
            last_completed_iteration=0,
            last_updated_at="",
        )

    def _persist(self) -> None:
        """Durably write the current payload to <run_dir>/plan_state.json.

        Design: §9/§13 every mutation must be crash-safe, so we always use
            durable=True to call durable_replace under the hood.
        Implementation: delegate to write_json with durable=True against
            layout.plan_state_json (the single run-level path).
        Example: _persist() writes plan_state.json atomically at the run root.
        """
        write_json(self._layout.plan_state_json, self._payload, durable=True)

    def set_state(self, state: PlanStateValue, *, now: str) -> None:
        """Update the plan state and durably persist.

        Design: §9 state transitions are the primary mutation; persisting
            immediately ensures the file reflects the latest logical state even
            if the process crashes right after this call.
        Implementation: update payload.state and payload.last_updated_at in
            place via model_copy, then call _persist().
        Example: set_state('done', now='2024-01-01T00:00:00Z') writes
            state='done' to plan_state.json.
        """
        self._payload = self._payload.model_copy(update={"state": state, "last_updated_at": now})
        self._persist()

    def bump_iteration(self, now: str) -> None:
        """Increment iteration by 1 and durably persist.

        Design: §9 each iteration of the plan loop increments this counter so the
            orchestrator can track progress against the cap.
        Implementation: update payload.iteration to iteration + 1 and update
            last_updated_at via model_copy, then call _persist().
        Example: bump_iteration(now='t') on iteration=0 writes iteration=1.
        """
        self._payload = self._payload.model_copy(
            update={
                "iteration": self._payload.iteration + 1,
                "last_updated_at": now,
            }
        )
        self._persist()

    def record_completed(self, n: int, now: str) -> None:
        """Set last_completed_iteration to n and durably persist.

        Design: §9 records the most recently completed iteration for forensics;
            there is no cross-run resume, so this field is forensic-only.
        Implementation: update payload.last_completed_iteration to n and
            last_updated_at via model_copy, then call _persist().
        Example: record_completed(2, now='t') writes last_completed_iteration=2.
        """
        self._payload = self._payload.model_copy(
            update={"last_completed_iteration": n, "last_updated_at": now}
        )
        self._persist()
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_plan_state.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/plan_state.py' 'tests/test_plan_state.py'
git commit -m "refactor(plan-state): collapse to single run-level plan_state.json\n\nDrop awaiting_amendment from PlanStateValue, remove plan_id and\nsandbox_path from PlanStatePayload, and have PlanState write one\nrun-level plan_state.json (no plans/<id>/ nesting) per spec 0003 \u00a79.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: artifacts.py — flatten run layout: drop plan_id keying, add plan_md/plan_json/plan_state_json, remove planset/merge/conflict/git paths

**Files:**
- Modify: `src/forge_mcp/artifacts.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: forge_mcp.state.{durable_append, durable_replace, light_replace} (unchanged imports).
- Produces: RunLayout properties (no args): state_json, run_log, inputs_dir, inputs_design, design_fingerprint, spec_md, spec_amendments, spec_fingerprint, plan_md, plan_json, plan_state_json. RunLayout methods keyed ONLY on n: iteration_dir(n), contract(n), summary(n), eval(n), triage(n), gap_fingerprint(n), verify_txt(n) — dir name still 'iteration-{n}' at the run root. init_run_layout(run_dir, design_text, *, design_fingerprint) -> RunLayout (UNCHANGED). ensure_iteration_dir(layout, n) -> Path (plan_id param dropped). REMOVED: planset_json, plan_dir, plan_state(id), plan_manifest, plan_merge, conflict_fingerprint, git_violation.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_artifacts.py`:

```python
from __future__ import annotations

import hashlib

from forge_mcp.artifacts import RunLayout, ensure_iteration_dir, init_run_layout


def test_flat_run_level_paths(tmp_path):
    """Design: §10 the layout is flattened to the run root (one plan, no plans/<id>/).
    Implementation: assert plan.json, plan.md, plan_state.json live at the run root.
    Example: lay.plan_json == run_dir / 'plan.json'.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.spec_md == tmp_path / "spec.md"
    assert lay.plan_json == tmp_path / "plan.json"
    assert lay.plan_md == tmp_path / "plan.md"
    assert lay.plan_state_json == tmp_path / "plan_state.json"


def test_iteration_paths_keyed_on_n_only(tmp_path):
    """Design: §10 iteration artifacts are keyed only on n, dir name 'iteration-{n}' at run root.
    Implementation: call the single-arg iteration methods and assert parents/names.
    Example: lay.eval(2).parent == lay.iteration_dir(2); dir name is 'iteration-2'.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.iteration_dir(2) == tmp_path / "iteration-2"
    assert lay.iteration_dir(2).name == "iteration-2"
    for meth, fname in [
        (lay.contract, "contract.md"),
        (lay.summary, "summary.md"),
        (lay.eval, "eval.json"),
        (lay.triage, "triage.json"),
        (lay.gap_fingerprint, "gap_fingerprint.json"),
        (lay.verify_txt, "verify.txt"),
    ]:
        p = meth(2)
        assert p.parent == lay.iteration_dir(2)
        assert p.name == fname


def test_removed_path_methods_are_gone(tmp_path):
    """Design: §10 planset/merge/conflict/git/per-plan-dir paths are removed.
    Implementation: the removed attributes no longer exist on RunLayout.
    Example: hasattr(lay, 'planset_json') is False.
    """
    lay = RunLayout.for_run(tmp_path)
    for attr in (
        "planset_json",
        "plan_dir",
        "plan_state",
        "plan_manifest",
        "plan_merge",
        "conflict_fingerprint",
        "git_violation",
    ):
        assert not hasattr(lay, attr), f"{attr} should be removed"


def test_ensure_iteration_dir_creates_at_run_root(tmp_path):
    """Design: §10 ensure_iteration_dir is keyed only on n and creates the dir on demand.
    Implementation: call with (layout, n) and assert the dir exists at the run root.
    Example: ensure_iteration_dir(lay, 1) creates run_dir/iteration-1.
    """
    lay = RunLayout.for_run(tmp_path)
    path = ensure_iteration_dir(lay, 1)
    assert path == tmp_path / "iteration-1"
    assert path.is_dir()


def test_init_run_layout_freezes_design_and_seeds_spec(tmp_path):
    """Design: §10/§3.1 design.md is immutable; spec.md starts equal to design (UNCHANGED).
    Implementation: init then read files.
    Example: spec.md == design content at init; spec_amendments empty.
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

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_artifacts.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

From src/forge_mcp/artifacts.py remove the following (verified by re-grep): (1) the 'conflict_fingerprint' property (current lines 57-65). (2) the '── planset ──' block: 'planset_json' property (lines 135-143). (3) from the '── per-plan files ──' block: 'plan_md(self, plan_id)' (lines 147-154), 'plan_dir(self, plan_id)' (lines 156-163), 'plan_state(self, plan_id)' (lines 165-172), 'plan_manifest(self, plan_id)' (lines 174-181), 'plan_merge(self, plan_id)' (lines 183-190). (4) 'git_violation(self, plan_id, n)' (lines 257-264). (5) drop the 'plan_id' param from every iteration method — iteration_dir, contract, summary, eval, triage, gap_fingerprint, verify_txt (lines 194-255) — and re-root them on self.root (was self.plan_dir(plan_id)). (6) drop the 'plan_id' param from ensure_iteration_dir (lines 302-314). ADD the new run-level properties plan_json, plan_md (no arg), plan_state_json. init_run_layout is UNCHANGED. Callers updated by their own tasks (cluster A2): phases.py:80-81,120-121,231,256,264,282,297,315 (iteration methods, drop plan.id), phases.py:270 (git_violation — removed in phases rewrite), engine.py:372,374,587 (planset_json/plan_md — become plan_json/plan_md no-arg in engine task), engine.py:533 (conflict_fingerprint — deleted with merge code), engine.py:108-112/210-215 (plan_dir/plan_merge — deleted with sandbox/merge methods), plan_state.py (plan_state(id)->plan_state_json, plan_dir — cluster A3 task 1).

Apply:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from forge_mcp.state import durable_append, durable_replace, light_replace


@dataclass(frozen=True)
class RunLayout:
    """Single source of truth for every path under a forge-mcp run directory.

    Design: §10 all file paths for a run are derived from a single root so no
        caller hard-codes relative paths. With one plan per run the layout is
        flat: plan.json/plan.md/plan_state.json and iteration-<n>/ live at the
        run root (no plans/<id>/ nesting).
    Implementation: frozen dataclass with one field (root: Path); computed
        properties return concrete Paths; iteration artifacts are keyed only on
        n; class-method factory for_run wraps construction.
    Example: RunLayout.for_run(p).spec_md == p / 'spec.md'.
    """

    root: Path

    @classmethod
    def for_run(cls, run_dir: Path) -> RunLayout:
        """Return a RunLayout rooted at run_dir.

        Design: §10 callers construct layouts via this factory so the field name
            stays an implementation detail.
        Implementation: construct and return a new frozen instance.
        Example: RunLayout.for_run(Path('/runs/abc')).root == Path('/runs/abc').
        """
        return cls(root=run_dir)

    # ── top-level files ─────────────────────────────────────────

    @property
    def state_json(self) -> Path:
        """Return path to the run-level state file.

        Design: §10 state.json is the durable orchestrator checkpoint.
        Implementation: root / 'state.json'.
        Example: lay.state_json == run_dir / 'state.json'.
        """
        return self.root / "state.json"

    @property
    def run_log(self) -> Path:
        """Return path to the append-only run log.

        Design: §10 run.log captures structured orchestrator events.
        Implementation: root / 'run.log'.
        Example: lay.run_log == run_dir / 'run.log'.
        """
        return self.root / "run.log"

    # ── inputs/ ────────────────────────────────────────────

    @property
    def inputs_dir(self) -> Path:
        """Return path to the inputs subdirectory.

        Design: §10 immutable design inputs live under inputs/ to separate them
            from orchestrator-owned artifacts.
        Implementation: root / 'inputs'.
        Example: lay.inputs_dir == run_dir / 'inputs'.
        """
        return self.root / "inputs"

    @property
    def inputs_design(self) -> Path:
        """Return path to the immutable design document.

        Design: §10 design.md is written once at init and never overwritten.
        Implementation: inputs_dir / 'design.md'.
        Example: lay.inputs_design.name == 'design.md'.
        """
        return self.inputs_dir / "design.md"

    @property
    def design_fingerprint(self) -> Path:
        """Return path to the design fingerprint file.

        Design: §10 stores the SHA-256 hex digest of design.md for integrity checks.
        Implementation: inputs_dir / 'design.fingerprint'.
        Example: lay.design_fingerprint.name == 'design.fingerprint'.
        """
        return self.inputs_dir / "design.fingerprint"

    # ── spec files ─────────────────────────────────────────

    @property
    def spec_md(self) -> Path:
        """Return path to the orchestrator-owned spec document.

        Design: §10 spec.md evolves as amendments are accepted; distinct from
            the immutable inputs/design.md.
        Implementation: root / 'spec.md'.
        Example: lay.spec_md == run_dir / 'spec.md'.
        """
        return self.root / "spec.md"

    @property
    def spec_amendments(self) -> Path:
        """Return path to the append-only spec amendments log.

        Design: §10/I3 amendments are never replaced, only appended.
        Implementation: root / 'spec_amendments.md'.
        Example: lay.spec_amendments.name == 'spec_amendments.md'.
        """
        return self.root / "spec_amendments.md"

    @property
    def spec_fingerprint(self) -> Path:
        """Return path to the spec fingerprint file.

        Design: §10 mirrors design_fingerprint for the evolving spec.md.
        Implementation: root / 'spec.fingerprint'.
        Example: lay.spec_fingerprint.name == 'spec.fingerprint'.
        """
        return self.root / "spec.fingerprint"

    # ── plan (single, run-level) ──────────────────────────────

    @property
    def plan_json(self) -> Path:
        """Return path to the structured Plan JSON.

        Design: §10 plan.json holds the single structured Plan (was planset.json).
        Implementation: root / 'plan.json'.
        Example: lay.plan_json.name == 'plan.json'.
        """
        return self.root / "plan.json"

    @property
    def plan_md(self) -> Path:
        """Return path to the human-readable plan body document.

        Design: §10 plan.md holds the single plan's body (was plan-<id>.md);
            with one plan there is no id to disambiguate.
        Implementation: root / 'plan.md'.
        Example: lay.plan_md.name == 'plan.md'.
        """
        return self.root / "plan.md"

    @property
    def plan_state_json(self) -> Path:
        """Return path to the single run-level plan state file.

        Design: §10 plan_state.json is the plan-level PlanState checkpoint (was
            plans/<id>/state.json), now flattened to the run root.
        Implementation: root / 'plan_state.json'.
        Example: lay.plan_state_json.name == 'plan_state.json'.
        """
        return self.root / "plan_state.json"

    # ── per-iteration files (keyed on n) ──────────────────────────

    def iteration_dir(self, n: int) -> Path:
        """Return path to iteration directory n at the run root.

        Design: §10 iterations are numbered subdirs directly under the run root
            (no plans/<id>/ nesting); the dir name keeps the 'iteration-<n>' form.
        Implementation: root / f'iteration-{n}'.
        Example: lay.iteration_dir(3).name == 'iteration-3'.
        """
        return self.root / f"iteration-{n}"

    def contract(self, n: int) -> Path:
        """Return path to the iteration contract document.

        Design: §10 contract.md records what the Generator must implement.
        Implementation: iteration_dir(n) / 'contract.md'.
        Example: lay.contract(1).name == 'contract.md'.
        """
        return self.iteration_dir(n) / "contract.md"

    def summary(self, n: int) -> Path:
        """Return path to the iteration summary document.

        Design: §10 summary.md is the Generator's self-reported outcome.
        Implementation: iteration_dir(n) / 'summary.md'.
        Example: lay.summary(1).name == 'summary.md'.
        """
        return self.iteration_dir(n) / "summary.md"

    def eval(self, n: int) -> Path:
        """Return path to the iteration eval JSON.

        Design: §10 eval.json holds the Evaluator's structured result.
        Implementation: iteration_dir(n) / 'eval.json'.
        Example: lay.eval(1).name == 'eval.json'.
        """
        return self.iteration_dir(n) / "eval.json"

    def triage(self, n: int) -> Path:
        """Return path to the iteration triage JSON.

        Design: §10 triage.json records the orchestrator's triage decision.
        Implementation: iteration_dir(n) / 'triage.json'.
        Example: lay.triage(1).name == 'triage.json'.
        """
        return self.iteration_dir(n) / "triage.json"

    def gap_fingerprint(self, n: int) -> Path:
        """Return path to the iteration gap-fingerprint JSON.

        Design: §10 gap_fingerprint.json fingerprints open gaps to detect loops.
        Implementation: iteration_dir(n) / 'gap_fingerprint.json'.
        Example: lay.gap_fingerprint(1).name == 'gap_fingerprint.json'.
        """
        return self.iteration_dir(n) / "gap_fingerprint.json"

    def verify_txt(self, n: int) -> Path:
        """Return path to the iteration verify output text file.

        Design: §10 verify.txt captures raw verification stdout for diagnostics.
        Implementation: iteration_dir(n) / 'verify.txt'.
        Example: lay.verify_txt(1).name == 'verify.txt'.
        """
        return self.iteration_dir(n) / "verify.txt"


def init_run_layout(
    run_dir: Path,
    design_text: str,
    *,
    design_fingerprint: str,
) -> RunLayout:
    """Create the run directory structure and write immutable design + seeded spec.

    Design: §10/§3.1 design.md is frozen at run creation; spec.md starts as a
        copy of design and may evolve; spec_amendments.md is created empty as an
        append-only log.
    Implementation: make inputs/ with mode 0o700; write design.md and spec.md
        via durable_replace (crash-safe); write fingerprints via light_replace;
        create empty spec_amendments.md via durable_append (no-op append of '').
    Example: init_run_layout(p, 'hi', design_fingerprint='abc') leaves
        p/inputs/design.md == 'hi' and p/spec.md == 'hi'.
    """
    lay = RunLayout.for_run(run_dir)

    os.makedirs(lay.inputs_dir, mode=0o700, exist_ok=True)

    # Immutable design inputs
    durable_replace(lay.inputs_design, design_text)
    light_replace(lay.design_fingerprint, design_fingerprint)

    # Orchestrator-owned spec (seeded from design at init)
    durable_replace(lay.spec_md, design_text)
    light_replace(lay.spec_fingerprint, design_fingerprint)

    # Empty append-only amendments log
    durable_append(lay.spec_amendments, "")

    return lay


def ensure_iteration_dir(layout: RunLayout, n: int) -> Path:
    """Create and return the iteration directory for n with mode 0o700.

    Design: §10 iteration dirs are created on demand so the orchestrator does
        not pre-allocate all directories at run start; keyed only on n now that
        there is a single plan.
    Implementation: os.makedirs with exist_ok=True so repeated calls are safe;
        mode 0o700 restricts access to the owner only.
    Example: ensure_iteration_dir(lay, 1) returns lay.iteration_dir(1) and
        guarantees the directory exists on disk.
    """
    path = layout.iteration_dir(n)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_artifacts.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/artifacts.py' 'tests/test_artifacts.py'
git commit -m "refactor(artifacts): flatten run layout to a single plan\n\nKey iteration artifacts on n only (dir still iteration-<n> at run root),\nadd plan_json/plan_md/plan_state_json, and remove the planset, per-plan\ndir, merge, conflict, and git-violation paths per spec 0003 \u00a710.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: statemachine.py — collapse RunState/_LEGAL_EDGES to init→planning→executing→finalizing→{terminal}; drop dead wave field

**Files:**
- Modify: `src/forge_mcp/orchestrator/statemachine.py`
- Test: `tests/test_statemachine.py`

**Interfaces:**
- Consumes: RunLayout.state_json and RunLayout.root from artifacts.py; forge_mcp.state.write_json(path, model, *, durable).
- Produces: RunState = Literal['init','planning','executing','finalizing','completed','incomplete','failed'] (no scheduling/merging/amending/verifying). RunStatePayload(extra='forbid') fields {state, last_phase:str|None, last_updated_at:str, run_dir:str='', iterations:int=0} (NO 'wave'). RunStateMachine(layout) with .payload property and .transition(to:str, *, now:str). Legal edges: init→planning→executing→finalizing→{completed,incomplete,failed}; 'failed' reachable from any non-terminal.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_statemachine.py`:

```python
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from forge_mcp.artifacts import RunLayout
from forge_mcp.orchestrator.statemachine import (
    RunState,
    RunStateMachine,
    RunStatePayload,
)


def test_legal_single_plan_path(tmp_path):
    """Design: §9 the run advances init->planning->executing->finalizing->terminal.
    Implementation: drive the full legal chain and read back the final state.
    Example: state.json reflects 'completed' at the end.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in ["planning", "executing", "finalizing", "completed"]:
        sm.transition(to, now="t")
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "completed"


def test_failed_reachable_from_any_nonterminal(tmp_path):
    """Design: §9 'failed' is reachable from any non-terminal state.
    Implementation: transition init->planning then planning->failed directly.
    Example: state becomes 'failed' without passing through finalizing.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    sm.transition("planning", now="t")
    sm.transition("failed", now="t")
    assert sm.payload.state == "failed"


def test_removed_states_are_illegal(tmp_path):
    """Design: §9 scheduling/merging/amending/verifying edges are removed.
    Implementation: planning->scheduling and executing->merging both raise.
    Example: no path threads the old wave-cycle states.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    sm.transition("planning", now="t")
    with pytest.raises(ValueError):
        sm.transition("scheduling", now="t")
    sm.transition("executing", now="t")
    with pytest.raises(ValueError):
        sm.transition("merging", now="t")


def test_illegal_skip_raises(tmp_path):
    """Design: §9 illegal edges are rejected (single source of legal ordering).
    Implementation: jump init->finalizing.
    Example: raises ValueError.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    with pytest.raises(ValueError):
        sm.transition("finalizing", now="t")


def test_terminal_cannot_advance(tmp_path):
    """Design: §9 terminal states do not advance.
    Implementation: reach 'incomplete' then attempt another transition.
    Example: transitioning out of a terminal state raises ValueError.
    """
    sm = RunStateMachine(RunLayout.for_run(tmp_path))
    for to in ["planning", "executing", "finalizing", "incomplete"]:
        sm.transition(to, now="t")
    with pytest.raises(ValueError):
        sm.transition("completed", now="t")


def test_wave_field_removed():
    """Design: §9 the dead RunStatePayload.wave field is removed.
    Implementation: the model has no 'wave' field and extra='forbid' rejects it.
    Example: passing wave=0 raises ValidationError.
    """
    assert "wave" not in RunStatePayload.model_fields
    assert "scheduling" not in RunState.__args__
    with pytest.raises(ValidationError):
        RunStatePayload(state="init", last_phase=None, last_updated_at="t", wave=0)
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_statemachine.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

From src/forge_mcp/orchestrator/statemachine.py: (1) remove the 'scheduling','merging','amending','verifying' members from the RunState Literal (current lines 14,16,17,18). (2) collapse _LEGAL_EDGES (current lines 28-37) so it contains only init->{planning}, planning->{executing}, executing->{finalizing}, finalizing->{completed,incomplete,failed}; delete the scheduling/merging/amending/verifying edge entries. (3) remove the 'wave: int = 0' field from RunStatePayload (current line 58). (4) update the RunStatePayload docstring Example (current line 50) to drop 'wave=0' and update RunStateMachine class/__init__/transition docstrings that describe the old graph. The only caller is the engine, whose transition sites are rewritten to the new chain in the engine task (cluster A2) in the same change — no other module reads these states.

Apply:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from forge_mcp.artifacts import RunLayout
from forge_mcp.state import write_json

# All valid state names for the single-plan, direct-edit run (§9).
RunState = Literal[
    "init",
    "planning",
    "executing",
    "finalizing",
    "completed",
    "incomplete",
    "failed",
]

_TERMINAL_STATES: frozenset[str] = frozenset({"completed", "incomplete", "failed"})

# Explicit legal adjacency map (non-failed edges). The wave-cycle states
# (scheduling/merging/amending/verifying) are gone: the run plans once, runs one
# plan loop, then finalizes.
_LEGAL_EDGES: dict[str, frozenset[str]] = {
    "init": frozenset({"planning"}),
    "planning": frozenset({"executing"}),
    "executing": frozenset({"finalizing"}),
    "finalizing": frozenset({"completed", "incomplete", "failed"}),
}


class RunStatePayload(BaseModel, extra="forbid"):
    """Durable checkpoint for a forge-mcp run.

    Design: §9 this payload is the sole content of run-level state.json; all
        fields are explicit so an unexpected key from a corrupt write is caught
        at parse time via extra='forbid'. The dead 'wave' field (write-only,
        never read — there are no waves) is removed.
    Implementation: Pydantic BaseModel with a Literal state field; last_phase
        records the FROM-state of the most recent non-terminal transition so
        that failure attribution is meaningful.
    Example: RunStatePayload(state='init', last_phase=None,
        last_updated_at='t', run_dir='/r', iterations=0).
    """

    state: RunState
    last_phase: str | None
    last_updated_at: str
    run_dir: str = ""
    iterations: int = 0


class RunStateMachine:
    """Single writer of run-level state.json (Invariant I1).

    Design: §9 the orchestrator advances the run through the collapsed state
        graph init->planning->executing->finalizing->terminal; illegal edges are
        rejected to prevent the run from entering an undefined state.
    Implementation: holds an in-memory RunStatePayload; each transition
        validates the requested edge against _LEGAL_EDGES, updates the payload,
        model_validates, and durably writes to RunLayout.state_json.
    Example: sm = RunStateMachine(layout); sm.transition('planning', now='t').
    """

    def __init__(self, layout: RunLayout) -> None:
        """Initialise the state machine in the 'init' state and write state.json.

        Design: §9 start state is always 'init' so recovery tools can detect an
            un-started run by inspecting state.json.
        Implementation: build a RunStatePayload at 'init', write it durably, and
            store layout for later transitions.
        Example: RunStateMachine(layout).payload.state == 'init'.
        """
        self._layout = layout
        self._payload = RunStatePayload(
            state="init",
            last_phase=None,
            last_updated_at="",
            run_dir=str(layout.root),
        )
        write_json(layout.state_json, self._payload, durable=True)

    @property
    def payload(self) -> RunStatePayload:
        """Return the current in-memory payload (read-only view).

        Design: §9 exposes the payload for inspection without granting write
            access; the only mutation path is transition().
        Implementation: return the private attribute directly.
        Example: sm.payload.state == 'init' after construction.
        """
        return self._payload

    def transition(self, to: str, *, now: str) -> None:
        """Advance the run to state *to* and durably write state.json.

        Design: §9 legal edges are validated against an explicit adjacency map;
            'failed' is reachable from any non-terminal; terminal states do not
            advance last_phase so failure attribution is preserved.
        Implementation: look up legal neighbours for the current state; raise
            ValueError if *to* is not among them (and is not 'failed'); update
            state, conditionally update last_phase, set last_updated_at,
            model_validate, then durably write.
        Example: sm.transition('planning', now='2024-01-01T00:00:00Z') moves the
            run from 'init' to 'planning'.
        """
        current = self._payload.state

        if current in _TERMINAL_STATES:
            raise ValueError(f"Cannot transition from terminal state '{current}' to '{to}'.")

        allowed = _LEGAL_EDGES.get(current, frozenset())
        # 'failed' is reachable from any non-terminal state.
        if to != "failed" and to not in allowed:
            raise ValueError(
                f"Illegal transition: '{current}' -> '{to}'. "
                f"Allowed: {sorted(allowed | {'failed'})}."
            )

        # Advance last_phase only when transitioning to a non-terminal state.
        new_last_phase = self._payload.last_phase
        if to not in _TERMINAL_STATES:
            new_last_phase = current

        self._payload = RunStatePayload.model_validate(
            {
                **self._payload.model_dump(),
                "state": to,
                "last_phase": new_last_phase,
                "last_updated_at": now,
            }
        )
        write_json(self._layout.state_json, self._payload, durable=True)
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_statemachine.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/statemachine.py' 'tests/test_statemachine.py'
git commit -m "refactor(statemachine): collapse to single-plan run graph; drop wave\n\nReduce RunState/_LEGAL_EDGES to init->planning->executing->finalizing->\n{completed,incomplete,failed} ('failed' from any non-terminal) and\nremove the dead RunStatePayload.wave field per spec 0003 \u00a79.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: lifecycle.py — single-plan projection (keep honest-gap projection; drop multi-plan-only prose)

**Files:**
- Modify: `src/forge_mcp/orchestrator/lifecycle.py`
- Test: `tests/test_lifecycle.py`

**Interfaces:**
- Consumes: forge_mcp.models.{EvalGap, GapSummary, RunResult} (unchanged).
- Produces: PlanReport dataclass {plan_id:str, terminal_state:str, gaps:list[EvalGap], synthesized:list[GapSummary], failure_reason:str|None}. synthesized_gap_for_failed_plan(plan_id, reason) -> GapSummary. project_unresolved_gaps(non_completed: list[PlanReport]) -> list[GapSummary]. build_run_result(*, status, run_dir, iterations, non_completed, stop_reason, verified, summary, failure_kind=None) -> RunResult. All signatures UNCHANGED so the single-plan engine passes a one-element non_completed list.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_lifecycle.py`:

```python
from __future__ import annotations

from forge_mcp.models import EvalGap, GapSummary
from forge_mcp.orchestrator.lifecycle import (
    PlanReport,
    build_run_result,
    project_unresolved_gaps,
    synthesized_gap_for_failed_plan,
)


def _gap(t):
    """Build an EvalGap fixture with the given title.

    Design: test helper to reduce boilerplate in lifecycle test cases.
    Implementation: construct EvalGap with fixed fields except the title.
    Example: _gap('g1') yields an EvalGap with title='g1'.
    """
    return EvalGap(
        title=t,
        severity="high",
        design_doc_section="§7.4",
        current_state="c",
        expected_state="e",
        suggested_fix="f",
    )


def test_failed_plan_gets_synthesized_gap():
    """Design: §8 a failed plan with no gaps contributes a synthesized failure gap.
    Implementation: build the synthesized GapSummary.
    Example: title names the plan; section is a sentinel.
    """
    g = synthesized_gap_for_failed_plan("plan", "crashed: OSError")
    assert "plan" in g.title and g.design_doc_section


def test_single_plan_projection_keeps_freshest_gaps():
    """Design: §8 the one non-completed plan contributes its freshest full gap set.
    Implementation: one PlanReport with an eval gap plus a synthesized verify gap.
    Example: both 'g1' and 'verify failed' appear as GapSummary rows.
    """
    reports = [
        PlanReport(
            "plan",
            "incomplete",
            [_gap("g1")],
            [GapSummary(title="verify failed", severity="high", design_doc_section="§6.5")],
            None,
        ),
    ]
    rows = project_unresolved_gaps(reports)
    titles = {r.title for r in rows}
    assert {"g1", "verify failed"} <= titles


def test_completed_run_has_no_unresolved_gaps():
    """Design: §8 a completed single-plan run has an empty non_completed list.
    Implementation: build_run_result with non_completed=[] and verified True.
    Example: status 'completed', verified True, no unresolved gaps.
    """
    r = build_run_result(
        status="completed",
        run_dir="/r",
        iterations=1,
        non_completed=[],
        stop_reason=None,
        verified=True,
        summary="done",
    )
    assert r.status == "completed" and r.verified is True
    assert r.unresolved_gaps == [] and r.failure_kind is None


def test_build_run_result_incomplete():
    """Design: §8 a non-convergent run is 'incomplete' with stop_reason + gaps.
    Implementation: build and read anchors for the single non-completed plan.
    Example: status incomplete, verified False, gap 'g1' present.
    """
    r = build_run_result(
        status="incomplete",
        run_dir="/r",
        iterations=5,
        non_completed=[PlanReport("plan", "incomplete", [_gap("g1")], [], None)],
        stop_reason="non-progress",
        verified=False,
        summary="incomplete",
    )
    assert r.status == "incomplete" and r.stop_reason == "non-progress"
    assert r.unresolved_gaps[0].title == "g1"


def test_failure_kind_only_on_failed_status():
    """Design: §8 failure_kind is set ONLY when status='failed' (orchestrator-internal error).
    Implementation: pass failure_kind for both incomplete and failed and compare.
    Example: incomplete drops failure_kind; failed keeps it.
    """
    inc = build_run_result(
        status="incomplete",
        run_dir="/r",
        iterations=1,
        non_completed=[PlanReport("plan", "incomplete", [], [], None)],
        stop_reason="cap",
        verified=False,
        summary="x",
        failure_kind="should-be-dropped",
    )
    assert inc.failure_kind is None
    failed = build_run_result(
        status="failed",
        run_dir="/r",
        iterations=0,
        non_completed=[],
        stop_reason="boom",
        verified=False,
        summary="x",
        failure_kind="internal",
    )
    assert failed.failure_kind == "internal"
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_lifecycle.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

From src/forge_mcp/orchestrator/lifecycle.py the function signatures and return types are UNCHANGED (the engine still passes a list, now one-element). Only prose/sentinels that encode multi-plan assumptions change: (1) module docstring (lines 1-5): replace 'per-plan terminal reports' with 'the single plan's terminal report' and cite §8 instead of §6.4/§4.3. (2) _FAILURE_SENTINEL_SECTION value (line 14): '§6.4-failure' -> '§8-failure' (the old §6.4 reference is from 0001; the projection section is now §8 of 0003). (3) PlanReport docstring (lines 18-26): drop the multi-plan 'each non-completed plan contributes' framing; describe the single plan. (4) project_unresolved_gaps docstring (lines 70-80): remove 'Union over all non-completed plans', 'NOT collapsed across plans', 'two plans with distinct gaps', and the I7 multi-plan reference; describe the single-plan list-shaped projection. (5) build_run_result docstring (lines 103-113): remove the plural 'non-completed plans' framing. No code logic changes — the loop already handles a 0- or 1-element list correctly. The engine task (cluster A2) builds the single PlanReport from the one PlanLoopResult.

Apply:

```python
"""Terminal honesty and result projection for forge-mcp orchestrator.

Pure projection functions — no I/O. Transforms the single plan's terminal
report into a RunResult with honest unresolved_gaps (§8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_mcp.models import EvalGap, GapSummary, RunResult

# Sentinel design_doc_section used for synthesized failure gaps (§8).
_FAILURE_SENTINEL_SECTION = "§8-failure"


@dataclass
class PlanReport:
    """Carries the terminal state and freshest gap set for the plan when it did not complete.

    Design: §8 a non-completed run contributes the plan's freshest full
        post-synthesize gap set (eval gaps ∪ synthesized verify gap) to the
        RunResult; plan_id is retained as a stable label for the synthesized
        failure gap even though there is only one plan.
    Implementation: plain dataclass; no I/O; consumed by project_unresolved_gaps.
    Example: PlanReport('plan', 'incomplete', [EvalGap(...)], [], None).
    """

    plan_id: str
    terminal_state: str
    gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    failure_reason: str | None = None


def synthesized_gap_for_failed_plan(plan_id: str, reason: str) -> GapSummary:
    """Build a synthesized failure GapSummary for a failed plan with no gap set.

    Design: §8 a failed plan that has no eval gaps or synthesized gaps must still
        contribute a visible gap so the caller is never silently absent of
        failure information.
    Implementation: title names the plan_id and includes the reason; severity is
        hard-coded 'high'; design_doc_section is a fixed sentinel so the
        non-optional field is always populated.
    Example: synthesized_gap_for_failed_plan('plan', 'crashed: OSError') returns
        a GapSummary whose title contains 'plan' and design_doc_section is set.
    """
    return GapSummary(
        title=f"Plan {plan_id} failed: {reason}",
        severity="high",
        design_doc_section=_FAILURE_SENTINEL_SECTION,
    )


def _eval_gap_to_summary(gap: EvalGap) -> GapSummary:
    """Project an EvalGap to a GapSummary by copying the shared fields.

    Design: §8 EvalGap carries implementation detail fields not needed in
        RunResult; only title, severity, and design_doc_section are projected.
    Implementation: construct GapSummary from the three shared fields.
    Example: _eval_gap_to_summary(EvalGap(title='t', ...)) returns
        GapSummary(title='t', ...).
    """
    return GapSummary(
        title=gap.title,
        severity=gap.severity,
        design_doc_section=gap.design_doc_section,
    )


def project_unresolved_gaps(non_completed: list[PlanReport]) -> list[GapSummary]:
    """Project the non-completed plan report(s) into the RunResult gap rows.

    Design: §8 the single-plan run passes at most one PlanReport here; the
        function stays list-shaped so build_run_result has one code path whether
        the plan completed (empty list) or not. Each report contributes its
        freshest full gap set — the per-iteration freshest set, NOT aggregated
        across iterations; gaps are not deduped (the projection is honest about
        every distinct row).
    Implementation: for each PlanReport, project its EvalGaps to GapSummary via
        {title, severity, design_doc_section}, then extend with its synthesized
        GapSummaries; for a 'failed' report with no eval gaps AND no synthesized
        gaps, contribute a synthesized_gap_for_failed_plan.
    Example: one incomplete PlanReport with one eval gap and one synthesized gap
        yields two GapSummary rows.
    """
    result: list[GapSummary] = []
    for report in non_completed:
        eval_summaries = [_eval_gap_to_summary(g) for g in report.gaps]
        plan_gaps = eval_summaries + list(report.synthesized)
        if not plan_gaps and report.terminal_state == "failed":
            reason = report.failure_reason or "unknown reason"
            plan_gaps = [synthesized_gap_for_failed_plan(report.plan_id, reason)]
        result.extend(plan_gaps)
    return result


def build_run_result(
    *,
    status: str,
    run_dir: str,
    iterations: int,
    non_completed: list[PlanReport],
    stop_reason: str | None,
    verified: bool,
    summary: str,
    failure_kind: str | None = None,
) -> RunResult:
    """Assemble a RunResult from orchestrator state and projected gap data.

    Design: §8 the RunResult must be an honest summary of what happened;
        unresolved_gaps are derived from the non-completed plan (empty when the
        plan completed); failure_kind is set ONLY when status='failed' (an
        orchestrator-internal error), never for plan non-convergence.
    Implementation: call project_unresolved_gaps to derive unresolved_gaps; pass
        failure_kind through only when status='failed'; construct RunResult.
    Example: build_run_result(status='incomplete', ...) yields a RunResult whose
        unresolved_gaps come from non_completed and failure_kind=None.
    """
    unresolved_gaps = project_unresolved_gaps(non_completed)
    resolved_failure_kind = failure_kind if status == "failed" else None
    return RunResult(
        status=status,  # type: ignore[arg-type]
        run_dir=run_dir,
        iterations=iterations,
        unresolved_gaps=unresolved_gaps,
        failure_kind=resolved_failure_kind,
        stop_reason=stop_reason,
        verified=verified,
        summary=summary,
    )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_lifecycle.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/lifecycle.py' 'tests/test_lifecycle.py'
git commit -m "refactor(lifecycle): single-plan gap projection\n\nKeep the honest-gap projection (build_run_result / PlanReport /\nproject_unresolved_gaps) list-shaped for one plan and drop the\nmulti-plan-only prose and \u00a76.4 sentinel per spec 0003 \u00a78.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Rewrite run_plan_loop for the §4 single-plan, direct-edit loop (target_dir, in-loop amendment, no git backstop, no change_set)

**Files:**
- Modify: `src/forge_mcp/orchestrator/phases.py`
- Test: `tests/test_phases.py`

**Interfaces:**
- Consumes: RunLayout + flat artifact methods (no plan_id): layout.contract(n), layout.summary(n), layout.eval(n), layout.triage(n), layout.gap_fingerprint(n), layout.verify_txt(n), layout.run_log, layout.spec_md, layout.spec_amendments, ensure_iteration_dir(layout, n) [A-artifacts §10]. PlanState(layout) constructor with no plan_id/sandbox_path [A-plan_state §9], plus .bump_iteration(now), .set_state(state, now=), .record_completed(n, now); PlanStateValue still includes generating/verifying/evaluating/triaging/remediating/done/incomplete/failed (awaiting_amendment dropped). run_generator(runner, *, contract_text, target_dir, surface, run_log_path=None) [A-generator §5/§15]. run_evaluator(runner, *, spec_text, eval_schema, cwd, run_log_path=None) -> EvalResult (sandbox param dropped) [A-drivers §6]. run_triage(runner, *, spec_text, eval_result, triage_schema, cwd, run_log_path=None) -> TriageResult (already cwd-only). apply_amendments(layout, *, spec_text, spec_fingerprint, proposed: list[GapTriage], now) -> AmendOutcome with .new_spec/.new_fingerprint/.churn_fingerprint, and spec_amendments.md entry carrying NO plan_id field [A-amend §11/§15]. run_verification(command, cwd) -> VerifyOutcome(passed, output). Plan(surface, verification_command, body) extra='forbid', no id/depends_on/file_scope [A-models §3]. fingerprint, detect_non_progress (window=2; EARLY_STOP needs >=4 equal fps); effective_code_bug_titles, passes_citation_gate; GapSummary(title, severity, design_doc_section); EvalGap fields title/severity/suggested_fix.
- Produces: async def run_plan_loop(*, layout: RunLayout, plan: Plan, target_dir: Path, spec_text: str, spec_fingerprint: str, claude_runner: ClaudeRunner, codex_runner: CodexRunner, schemas: dict, max_iterations: int) -> PlanLoopResult. @dataclass PlanLoopResult(terminal_state: Literal['done','incomplete','failed'], iterations: int, last_gaps: list[EvalGap]=[], synthesized: list[GapSummary]=[], stop_reason: str|None=None) — change_set and proposed_amendment fields REMOVED. Module-level helpers: _now()->str, _seed_contract(plan, layout, n)->str, _write_remediation_contract(plan, layout, n, *, gaps, synthesized, nudge)->str, _synthesize_blocking_gaps(*, last_verification, verification_command)->list[GapSummary] (verify gap only, no git branch/git_diff param), _validated_amendment_row(triages, spec_text)->GapTriage|None. Constant _VERIFY_SENTINEL_SECTION='§6.5'. Consumed by orchestrator/engine.py (A-engine), which imports run_plan_loop + PlanLoopResult and reads .terminal_state/.iterations/.last_gaps/.synthesized/.stop_reason.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_phases.py`:

```python
# tests/test_phases.py
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from forge_mcp.artifacts import init_run_layout
from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.models import Plan
from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured

# A spec whose body contains a verbatim, citable, amendable target so the
# in-loop amendment path can re-validate the citation and rewrite spec.md.
SPEC = (
    "Alpha section. The widget must flush before close. "
    "It must flush before close in all cases. Omega section."
)


def _plan(**kw):
    """Build a single-plan Plan fixture with defaults, overridable via kwargs.

    Design: §3/§4 the collapsed Plan carries only surface, verification_command,
        and body — there is no id/depends_on/file_scope — so the loop tests build
        from exactly those fields.
    Implementation: start from a default backend plan dict, apply overrides, and
        construct a Plan (extra='forbid' rejects any stray legacy field).
    Example: _plan(verification_command='false') yields a plan with that command.
    """
    base = dict(surface="backend", verification_command=None, body="do X")
    base.update(kw)
    return Plan(**base)  # type: ignore[arg-type]


def _eval(no_gaps: bool, gaps: list[dict] | None = None) -> dict:
    """Build an EvalResult payload dict for a scripted FakeClaudeRunner result.

    Design: §4 the evaluator output drives the completion gate; tests script a
        clean (no_gaps) or gapful evaluator turn without the real SDK.
    Implementation: return the minimal EvalResult dict with no_gaps, a gaps list,
        and a summary string.
    Example: _eval(True) returns {'no_gaps': True, 'gaps': [], 'summary': 'ok'}.
    """
    return {"no_gaps": no_gaps, "gaps": gaps or [], "summary": "ok"}


def _gap(title: str = "broken thing") -> dict:
    """Build one EvalGap payload dict for a scripted evaluator turn.

    Design: §4 a gapful evaluator turn forces triage and (when undemoted) blocks
        completion; tests supply a concrete code-bug gap.
    Implementation: return an EvalGap dict with all required fields populated.
    Example: _gap('broken thing')['title'] == 'broken thing'.
    """
    return {
        "title": title,
        "severity": "high",
        "design_doc_section": "§1",
        "current_state": "broken",
        "expected_state": "fixed",
        "suggested_fix": "fix it",
    }


def _amend_triage() -> dict:
    """Build a TriageResult payload that demotes the gap to a design fault + amendment.

    Design: §4 step 8 a cited design-fault triage carrying a proposed_amendment is
        applied to spec.md in-loop; the citation and 'before' text both appear in
        SPEC so the row passes the citation gate and the amendment applies.
    Implementation: return a TriageResult dict with one design_fault triage whose
        cited_sections and proposed_amendment.before are verbatim spec substrings.
    Example: _amend_triage()['triages'][0]['design_fault'] is True.
    """
    cite = "The widget must flush before close"
    return {
        "triages": [
            {
                "gap_title": "broken thing",
                "design_fault": True,
                "fault_kind": "contradiction",
                "cited_sections": [cite],
                "explanation": "spec is wrong",
                "proposed_amendment": {
                    "cited_sections": [cite],
                    "before": "must flush before close",
                    "after": "must flush and fsync before close",
                    "rationale": "fsync is required",
                },
            }
        ]
    }


@pytest.mark.driver
async def test_clean_iteration_completes_direct_edit(tmp_path: Path):
    """Design: §4 no gaps + no verify cmd -> done; the loop edits target_dir directly.

    Implementation: seed the layout, point target_dir at a writable dir, script a
        clean evaluator turn; assert done and that the generator ran rooted at
        target_dir (no sandbox copy).
    Example: terminal_state 'done', iterations 1.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    written: list[str] = []
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: written.append(cwd),
    )
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3,
    )
    assert res.terminal_state == "done"
    assert res.iterations == 1
    # The generator ran rooted at target_dir (direct edit, no sandbox copy).
    assert written == [str(target)]


def test_plan_loop_result_has_no_change_set_or_amendment_fields():
    """Design: §4 PlanLoopResult drops change_set and proposed_amendment.

    Implementation: inspect the dataclass fields; assert the removed names are
        absent and the surviving fields are exactly the new set.
    Example: 'change_set' not in field names.
    """
    names = {f.name for f in dataclasses.fields(PlanLoopResult)}
    assert "change_set" not in names
    assert "proposed_amendment" not in names
    assert names == {
        "terminal_state",
        "iterations",
        "last_gaps",
        "synthesized",
        "stop_reason",
    }


@pytest.mark.driver
async def test_verify_failure_synthesizes_only_verify_gap(tmp_path: Path):
    """Design: §6.5 a failing verification synthesizes a non-demotable §6.5 gap, blocks done.

    Implementation: verification_command 'false' with a clean evaluator -> not
        done; the single synthesized gap is the §6.5 verify gap (no §9 git gap).
    Example: terminal_state 'incomplete', synthesized has exactly the §6.5 gap.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(verification_command="false"),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
    )
    assert res.terminal_state == "incomplete"
    assert [g.design_doc_section for g in res.synthesized] == ["§6.5"]
    assert layout.verify_txt(1).exists()


@pytest.mark.driver
async def test_verify_pass_with_no_gaps_completes(tmp_path: Path):
    """Design: §4 verify passing + no gaps -> done (two-conjunct gate satisfied).

    Implementation: verification_command 'true' (exit 0) with a clean evaluator.
    Example: terminal_state 'done', no synthesized gaps.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(verification_command="true"),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
    )
    assert res.terminal_state == "done"
    assert res.synthesized == []


@pytest.mark.driver
async def test_in_loop_amendment_rewrites_spec_and_continues(tmp_path: Path):
    """Design: §4 step 8 a validated design-fault triage rewrites spec.md in-loop and continues.

    Implementation: iteration 1 evaluator finds a gap, triage demotes it to a
        cited design fault with an amendment; iteration 2 evaluator is clean so
        the loop completes. Assert spec.md gained the amended text, an amendments
        log entry was written with no plan_id field, and done at iteration 2.
    Example: spec.md contains 'fsync'; terminal_state 'done', iterations 2.
    """
    import json

    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_eval(False, [_gap()])),
            structured(_amend_triage()),
            structured(_eval(True)),
        ]
    )
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 2)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3,
    )
    assert res.terminal_state == "done"
    assert res.iterations == 2
    # The amendment durably rewrote spec.md against the current spec_text.
    assert "fsync" in layout.spec_md.read_text()
    # The amendments log entry carries no plan_id field (single-plan).
    log = layout.spec_amendments.read_text().strip().splitlines()
    assert log, "expected one amendment log entry"
    entry = json.loads(log[-1])
    assert "plan_id" not in entry
    assert entry["after"] == "must flush and fsync before close"


@pytest.mark.driver
async def test_unresolved_code_bug_hits_iteration_cap(tmp_path: Path):
    """Design: §4 a persistent undemoted code bug never completes -> incomplete at the cap.

    Implementation: every iteration the evaluator reports the same gap and triage
        does NOT demote it (empty triages list); max_iterations=2 forces the cap.
    Example: terminal_state 'incomplete', stop_reason 'iteration cap'.
    """
    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_eval(False, [_gap()])),
            structured({"triages": []}),
            structured(_eval(False, [_gap()])),
            structured({"triages": []}),
        ]
    )
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 2)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=2,
    )
    assert res.terminal_state == "incomplete"
    assert res.stop_reason == "iteration cap"
    assert res.iterations == 2


@pytest.mark.driver
async def test_generator_failure_isolated_as_failed(tmp_path: Path):
    """Design: §4 an unhandled error in the loop returns a 'failed' report, not a raise.

    Implementation: a codex runner whose on_generate raises forces the loop's
        failure-isolation path.
    Example: terminal_state 'failed', stop_reason names the exception.
    """

    def _boom(_cwd: str) -> None:
        """Raise to simulate a generator failure inside the loop.

        Design: §4 exercises the failure-isolation branch.
        Implementation: unconditionally raise RuntimeError.
        Example: _boom('/x') raises RuntimeError.
        """
        raise RuntimeError("codex exploded")

    layout = init_run_layout(tmp_path / "run", SPEC, design_fingerprint="fp0")
    target = tmp_path / "target"
    target.mkdir()
    claude = FakeClaudeRunner([structured(_eval(True))])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})], on_generate=_boom)
    res = await run_plan_loop(
        layout=layout,
        plan=_plan(),
        target_dir=target,
        spec_text=SPEC,
        spec_fingerprint="fp0",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=2,
    )
    assert res.terminal_state == "failed"
    assert "codex exploded" in (res.stop_reason or "")
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_phases.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Rewrite the whole file (new_code is the complete replacement). Relative to the current phases.py: (1) Module docstring lines 1-7 — drop 'isolated in its sandbox', 'consumed by the scheduler', 'wave's frozen spec'; replace with the single-plan/direct-edit wording. (2) Imports — DELETE `from forge_mcp.gitguard import capture_state, diff_state` (line 20) and `from forge_mcp.sandbox import Change, capture_manifest, detect_changes` (line 23); ADD `from forge_mcp.orchestrator.amend import apply_amendments`. (3) Constant `_GIT_SENTINEL_SECTION = "§9"` (line 33) — DELETE; keep `_VERIFY_SENTINEL_SECTION`. (4) PlanLoopResult — REMOVE fields `proposed_amendment` (line 54) and `change_set` (line 56); narrow `terminal_state` Literal to drop `"awaiting_amendment"`; update the docstring. (5) `_synthesize_blocking_gaps` — REMOVE the `git_diff: str` parameter and the entire `if git_diff:` §9 branch (lines 143-150) and the `_GIT_SENTINEL_SECTION` use; keep only the §6.5 verify branch. (6) `run_plan_loop` signature — REMOVE params `sandbox: Path`, `base_git_state: str | None`, `git_surface: Path | None = None`; RENAME the working dir to `target_dir: Path`; ADD `spec_fingerprint: str`. (7) Loop body — DELETE `baseline = capture_manifest(sandbox)` (line 223); DELETE the git-backstop step (lines 266-270: `end_git_state`, `git_diff`, `layout.git_violation` write); change `PlanState(layout, plan_id=plan.id, sandbox_path=str(sandbox))` (line 221) to `PlanState(layout)`; change `run_generator(..., sandbox=sandbox, ...)` to `target_dir=target_dir`; change `run_evaluator(..., sandbox=sandbox, cwd=sandbox, ...)` to drop `sandbox=` and use `cwd=target_dir`; change `run_triage(..., cwd=sandbox, ...)` to `cwd=target_dir`; change every artifact call to the flat form (`layout.contract(plan.id, n)`→`layout.contract(n)`, same for `summary`/`verify_txt`/`eval`/`triage`/`gap_fingerprint`) and `ensure_iteration_dir(layout, plan.id, n)`→`ensure_iteration_dir(layout, n)`; in `_seed_contract`/`_write_remediation_contract` drop `plan.id` from their artifact calls; change the summary write to `light_replace(layout.summary(n), f"Iteration {n} generated.")`. (8) Step 9 amendment block (current lines 321-331) — REPLACE the `awaiting_amendment` early-return with the in-loop apply: `apply_amendments(layout, ..., proposed=[amendment_row], ...)`, rebind `spec_text`/`spec_fingerprint`, append `outcome.churn_fingerprint` to a new `amendment_history`, `detect_non_progress(amendment_history)==EARLY_STOP`→incomplete 'amendment thrash', else honor cap (incomplete 'iteration cap') then `continue`. (9) Completion gate (current lines 333-345) — REMOVE `no_git_violation` and `no_pending_amend` and the `git_diff` reference; gate becomes `if effective_no_gaps and verify_passed:` and the done return drops `change_set=detect_changes(...)`. (10) Add the `amendment_history` local. No other behavior changes.

Apply:

```python
"""Single-plan iteration loop driving the §4 phase order and §6.5 verify gate.

run_plan_loop runs the one plan's iteration loop directly on target_dir against
the current spec.md, applying validated design-fault amendments in-loop. It is
consumed by the single-plan engine (§8). The loop is wrapped in failure
isolation so an unhandled error returns a `failed` report rather than
propagating into the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from forge_mcp.artifacts import RunLayout, ensure_iteration_dir
from forge_mcp.convergence import detect_non_progress, fingerprint
from forge_mcp.drivers.evaluator import run_evaluator, run_triage
from forge_mcp.drivers.generator import run_generator
from forge_mcp.models import EvalGap, GapSummary, GapTriage, Plan
from forge_mcp.orchestrator.amend import apply_amendments
from forge_mcp.orchestrator.plan_state import PlanState
from forge_mcp.state import light_replace, write_json
from forge_mcp.triage import effective_code_bug_titles, passes_citation_gate
from forge_mcp.verifier import VerifyOutcome, run_verification

if TYPE_CHECKING:
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner

# Sentinel design_doc_section value for the non-demotable synthesized verify gap (§6.5).
_VERIFY_SENTINEL_SECTION = "§6.5"


@dataclass
class PlanLoopResult:
    """Terminal report from the single plan's §4 iteration loop.

    Design: §4 the loop reports a single terminal state plus the freshest full
        post-synthesize gap set so the engine can project honest unresolved
        gaps; with direct edits there is no change_set and amendments are
        applied in-loop, so the proposed_amendment hand-off field is gone too.
    Implementation: plain dataclass; terminal_state is one of done / incomplete
        / failed (no awaiting_amendment); last_gaps and synthesized carry the
        freshest gap set; stop_reason explains a non-done stop.
    Example: PlanLoopResult(terminal_state='done', iterations=1).
    """

    terminal_state: Literal["done", "incomplete", "failed"]
    iterations: int
    last_gaps: list[EvalGap] = field(default_factory=list)
    synthesized: list[GapSummary] = field(default_factory=list)
    stop_reason: str | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §4 PlanState mutations record a last_updated_at timestamp; the loop
        is the sole writer of plan state so it supplies the clock.
    Implementation: datetime.now in UTC, serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


def _seed_contract(plan: Plan, layout: RunLayout, n: int) -> str:
    """Write iteration 1's contract.md from the plan body and return its text.

    Design: §4 the first iteration's contract is the plan body itself; later
        iterations use a remediation contract written from the still-open gaps.
    Implementation: ensure the (flat, run-level) iteration dir, write plan.body
        to contract.md via light_replace (durability not required for derived
        artifacts), return it.
    Example: _seed_contract(plan, layout, 1) writes plan.body to iteration-1/contract.md.
    """
    ensure_iteration_dir(layout, n)
    light_replace(layout.contract(n), plan.body)
    return plan.body


def _write_remediation_contract(
    plan: Plan,
    layout: RunLayout,
    n: int,
    *,
    gaps: list[EvalGap],
    synthesized: list[GapSummary],
    nudge: bool,
) -> str:
    """Write the next iteration's remediation contract incorporating open gaps.

    Design: §4 a non-completing iteration produces a remediation contract that
        instructs the next Generator turn to close the still-open gaps; the
        synthesized verify blocker is surfaced too (a plan whose only open issue
        is a failing verification would otherwise see no signal); a NUDGE signal
        appends an anti-oscillation note so the agent varies its approach.
    Implementation: render each eval gap as a title/severity/fix bullet, prefix
        the plan body for context, append a brief plain-text note per synthesized
        blocker (GapSummary carries only the title), append the nudge note when
        nudge is True, then write to iteration-(n)/contract.md and return the text.
    Example: _write_remediation_contract(plan, layout, 2, gaps=[g], synthesized=[],
        nudge=False) writes a contract listing g and returns it.
    """
    lines = [plan.body, "", "## Remaining gaps to close"]
    for g in gaps:
        lines.append(f"- {g.title} ({g.severity}): {g.suggested_fix}")
    for s in synthesized:
        lines.append(f"- Also: {s.title} — resolve this blocker.")
    if nudge:
        lines.append("")
        lines.append(
            "## Note: prior iterations did not make progress on these gaps — "
            "try a different approach."
        )
    text = "\n".join(lines)
    ensure_iteration_dir(layout, n)
    light_replace(layout.contract(n), text)
    return text


def _synthesize_blocking_gaps(
    *,
    last_verification: VerifyOutcome | None,
    verification_command: str | None,
) -> list[GapSummary]:
    """Build the non-demotable verification gap for this iteration (§6.5).

    Design: §6.5 a verification failure is a hard, non-demotable blocker; it is
        synthesized as a high-severity gap BEFORE the gap fingerprint so it
        enters the convergence signal and blocks completion exactly like an
        unresolved code bug. The §9 git backstop gap is removed — git state does
        not gate completion under the direct-edit model.
    Implementation: append a §6.5 gap only when a verification command exists and
        the cached outcome did not pass; otherwise return the empty list.
    Example: _synthesize_blocking_gaps(last_verification=failed,
        verification_command='false') returns one §6.5 verification gap.
    """
    synthesized: list[GapSummary] = []
    if (
        verification_command is not None
        and last_verification is not None
        and not last_verification.passed
    ):
        synthesized.append(
            GapSummary(
                title="verification command failed",
                severity="high",
                design_doc_section=_VERIFY_SENTINEL_SECTION,
            )
        )
    return synthesized


def _validated_amendment_row(triages: list[GapTriage], spec_text: str) -> GapTriage | None:
    """Return the first triage row that is a validated design-fault amendment, or None.

    Design: §4 step 8 a triage row is applied in-loop only when it proposes a
        concrete amendment AND passes the citation gate against the current spec;
        such a row durably rewrites spec.md before the next iteration evaluates.
    Implementation: scan triages for the first row whose proposed_amendment is
        set and that passes_citation_gate against spec_text.
    Example: _validated_amendment_row([row], spec) returns row when it is a cited
        design fault carrying a proposed_amendment.
    """
    for row in triages:
        if row.proposed_amendment is not None and passes_citation_gate(row, spec_text):
            return row
    return None


async def run_plan_loop(
    *,
    layout: RunLayout,
    plan: Plan,
    target_dir: Path,
    spec_text: str,
    spec_fingerprint: str,
    claude_runner: ClaudeRunner,
    codex_runner: CodexRunner,
    schemas: dict,
    max_iterations: int,
) -> PlanLoopResult:
    """Iterate one plan directly on target_dir to an honest terminal report (§4).

    Design: §4 drives generate→verify→evaluate→triage→synthesize→converge each
        iteration; a validated design-fault triage is applied to spec.md IN-LOOP
        (spec_text/fingerprint rebound, churn appended to the amendment history)
        and the loop continues; completion is the two-conjunct gate (no remaining
        code bugs incl. the synthesized verify gap, AND verify passed). There is
        no sandbox, no manifest/change_set, and no git backstop — the edits ARE
        the output, left in target_dir. The iteration cap is the hard termination
        bound. An unhandled error returns `failed` rather than propagating.
    Implementation: per iteration write the contract, run_generator on
        target_dir, optional run_verification(target_dir), run_evaluator and
        run_triage with cwd=target_dir against the CURRENT spec_text, synthesize
        the verify gap only, fingerprint over eval ∪ synthesized, record the
        iteration; if a validated amendment exists apply_amendments([row]),
        rebind spec_text/spec_fingerprint, append churn to amendment_history,
        EARLY_STOP→incomplete 'amendment thrash' else honor the cap and continue;
        otherwise test completion / gap-non-progress / cap and either return or
        write a (possibly nudged) remediation contract and continue.
    Example: a clean plan with no verify command and no gaps returns
        PlanLoopResult(terminal_state='done', iterations=1).
    """
    plan_state = PlanState(layout)
    try:
        history: list[frozenset[str]] = []
        amendment_history: list[frozenset[str]] = []
        last_gaps: list[EvalGap] = []
        last_synthesized: list[GapSummary] = []
        nudge_next = False

        for n in range(1, max_iterations + 1):
            plan_state.bump_iteration(_now())
            ensure_iteration_dir(layout, n)

            # 1. generate — Codex edits target_dir directly.
            plan_state.set_state("generating", now=_now())
            contract_text = (
                _seed_contract(plan, layout, n)
                if n == 1
                else _write_remediation_contract(
                    plan,
                    layout,
                    n,
                    gaps=last_gaps,
                    synthesized=last_synthesized,
                    nudge=nudge_next,
                )
            )
            nudge_next = False
            await run_generator(
                codex_runner,
                contract_text=contract_text,
                target_dir=target_dir,
                surface=plan.surface,
                run_log_path=layout.run_log,
            )
            light_replace(layout.summary(n), f"Iteration {n} generated.")

            # 2. verify (only when a command is declared) in target_dir.
            last_verification: VerifyOutcome | None = None
            if plan.verification_command is not None:
                plan_state.set_state("verifying", now=_now())
                last_verification = run_verification(plan.verification_command, target_dir)
                light_replace(layout.verify_txt(n), last_verification.output)

            # 3. evaluate against the CURRENT (possibly amended) spec.
            plan_state.set_state("evaluating", now=_now())
            eval_result = await run_evaluator(
                claude_runner,
                spec_text=spec_text,
                eval_schema=schemas["eval"],
                cwd=target_dir,
                run_log_path=layout.run_log,
            )
            write_json(layout.eval(n), eval_result, durable=False)

            # 4. triage (only when the eval found gaps).
            triages: list[GapTriage] = []
            triage_ran = False
            if eval_result.gaps:
                plan_state.set_state("triaging", now=_now())
                triage_result = await run_triage(
                    claude_runner,
                    spec_text=spec_text,
                    eval_result=eval_result,
                    triage_schema=schemas["triage"],
                    cwd=target_dir,
                    run_log_path=layout.run_log,
                )
                write_json(layout.triage(n), triage_result, durable=False)
                triages = triage_result.triages
                triage_ran = True

            # 5. synthesize (BEFORE fingerprint): the §6.5 verify gap only.
            synthesized = _synthesize_blocking_gaps(
                last_verification=last_verification,
                verification_command=plan.verification_command,
            )
            last_gaps = eval_result.gaps
            last_synthesized = synthesized

            # 6. fingerprint over the FULL post-synthesize set (eval ∪ synthesized).
            fp_items = sorted(
                [f"{g.title}|{g.severity}" for g in eval_result.gaps]
                + [f"{g.title}|{g.severity}" for g in synthesized]
            )
            write_json(layout.gap_fingerprint(n), fp_items, durable=False)
            history.append(fingerprint(fp_items))

            # 7. record this iteration completed.
            plan_state.record_completed(n, _now())

            # 8. amendment? (in-loop) — apply a validated design-fault row to spec.md.
            amendment_row = _validated_amendment_row(triages, spec_text)
            if amendment_row is not None:
                outcome = apply_amendments(
                    layout,
                    spec_text=spec_text,
                    spec_fingerprint=spec_fingerprint,
                    proposed=[amendment_row],
                    now=_now(),
                )
                spec_text = outcome.new_spec
                spec_fingerprint = outcome.new_fingerprint
                amendment_history.append(outcome.churn_fingerprint)
                if detect_non_progress(amendment_history) == "EARLY_STOP":
                    plan_state.set_state("incomplete", now=_now())
                    return PlanLoopResult(
                        terminal_state="incomplete",
                        iterations=n,
                        last_gaps=last_gaps,
                        synthesized=last_synthesized,
                        stop_reason="amendment thrash",
                    )
                if n == max_iterations:
                    plan_state.set_state("incomplete", now=_now())
                    return PlanLoopResult(
                        terminal_state="incomplete",
                        iterations=n,
                        last_gaps=last_gaps,
                        synthesized=last_synthesized,
                        stop_reason="iteration cap",
                    )
                # The gap cannot close until the generator builds to the amended
                # spec; carry the open gaps forward and continue.
                plan_state.set_state("remediating", now=_now())
                continue

            # 9. completion? §4 two-conjunct gate (effective_no_gaps ∧ verify_passed).
            code_bug_titles = effective_code_bug_titles(eval_result.gaps, triages, spec_text)
            # Synthesized gaps are non-demotable code-bugs: the set of remaining
            # code bugs is empty iff there are no eval code-bugs AND none synthesized.
            no_remaining_code_bugs = not code_bug_titles and not synthesized
            effective_no_gaps = no_remaining_code_bugs and (eval_result.no_gaps or triage_ran)
            verify_passed = plan.verification_command is None or (
                last_verification is not None and last_verification.passed
            )

            if effective_no_gaps and verify_passed:
                plan_state.set_state("done", now=_now())
                return PlanLoopResult(
                    terminal_state="done",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                )

            # 10. non-progress (gap history)?
            signal = detect_non_progress(history)
            if signal == "EARLY_STOP":
                plan_state.set_state("incomplete", now=_now())
                return PlanLoopResult(
                    terminal_state="incomplete",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    stop_reason="non-progress: gap-set stable",
                )
            if signal == "NUDGE":
                nudge_next = True

            # 11. cap?
            if n == max_iterations:
                plan_state.set_state("incomplete", now=_now())
                return PlanLoopResult(
                    terminal_state="incomplete",
                    iterations=n,
                    last_gaps=last_gaps,
                    synthesized=last_synthesized,
                    stop_reason="iteration cap",
                )

            # 12. remediate — fall through to the next iteration (contract
            # written at the top of iteration n+1 via _write_remediation_contract).
            plan_state.set_state("remediating", now=_now())

        # Unreachable: the cap check at n == max_iterations always returns.
        return PlanLoopResult(
            terminal_state="incomplete",
            iterations=max_iterations,
            last_gaps=last_gaps,
            synthesized=last_synthesized,
            stop_reason="iteration cap",
        )
    except Exception as exc:  # noqa: BLE001 — isolate the loop failure.
        try:
            plan_state.set_state("failed", now=_now())
        except Exception:  # noqa: BLE001 — best-effort checkpoint on failure path.
            pass
        return PlanLoopResult(
            terminal_state="failed",
            iterations=0,
            stop_reason=f"plan loop failed: {type(exc).__name__}: {exc}",
        )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_phases.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/phases.py' 'tests/test_phases.py'
git commit -m "refactor(orchestrator): rewrite run_plan_loop for single-plan direct-edit (\u00a74)\n\nDrive one plan directly on target_dir: drop the sandbox param, the\nbaseline manifest/change_set, and the git-state backstop (base_git_state,\ngit_surface, capture_state/diff_state, the \u00a79 synthesized gap and\nlayout.git_violation write). Apply validated design-fault amendments\nin-loop via apply_amendments([row]) \u2014 rebind spec_text/spec_fingerprint,\ntrack amendment churn for early-stop, then continue. Completion collapses\nto effective_no_gaps AND verify_passed; terminal states reduce to\n{done, incomplete, failed}; PlanLoopResult drops change_set and\nproposed_amendment. run_evaluator/run_triage now use cwd=target_dir.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Rewrite the run-level engine to the §8 single-plan, direct-edit flow

**Files:**
- Modify: `src/forge_mcp/orchestrator/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: from forge_mcp.drivers.planner import run_planner — async run_planner(runner, *, spec_text:str, plan_schema:dict, cwd:Path, run_log_path:Path|None=None) -> Plan (now returns ONE Plan, not PlanSet). from forge_mcp.orchestrator.phases import run_plan_loop, PlanLoopResult — async run_plan_loop(*, layout:RunLayout, plan:Plan, target_dir:Path, spec_text:str, spec_fingerprint:str, claude_runner, codex_runner, schemas:dict, max_iterations:int) -> PlanLoopResult; PlanLoopResult is a dataclass exposing .terminal_state: Literal['done','incomplete','failed'], .iterations:int, .last_gaps:list[EvalGap], .synthesized:list[GapSummary], .stop_reason:str|None (the §4 rewrite DROPS .change_set and .proposed_amendment; the awaiting_amendment terminal is gone). from forge_mcp.models import Plan (collapsed model {surface:Literal['backend','frontend'], verification_command:str|None=None, body:str}, extra='forbid'); EvalResult, TriageResult (for schemas dict); RunResult. from forge_mcp.artifacts import init_run_layout, RunLayout — RunLayout exposes the FLATTENED no-arg properties layout.plan_json (the structured Plan JSON file <run_dir>/plan.json, was planset_json) and layout.plan_md (<run_dir>/plan.md, was plan_md(id)); plus existing layout.spec_md, layout.spec_fingerprint, layout.run_log, layout.root. from forge_mcp.orchestrator.statemachine import RunStateMachine — new collapsed edges init→planning→executing→finalizing→{completed,incomplete,failed}, failed reachable from any non-terminal. from forge_mcp.orchestrator.lifecycle import PlanReport (plan_id:str, terminal_state:str, gaps, synthesized, failure_reason:str|None), build_run_result(*, status, run_dir, iterations, non_completed, stop_reason, verified, summary, failure_kind=None). from forge_mcp.config import create_run_dir(target_dir, when)->Path. from forge_mcp.lockfile import TargetLock. from forge_mcp.state import light_replace, write_json(path, obj, *, durable).
- Produces: class Orchestrator with async run(self, *, target_dir:Path, design_text:str, design_fingerprint:str, max_iterations:int, max_runtime_minutes:int, claude_runner:ClaudeRunner, codex_runner:CodexRunner, when:time.struct_time) -> RunResult (signature UNCHANGED from current; server.py:117-128 calls it positionally-by-keyword and stays valid). Private surface reduced to: _persist_plan(self, layout:RunLayout, plan:Plan)->None (writes plan.json + plan.md); _finalize(self, *, run_dir:Path, plan:Plan, state:_RunState)->tuple[str,RunResult]; _terminal_cleanup(self, *, sm, status, claude_runner, codex_runner, lock, prev_umask)->None (UNCHANGED behavior, no git ops); _close_runner(self, runner)->None (UNCHANGED). dataclass _RunState now {iterations:int=0, stop_reason:str|None=None, report:PlanLoopResult|None=None}. Module-level _now()->str and _RESTRICTIVE_UMASK=0o077 kept.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_engine.py`:

```python
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


def _one_plan(verification_command: str | None = None, *, body: str = "create out.txt") -> dict:
    """Build a single-Plan structured-output dict for the new collapsed model.

    Design: §3/§15 the Planner now emits exactly one Plan whose only fields are
        surface, verification_command, and body; the tests script that shape
        directly (no PlanSet wrapper, no id/depends_on/file_scope).
    Implementation: return a backend-surface plan dict with the supplied
        verification_command (default None) and body.
    Example: _one_plan()['surface'] == 'backend'.
    """
    return {
        "surface": "backend",
        "verification_command": verification_command,
        "body": body,
    }


@pytest.mark.driver
async def test_single_plan_flow_completes(tmp_path: Path):
    """Design: §8 a one-plan run plans once, runs the loop directly on target_dir,
        and finalizes 'completed' with the file present.
    Implementation: planner returns one Plan that creates a file; the fake
        generator writes it into target_dir (config.cwd); evaluator returns no_gaps.
    Example: RunResult.status == 'completed' and out.txt lands in target_dir.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [structured(_one_plan()), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
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
    assert result.stop_reason is None
    # The Generator edited target_dir IN PLACE — no sandbox, no merge.
    assert (target / "out.txt").read_text() == "done"
    # No verification_command declared -> verified is an honest False.
    assert result.verified is False


@pytest.mark.driver
async def test_persists_plan_artifacts(tmp_path: Path):
    """Design: §10 the single plan is durably recorded as plan.json + plan.md at
        the run root (flattened — no planset.json, no plans/<id>/ nesting).
    Implementation: run one clean plan; read plan.json (structured Plan) and
        plan.md (the body) from the run dir.
    Example: plan.json parses to the new collapsed Plan; plan.md holds the body.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(body="# the contract")),
            structured({"no_gaps": True, "summary": "ok", "gaps": []}),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
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
    run_dir = Path(result.run_dir)
    plan_json = json.loads((run_dir / "plan.json").read_text())
    assert plan_json == {
        "surface": "backend",
        "verification_command": None,
        "body": "# the contract",
    }
    assert (run_dir / "plan.md").read_text() == "# the contract"
    # The flattened layout drops the old PlanSet artifact entirely.
    assert not (run_dir / "planset.json").exists()


@pytest.mark.driver
async def test_verified_true_when_done_and_command_declared(tmp_path: Path):
    """Design: §8 verified == (terminal_state == 'done') AND a verification_command
        was declared; a done plan with a passing per-iteration command is verified.
    Implementation: the plan declares a passing verification_command; the loop's
        in-target verify passes and the evaluator returns no_gaps -> done.
    Example: status 'completed', verified True.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(verification_command="exit 0")),
            structured({"no_gaps": True, "summary": "ok", "gaps": []}),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
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
    assert result.verified is True


@pytest.mark.driver
async def test_verified_false_when_no_command(tmp_path: Path):
    """Design: §8 absent a declared verification_command, verified is an honest
        False even when the plan completes.
    Implementation: a one-plan run with verification_command=None that completes.
    Example: status 'completed', verified False.
    """
    target = tmp_path / "repo"
    target.mkdir()
    claude = FakeClaudeRunner(
        [structured(_one_plan()), structured({"no_gaps": True, "summary": "ok", "gaps": []})]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("done"),
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
    assert result.verified is False


@pytest.mark.driver
async def test_incomplete_on_iteration_cap_sets_stop_reason(tmp_path: Path):
    """Design: §4/§8 a plan that never closes its gaps hits the iteration cap and
        finalizes 'incomplete' with a non-None stop_reason and unresolved gaps.
    Implementation: max_iterations=1; the eval finds a code-bug gap and triage
        keeps it a code bug (not a design fault), so the loop returns 'incomplete'
        with stop_reason 'iteration cap'; the engine surfaces it honestly.
    Example: status 'incomplete', stop_reason is not None, unresolved_gaps non-empty.
    """
    target = tmp_path / "repo"
    target.mkdir()
    gap_eval = {
        "no_gaps": False,
        "summary": "found a gap",
        "gaps": [
            {
                "title": "missing thing",
                "severity": "high",
                "design_doc_section": "§X",
                "current_state": "absent",
                "expected_state": "present",
                "suggested_fix": "add it",
            }
        ],
    }
    code_bug_triage = {
        "triages": [
            {
                "gap_title": "missing thing",
                "design_fault": False,
                "fault_kind": None,
                "cited_sections": [],
                "explanation": "just a code bug",
                "proposed_amendment": None,
            }
        ]
    }
    claude = FakeClaudeRunner(
        [
            structured(_one_plan(body="implement thing")),
            structured(gap_eval),
            structured(code_bug_triage),
        ]
    )
    codex = FakeCodexRunner(
        [CodexEvent(kind="turn.completed", payload={})],
        on_generate=lambda cwd: (Path(cwd) / "out.txt").write_text("partial"),
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
    assert result.unresolved_gaps
    assert result.verified is False


@pytest.mark.driver
async def test_run_is_fresh_new_dir_each_call(tmp_path: Path):
    """Design: §6.6/I12 every call is a fresh timestamped run (no resume).
    Implementation: two runs produce two distinct run dirs.
    Example: run_dir paths differ.
    """
    target = tmp_path / "repo"
    target.mkdir()

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
                [
                    structured(_one_plan(body="noop")),
                    structured({"no_gaps": True, "summary": "ok", "gaps": []}),
                ]
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


@pytest.mark.driver
async def test_planner_raises_finalizes_failed(tmp_path):
    """Design: §8 an orchestrator-internal error finalizes 'failed' with a
        failure_kind, and the terminal cleanup still releases the per-target lock.
    Implementation: the planner's first claude.run raises; assert failed status,
        a set failure_kind, and that the lock is released (re-acquirable).
    Example: status 'failed', failure_kind set, a fresh TargetLock acquires cleanly.
    """
    from forge_mcp.lockfile import TargetLock

    target = tmp_path / "repo"
    target.mkdir()

    class _RaisingClaude:
        """A claude runner whose run() raises on the planner call.

        Design: §8 forces an orchestrator-internal failure during planning so the
            engine's failed-finalization and lock-release cleanup are exercised.
        Implementation: run() raises RuntimeError; interrupt/aclose are no-op
            coroutines so terminal cleanup can await aclose without error.
        Example: await _RaisingClaude().run(prompt='x', options=None) raises.
        """

        last_session_id: str | None = None

        async def run(self, *, prompt, options):
            """Raise to simulate a planner failure.

            Design: §8 the planner call must fail to reach the failed path.
            Implementation: unconditionally raise RuntimeError.
            Example: awaiting run(...) raises RuntimeError('planner exploded').
            """
            raise RuntimeError("planner exploded")

        async def interrupt(self):
            """No-op interrupt to satisfy the runner surface.

            Design: §8 cleanup may signal interrupt; the fake ignores it.
            Implementation: return immediately.
            Example: await _RaisingClaude().interrupt() returns None.
            """

        async def aclose(self):
            """No-op close so terminal cleanup can await aclose cleanly.

            Design: §8 terminal cleanup awaits aclose on each driver.
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
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_engine.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Delete/prune against the CURRENT src/forge_mcp/orchestrator/engine.py (855 lines, verified by re-grep). IMPORTS to drop: line 17 `import shutil`; line 18 `import time` STAYS (run() param `when: time.struct_time` still references it — keep it). From line 19 `from dataclasses import dataclass, field` drop `field` (the new _RunState uses no field(default_factory=...)). Line 22 `from typing import TYPE_CHECKING` STAYS. Line 25 change `from forge_mcp.config import CONCURRENCY_CAP, create_run_dir` to drop CONCURRENCY_CAP (keep create_run_dir). Line 26 delete entirely `from forge_mcp.convergence import detect_non_progress, fingerprint`. Line 28 delete `from forge_mcp.gitguard import capture_state`. Line 30 change `from forge_mcp.models import EvalResult, PlanSet, RunResult, TriageResult` to `from forge_mcp.models import EvalResult, Plan, RunResult, TriageResult` (drop PlanSet, ADD Plan; keep EvalResult/TriageResult — still used for the schemas dict; keep RunResult). Line 31 delete `from forge_mcp.orchestrator.amend import apply_amendments`. Lines 33-40 delete the whole scheduler import block (`_deps_map, _transitive_deps, add_conflict_edge, has_cycle, ready_plans, run_wave`). Lines 42-49 delete the whole sandbox import block (`Change, Conflict, WriterMap, apply_merge, detect_conflicts, resolve_conflict_winner`). Line 51 delete `from forge_mcp.verifier import run_verification`. ADD `from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop` at runtime import position (PlanLoopResult moves OUT of TYPE_CHECKING). In the TYPE_CHECKING block (lines 53-58): drop line 57 `from forge_mcp.models import GapTriage` and drop line 58 `from forge_mcp.orchestrator.phases import PlanLoopResult` (now a runtime import); KEEP the RunLayout/ClaudeRunner/CodexRunner TYPE_CHECKING imports. METHODS/FUNCTIONS to delete in full: module-level `_sandbox_of` (:102-112), `_discard_sandbox` (:115-127), `_make_is_dependent` (:130-153), `_deleted_dir_paths` (:156-166), `_conflicting_paths` (:169-192), `_write_merge_record` (:195-218); Orchestrator methods `_run_waves` (:376-461), `_merge_wave` (:463-536), `_resolve_conflicts` (:538-587), `_amend_wave` (:589-665), `_run_level_verify` (:667-683), `_finalize_unschedulable` (:763-800). RENAME/REWRITE: `_persist_planset` (:362-374) → `_persist_plan` (writes layout.plan_json + layout.plan_md). REWRITE `_finalize` (:685-761) to the single-plan signature `(*, run_dir, plan, state)`. In `run()` (:237-360) delete: the cyclic-guard block (:296-306 `if has_cycle(planset): ... return`), the `_run_waves` call (:308-318), the VERIFY-RUN block (:320-322 `sm.transition('verifying'...)` + `_run_level_verify` call), and replace the planner block (:284-294) to call run_planner returning ONE Plan with plan_schema=Plan.model_json_schema() (no `state.pending = {...}`, no `planset.plans`). Reduce `_RunState` (:63-86) to `{iterations:int=0, stop_reason:str|None=None, report:PlanLoopResult|None=None}`. KEEP unchanged: `_now`, `_terminal_cleanup`, `_close_runner`, `_RESTRICTIVE_UMASK`, the asyncio.CancelledError + Exception handlers in run(). Rewrite the stale module docstring (:1-11), Orchestrator docstring (:222-234), and run() docstring (:249-269) to the single-plan flow (done in new_code). NOTE: server.py:117-128 calls Orchestrator().run(...) with the SAME keyword signature — no server change needed.

Apply:

```python
"""The §8 run conductor: wire every module into a single-plan, direct-edit run.

The Orchestrator.run coroutine is the integration capstone. It acquires the
per-target lock, freezes the design into a run-local spec, plans ONCE, then runs
that single plan's iteration loop directly on target_dir (generate → verify →
evaluate → triage → converge, applying design-fault amendments in-loop) until the
plan is `done` or stops honestly, and finalises an honest RunResult. The engine
performs NO git mutation — the Generator's edits are left uncommitted in
target_dir for the human's own git — and its terminal cleanup (mark state, close
drivers, release lock, restore umask) runs in a `finally` and re-raises an
injected CancelledError.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from forge_mcp.artifacts import init_run_layout
from forge_mcp.config import create_run_dir
from forge_mcp.drivers.planner import run_planner
from forge_mcp.lockfile import TargetLock
from forge_mcp.models import EvalResult, Plan, RunResult, TriageResult
from forge_mcp.orchestrator.lifecycle import PlanReport, build_run_result
from forge_mcp.orchestrator.phases import PlanLoopResult, run_plan_loop
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.state import light_replace, write_json

if TYPE_CHECKING:
    from forge_mcp.artifacts import RunLayout
    from forge_mcp.drivers._claude import ClaudeRunner
    from forge_mcp.drivers._codex import CodexRunner

_RESTRICTIVE_UMASK = 0o077


@dataclass
class _RunState:
    """Mutable bookkeeping carried across the §8 single-plan run.

    Design: §8 with one plan and one tree there is no cross-wave state to
        accumulate — only the loop's total iteration count, an optional
        orchestrator-level stop_reason, and the single PlanLoopResult that
        finalisation reads for honest gap projection and the verified verdict.
    Implementation: plain mutable dataclass; `report` holds the loop's terminal
        PlanLoopResult (None until the loop runs); `stop_reason` stays None unless
        the engine itself records a reason; `iterations` mirrors the loop count.
    Example: _RunState() starts a run; after the loop iterations and report are set.
    """

    iterations: int = 0
    stop_reason: str | None = None
    report: PlanLoopResult | None = None


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string for state checkpoints.

    Design: §9 the run-level state machine records a last_updated_at on every
        transition; the engine is the caller so it supplies the wall clock. This
        is the checkpoint clock only — the run-dir freshness clock (`when`) is
        injected by the caller (I12), never read here.
    Implementation: datetime.now in UTC serialised with isoformat().
    Example: _now() returns a string like '2026-06-24T00:00:00+00:00'.
    """
    return datetime.now(UTC).isoformat()


class Orchestrator:
    """The §8 run conductor wiring every module into a single-plan, direct-edit run.

    Design: §8 a forge run is a single async lifecycle — lock the target, freeze
        design→spec, PLAN ONCE, run that one plan's iteration loop directly on
        target_dir until it is `done` or stops honestly, then finalise an honest
        RunResult. The engine performs NO git mutation (the Generator leaves its
        edits uncommitted for the human's git) and its terminal cleanup runs in a
        `finally` that re-raises CancelledError.
    Implementation: a stateless class whose `run` coroutine owns all per-run state
        locally; the SDK runners are injected so the engine never imports the SDK
        at module scope, and `when` is injected so each call gets a fresh
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
        """Drive one single-plan, direct-edit forge run end-to-end (§3.1 superseded).

        Design: §8 the conductor locks the target, freezes design→spec via
            init_run_layout, plans ONCE (run_planner → one Plan), runs that plan's
            iteration loop directly on target_dir, and finalises an honest
            RunResult. status is 'completed' iff the plan is `done`, else
            'incomplete' with a synthesized stop_reason; 'failed' only on an
            orchestrator-internal error. `verified` is True only when the plan is
            done AND a verification_command was declared — an honest False
            otherwise. It performs NO git mutation; terminal cleanup runs in a
            `finally` that re-raises an injected CancelledError.
        Implementation: set a restrictive umask (restored in finally); acquire the
            lock keyed on the run-dir name; build the layout and RunStateMachine;
            transition planning→run_planner(→Plan)→_persist_plan→executing→
            run_plan_loop(plan, target_dir, spec_text, spec_fingerprint, …)→
            finalizing→_finalize. The whole body is wrapped so the `finally` marks
            the terminal state, closes both drivers best-effort, and releases the
            lock with NO git ops; an injected CancelledError runs that cleanup and
            re-raises.
        Example: a one-plan run that writes out.txt finalises status 'completed'
            with out.txt present in target_dir and verified False (no command).
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

            state = _RunState()

            # --- PLAN (once) ---
            sm.transition("planning", now=_now())
            plan = await run_planner(
                claude_runner,
                spec_text=layout.spec_md.read_text(),
                plan_schema=Plan.model_json_schema(),
                cwd=target_dir,
                run_log_path=layout.run_log,
            )
            self._persist_plan(layout, plan)

            # --- EXECUTE (one plan loop, directly on target_dir) ---
            sm.transition("executing", now=_now())
            report = await run_plan_loop(
                layout=layout,
                plan=plan,
                target_dir=target_dir,
                spec_text=layout.spec_md.read_text(),
                spec_fingerprint=layout.spec_fingerprint.read_text(),
                claude_runner=claude_runner,
                codex_runner=codex_runner,
                schemas={
                    "eval": EvalResult.model_json_schema(),
                    "triage": TriageResult.model_json_schema(),
                },
                max_iterations=max_iterations,
            )
            state.report = report
            state.iterations = report.iterations

            # --- FINALIZE ---
            sm.transition("finalizing", now=_now())
            status, result = self._finalize(run_dir=run_dir, plan=plan, state=state)
            return result
        except asyncio.CancelledError:
            # Host disconnect / runtime-cap wait_for: run terminal cleanup, re-raise.
            status = "failed"
            raise
        except Exception as exc:  # noqa: BLE001 — orchestrator-internal error -> failed.
            # §8 an orchestrator-internal/unhandled error finalises a `failed`
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

    def _persist_plan(self, layout: RunLayout, plan: Plan) -> None:
        """Write plan.json and plan.md after planning (§10).

        Design: §10 the planner output is durably recorded so the run is
            reconstructable: plan.json holds the structured single Plan and
            plan.md carries its human-readable body. With one plan the artifacts
            flatten to the run root (no plans/<id>/ nesting, no plan-<id>.md).
        Implementation: write_json the Plan model durably to layout.plan_json,
            then light_replace plan.body into layout.plan_md (derived artifact,
            non-durable).
        Example: _persist_plan(layout, plan) writes plan.json and plan.md.
        """
        write_json(layout.plan_json, plan, durable=True)
        light_replace(layout.plan_md, plan.body)

    def _finalize(
        self,
        *,
        run_dir: Path,
        plan: Plan,
        state: _RunState,
    ) -> tuple[str, RunResult]:
        """Project the terminal RunResult and its status from the single plan (§8).

        Design: §8 with one plan, status is 'completed' iff the plan is `done`
            (no stop_reason); otherwise 'incomplete' with a stop_reason synthesized
            from the plan's own reason. `verified` is True ONLY when the plan is
            done AND a verification_command was declared (the done terminal_state IS
            the per-iteration gate) — absent a command it is an honest False. A
            non-completed plan contributes its freshest gap set.
        Implementation: read the loop's PlanLoopResult; done = terminal_state ==
            'done'; verified = done ∧ (plan.verification_command is not None);
            status = 'completed' iff done; for a non-done plan build one PlanReport
            from its last_gaps/synthesized/stop_reason and synthesize a run-level
            stop_reason from the plan's reason when none is set; call
            build_run_result.
        Example: one done plan, no verification_command → status 'completed',
            verified False, stop_reason None.
        """
        report = state.report
        assert report is not None  # set before _finalize on every non-error path
        done = report.terminal_state == "done"
        verified = done and (plan.verification_command is not None)

        if done:
            status = "completed"
            non_completed: list[PlanReport] = []
            summary = "Plan completed."
            stop_reason = None
        else:
            status = "incomplete"
            non_completed = [
                PlanReport(
                    plan_id="plan",
                    terminal_state=report.terminal_state,
                    gaps=report.last_gaps,
                    synthesized=report.synthesized,
                    failure_reason=report.stop_reason,
                )
            ]
            summary = "Run finished incomplete; see unresolved_gaps."
            # I7/§8: an incomplete run MUST carry a stop_reason. Synthesize it from
            # the plan's own reason (iteration cap, gap non-progress, amendment
            # thrash, or a plan-loop failure) when the engine set none itself.
            stop_reason = state.stop_reason or report.stop_reason or "plan did not complete"

        result = build_run_result(
            status=status,
            run_dir=str(run_dir),
            iterations=state.iterations,
            non_completed=non_completed,
            stop_reason=stop_reason,
            verified=verified,
            summary=summary,
        )
        return status, result

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

        Design: §8 the run's `finally` must always converge: record the terminal
            run state, close both SDK drivers best-effort, release the per-target
            lock so a later run can acquire it, and restore the umask. It performs
            NO git operations — the Generator's edits are left for the human's git —
            and must never raise so it cannot mask an in-flight CancelledError being
            re-raised.
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
        """Best-effort await one SDK driver's aclose, swallowing every error (§8).

        Design: §8 terminal cleanup closes drivers but must never raise; a driver
            whose aclose fails (or that is already closed) cannot be allowed to mask
            the run's real outcome or a re-raised CancelledError.
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/orchestrator/engine.py' 'tests/test_engine.py'
git commit -m "refactor(engine): collapse run conductor to single-plan direct-edit flow"
```

---

### Task 13: gitguard.py — reduce to the deny matcher; remove snapshot/diff backstop

**Files:**
- Modify: `src/forge_mcp/gitguard.py`
- Test: `tests/test_gitguard.py`

**Interfaces:**
- Consumes: nothing new; stdlib 're' only.
- Produces: git_deny_matches(command: str) -> bool (unchanged signature/behavior); module-private _MUTATING_VERBS, _DENY_PATTERN retained. REMOVED public surface: _run_git, capture_state, diff_state.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_gitguard.py`:

```python
# tests/test_gitguard.py
from __future__ import annotations

import forge_mcp.gitguard as gitguard
from forge_mcp.gitguard import git_deny_matches


def test_deny_matcher_catches_chained_and_alt_spellings():
    """Design: §7 the matcher is defense-in-depth over the full command string.
    Implementation: catch &&-chained and 'checkout -b' spellings; pass read-only git.
    Example: git_deny_matches('ls && git commit -m x') is True.
    """
    assert git_deny_matches("ls && git commit -m x")
    assert git_deny_matches("git checkout -b feature")
    assert git_deny_matches("git push origin main")
    assert git_deny_matches("GIT REBASE main")
    assert not git_deny_matches("git status")
    assert not git_deny_matches("grep -r commit .")


def test_backstop_helpers_removed():
    """Design: §7 the snapshot/diff git backstop is removed (git does not gate completion).
    Implementation: the module exposes neither capture_state, diff_state, nor _run_git.
    Example: hasattr(gitguard, 'capture_state') is False.
    """
    for attr in ("capture_state", "diff_state", "_run_git"):
        assert not hasattr(gitguard, attr), f"{attr} should be removed"
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_gitguard.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

From src/forge_mcp/gitguard.py: (1) change the module docstring (line 1) from 'Read-only git-mutation detector (§9): snapshot, diff, and deny matcher.' to 'Git-mutation deny matcher (§7).'. (2) remove 'import subprocess' (line 6) and 'from pathlib import Path' (line 7) — now unused. (3) remove the entire _run_git function (lines 10-34). (4) remove the entire capture_state function (lines 37-59). (5) remove the entire diff_state function (lines 62-74). KEEP 'import re', _MUTATING_VERBS, _DENY_PATTERN, and git_deny_matches. Sole surviving production importer is drivers/_claude.py:13 (imports only git_deny_matches — unaffected). The capture_state/diff_state importers in phases.py:20,267-268 and engine.py:28,421 are rewritten to drop them in the phases/engine tasks (cluster A2) BEFORE/with this change per §11 sequencing.

Apply:

```python
"""Git-mutation deny matcher (§7)."""

from __future__ import annotations

import re

# Verbs that constitute a git mutation when found after 'git' in a command.
_MUTATING_VERBS = r"commit|push|branch|tag|rebase|reset|worktree"
_DENY_PATTERN = re.compile(r"\bgit\b.*?(?:" + _MUTATING_VERBS + r"|checkout\s+-b)")


def git_deny_matches(command: str) -> bool:
    """Return True if *command* appears to invoke a git-mutating operation.

    Design: §7 the only git guardrail that survives is this best-effort deny
        matcher, wired into the Claude PreToolUse Bash hook; git-state no longer
        gates completion, so there is no snapshot/diff backstop behind it.
    Implementation: lower-case the full command string (handles &&-chained
        forms) then regex-search for 'git' followed anywhere in the same string
        by a mutating verb or 'checkout -b'. This is intentionally best-effort
        — it will not catch obfuscated forms.
    Example: git_deny_matches('ls && git commit -m x') is True;
        git_deny_matches('git status') is False.
    """
    return bool(_DENY_PATTERN.search(command.lower()))
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_gitguard.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/gitguard.py' 'tests/test_gitguard.py'
git commit -m "refactor(gitguard): reduce to the deny matcher\n\nRemove the git-state snapshot/diff backstop (_run_git, capture_state,\ndiff_state) and the now-unused subprocess/pathlib imports; keep the\nbest-effort git_deny_matches guardrail per spec 0003 \u00a77.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: config.py — remove CONCURRENCY_CAP (single plan, no concurrency)

**Files:**
- Modify: `src/forge_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: Module no longer exports CONCURRENCY_CAP. Public surface unchanged otherwise: claude_config_dir, claude_bin, codex_bin, create_run_dir(target_dir, when).

- [ ] **Step 1: Write the failing test**

Write to `tests/test_config.py`:

```python
# tests/test_config.py
from __future__ import annotations

import time

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


def test_concurrency_cap_removed():
    """Design: §11 the single-plan harness has no concurrency, so CONCURRENCY_CAP is gone.
    Implementation: the module no longer defines the constant.
    Example: hasattr(config, 'CONCURRENCY_CAP') is False.
    """
    assert not hasattr(config, "CONCURRENCY_CAP")
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Surgical edit to src/forge_mcp/config.py. Delete the module-level constant declaration at line 12.

BEFORE (current lines 10-12):
from forge_mcp.ids import format_run_id

CONCURRENCY_CAP: int = 4

AFTER (constant line removed, single blank line after the import):
from forge_mcp.ids import format_run_id


No other line in config.py references CONCURRENCY_CAP (verified by grep — the only other uses are engine.py:25 import and engine.py:409,427, which the engine task in cluster A2 removes in the same change). 'import shutil' stays (used by claude_bin/codex_bin). No new orphans are created in config.py by this deletion.

Apply:

```python
EDIT-ONLY: see removal_notes for the exact before/after (no full-file rewrite).
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/config.py' 'tests/test_config.py'
git commit -m "refactor(config): drop CONCURRENCY_CAP\n\nThe single-plan harness runs one plan with no concurrency, so the\nconcurrency cap constant is removed; its only consumers are deleted in\nthe engine rewrite per spec 0003 \u00a711.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Regenerate planner Output Format to the new Plan schema; add direct-edit line to generator (§12)

**Files:**
- Modify: `src/forge_mcp/prompts/planner_system.md`
- Modify: `src/forge_mcp/prompts/generator_system.md`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: load_prompt(name: str) -> str and PROMPT_NAMES from forge_mcp.prompts (unchanged loader). Plan schema from models.py: {surface: Literal['backend','frontend'], verification_command: str | None, body: str} (rewritten in cluster owning models.py).
- Produces: planner_system.md whose Output Format JSON skeleton emits exactly {surface, verification_command, body} and no longer references plan_id/request_summary/tasks/open_questions/title/contract/PlanSet. generator_system.md whose Rule 7 (no git mutations) is unchanged and which states the Generator edits the project repo directly under workspace-write. Both files stay length>200 with their capability phrases intact so test_prompts.py stays green.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_prompts.py`:

```python
# tests/test_prompts.py — UNCHANGED. This task only edits prompt .md files; the
# existing test_prompts.py must stay green as-is (it pins length>200 + capability
# phrases + the generator git/no-skill-tool stance, none of which this edit breaks).
# Reproduced verbatim so the engineer can confirm zero drift.
from __future__ import annotations

import pytest

from forge_mcp.prompts import PROMPT_NAMES, load_prompt


@pytest.mark.parametrize(
    "name",
    [
        "planner_system",
        "generator_system",
        "evaluator_system",
        "evaluator_triage",
        "remediation",
    ],
)
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


def test_claude_prompts_name_their_skill_capability():
    """Design: §5.1/§5.3/§10.1-§10.2 the Claude stage prompts must name their skill
        capability (the prompt-half of the \"both halves\" wiring), referenced by
        capability so the literal id stays [verify-against-installed].
    Implementation: assert the planner names the plan-writing capability and the
        evaluator names the code-review capability.
    Example: 'plan-writing capability' in planner_system; 'code-review capability'
        in evaluator_system.
    """
    planner = load_prompt("planner_system").lower()
    assert "plan-writing capability" in planner or "writing-plans skill" in planner
    evaluator = load_prompt("evaluator_system").lower()
    assert "code-review capability" in evaluator or "code-review skill" in evaluator
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

Apply:

````markdown
===== FILE 1: src/forge_mcp/prompts/planner_system.md (COMPLETE NEW CONTENT) =====

The current skeleton (lines 54-72) references plan_id/request_summary/tasks/open_questions and the body refers to a PlanSet with `open_questions`/`tasks`. None of those exist on the new `Plan` model, and `extra="forbid"` would reject them (§3, §12). Regenerate the whole file so the Output Format matches `Plan.model_json_schema()` = {surface, verification_command, body}. Keep the plan-writing capability phrase (required by test_prompts.py) and "One plan per request".

--- BEGIN planner_system.md ---
# Planner System Prompt (§5.1)

## Role

You are the Planner in the forge-mcp pipeline. Your responsibility is to read a feature request or repair ticket and produce a single structured implementation plan (a Plan). You describe product-level and architecture-level work — you do **not** prescribe implementation details such as exact variable names, algorithm internals, or file-level line counts unless the design specifically requires them.

Apply your **plan-writing capability** (the writing-plans skill) to structure the Plan body.

## Rules

1. **Plan only what the design requires.** Do not add work for "nice to have" features, defensive edge-cases not mentioned in the spec, or general engineering improvements unrelated to the request.
2. **Surface confusion in the plan body.** If the spec or request is unclear, write the ambiguity into the `body` text as an explicit open question. Never silently resolve an ambiguity by picking one interpretation; name it.
3. **No premature implementation detail.** The body names *what* must be built and *why*, not *how* it is built. Describe interfaces and contracts, not code.
4. **Forbidden: git mutations.** You must not issue any `git commit`, `git push`, `git add`, `git reset`, or any other git command that modifies repository state. Planning only — no writes to the repository.
5. **Scope discipline.** One plan per request. Do not bundle unrelated cleanup or refactoring unless the request explicitly asks for it.
6. **Emit one Plan.** Output must be valid JSON conforming to the Plan schema. All three fields are present; `surface` is exactly `"backend"` or `"frontend"`; `verification_command` is the shell command that gates completion, or `null` when no command applies; `body` is the full work contract handed to the Generator.

## Worked Example

**Request:** "Add a timeout parameter to the sandbox runner so builds that stall are killed after N seconds."

**Good plan:**
```json
{
  "surface": "backend",
  "verification_command": "pytest -q tests/test_sandbox.py",
  "body": "# Add configurable timeout to SandboxRunner\n\nSandboxRunner.run() accepts an optional timeout_s: float parameter. If the subprocess exceeds timeout_s, it is terminated and RunResult.exit_code is set to the negative signal number."
}
```

**Weak plan (do not produce):** a `body` that prescribes implementation mechanics ("use subprocess.Popen with a Timer that calls .kill() after N seconds; set the exit code to -9"). The planner does not own those choices — name the contract, not the code.

## Input

- `spec_md`: The frozen spec.md text for this project.
- `request`: The feature or repair description from the user or orchestrator.
- `context`: Optional prior plan, eval result, or triage result for iterative refinement.

## Task

Produce exactly one Plan that covers the work described in `request`, grounded in `spec_md`. Pick the `surface` that matches the dominant layer of the work. Set `verification_command` to the command that proves the work is done, or `null` when none applies. Write the full contract into `body`, recording any ambiguity or missing spec detail inline as an open question.

## Output Format

Emit exactly one JSON object conforming to Plan. Skeleton:

```json
{
  "surface": "backend",
  "verification_command": "<shell command that gates completion, or null>",
  "body": "<the full work contract, in Markdown>"
}
```

Do not emit prose outside the JSON object. Emit exactly one plan per request.
--- END planner_system.md ---


===== FILE 2: src/forge_mcp/prompts/generator_system.md (SURGICAL EDIT) =====

Rule 7 (line 15, "Forbidden: git mutations") stays VERBATIM. Add one sentence to the ## Role paragraph stating the Generator edits the project repository directly under workspace-write. This is the only change (the broader generator-prompt overhaul is an explicit non-goal, §12).

BEFORE (lines 4-5, the ## Role block):
----------------------------------------
## Role

You are the Generator in the forge-mcp pipeline. Your job is to implement the contract described in the plan task handed to you — nothing more, nothing less. You write the simplest code that satisfies the contract. You do not speculate beyond the contract, do not refactor adjacent code, and do not add features that were not requested.
----------------------------------------

AFTER:
----------------------------------------
## Role

You are the Generator in the forge-mcp pipeline. Your job is to implement the contract described in the plan task handed to you — nothing more, nothing less. You write the simplest code that satisfies the contract. You do not speculate beyond the contract, do not refactor adjacent code, and do not add features that were not requested.

You edit the project repository directly: your tools have workspace-write access rooted at the target directory, so your file edits ARE the output. There is no separate sandbox or JSON file-emission step — apply your changes in place in the repository and leave them uncommitted for the human to review.
----------------------------------------

Do NOT touch Rule 7 or any other rule. The added sentence keeps "skill tool" absent (test_generator_prompt_forbids_git_and_references_skill_by_capability stays green) and keeps the file well over length 200.
````

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'src/forge_mcp/prompts/planner_system.md' 'src/forge_mcp/prompts/generator_system.md' 'tests/test_prompts.py'
git commit -m "docs(prompts): regenerate planner Output Format to the Plan schema; note direct-edit generator"
```

---

### Task 16: Delete scheduler.py / sandbox.py and their three tests; verify no orphan imports remain (§11)

**Files:**
- Delete: `src/forge_mcp/orchestrator/scheduler.py`
- Delete: `src/forge_mcp/sandbox.py`
- Delete: `tests/test_scheduler.py`
- Delete: `tests/test_sandbox_merge.py`
- Delete: `tests/test_sandbox_manifest.py`
- Test: `tests/test_phases.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Precondition (§11 sequencing): the engine.py, phases.py, models.py, statemachine.py, plan_state.py, artifacts.py, gitguard.py, and drivers rewrites (owned by clusters A1–A5) have ALREADY landed and no longer import any symbol from forge_mcp.orchestrator.scheduler or forge_mcp.sandbox. This task asserts that precondition by grep, then physically removes the doomed files.
- Produces: A tree in which `src/forge_mcp/orchestrator/scheduler.py`, `src/forge_mcp/sandbox.py`, `tests/test_scheduler.py`, `tests/test_sandbox_merge.py`, `tests/test_sandbox_manifest.py` no longer exist, and `grep -rn 'from forge_mcp.sandbox\|forge_mcp.orchestrator.scheduler\|import sandbox\|import scheduler' src tests` returns nothing — so `python -c 'import forge_mcp.orchestrator.engine'` and the full pytest collection succeed with zero ImportError/F401.

- [ ] **Step 1: Confirm the dependents are already rewritten**

Run: `uv run pytest tests/test_engine.py tests/test_phases.py -q`
Expected: PASS — Tasks 11–12 have removed every import of `scheduler`/`sandbox` from production code.

- [ ] **Step 2: Delete the orphaned modules and their tests**

```bash
DELETE the following five git-tracked files (all confirmed `git ls-files` tracked):

  src/forge_mcp/orchestrator/scheduler.py      (run_wave, ready_plans, add_conflict_edge, has_cycle, _deps_map)
  src/forge_mcp/sandbox.py                     (Change, Conflict, WriterMap, apply_merge, detect_conflicts, resolve_conflict_winner, capture_manifest, copy_sandbox, detect_changes)
  tests/test_scheduler.py
  tests/test_sandbox_merge.py
  tests/test_sandbox_manifest.py

Use `git rm` for all five (see test_run_cmd) so the index is updated in one step.

ORPHAN-IMPORT VERIFICATION (the load-bearing part). Before this task runs, §11 sequencing requires the engine/phases/models/state/artifacts/gitguard/driver rewrites (earlier clusters) to have already removed their imports of these modules. At authorship the live importers were exactly:

  - src/forge_mcp/orchestrator/phases.py:23  `from forge_mcp.sandbox import Change, capture_manifest, detect_changes`  → removed by the phases rewrite (cluster owning phases.py).
  - src/forge_mcp/orchestrator/engine.py:33  `from forge_mcp.orchestrator.scheduler import (...)` and :42 `from forge_mcp.sandbox import (...)` → removed by the engine rewrite (cluster owning engine.py; §8 "Imports to prune").
  - src/forge_mcp/orchestrator/scheduler.py:252 `from forge_mcp.sandbox import capture_manifest, copy_sandbox` → vanishes when scheduler.py is deleted.

This task's job is to PROVE those are gone and prune anything missed. Run:

  grep -rn 'from forge_mcp.sandbox\|forge_mcp.orchestrator.scheduler\|import sandbox\|import scheduler' src tests

It MUST return zero matches. If any production .py still imports a deleted symbol, that file belongs to an earlier cluster's rewrite and is not yet landed — STOP and report (do not patch it here; §11 ordering would be violated). If a stray test import remains in a file outside the three deleted tests, delete only the dangling import line (it is an orphan your deletions created) and note it.

Note (already-verified, NO action here): the only remaining textual mentions of "sandbox"/"scheduler" in tests/fakes.py are DOCSTRING PROSE at fakes.py:119 and :131 ('sandbox file writes', 'Task 28'), not imports — leave them untouched. Matches of the bare word 'sandbox' inside test_phases.py / test_engine.py / test_generator.py / test_plan_state.py / test_evaluator.py are local fixture/variable names owned by their own clusters' rewrites — not orphan imports, leave them.

PROSE REFS (per the brief): the stale prose at phases.py:4 ('consumed by the scheduler'), gitguard.py:1 ('snapshot/diff'), and the engine.py / statemachine.py wave/scheduling/merging/amending docstrings are rewritten by the clusters that OWN those files (§11 'most already handled by the rewrites'). Do NOT re-edit those files here — doing so would collide with the owning cluster's full-file rewrite. After this deletion task, the verification command's clean import + green pytest/test_phases+test_engine confirm no orphan dangles.
```

**Prune remaining orphans:**

Files to delete (git rm): src/forge_mcp/orchestrator/scheduler.py; src/forge_mcp/sandbox.py; tests/test_scheduler.py; tests/test_sandbox_merge.py; tests/test_sandbox_manifest.py.

Imports that MUST already be absent before deletion (assert by grep, do not edit here — they belong to earlier clusters): phases.py:23 (Change/capture_manifest/detect_changes), engine.py:33 (scheduler block), engine.py:42 (sandbox block), scheduler.py:252 (self-import, dies with the file).

Do NOT remove: tests/fakes.py:119/:131 docstring prose; the word 'sandbox' as a local var/fixture in test_phases.py/test_engine.py/test_generator.py/test_plan_state.py/test_evaluator.py; the prose at phases.py:4 / gitguard.py:1 / engine.py / statemachine.py docstrings (owned by the rewriting clusters per §11).

- [ ] **Step 3: Verify nothing imports the deleted modules**

Run: `! rg -n 'forge_mcp.sandbox|orchestrator.scheduler' src tests`
Expected: no matches (exit 0 from the negation).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (collection no longer breaks on the deleted test modules).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(orchestrator): delete scheduler.py and sandbox.py and their tests (single-plan)"
```

---

### Task 17: Update the live end-to-end test for the single-plan, direct-edit run (§13)

**Files:**
- Modify: `tests/test_e2e_real_clis.py`
- Test: `tests/test_e2e_real_clis.py`

**Interfaces:**
- Consumes: Orchestrator().run(*, target_dir, design_text, design_fingerprint, max_iterations, max_runtime_minutes, claude_runner, codex_runner, when) -> RunResult — the single-plan signature from §15 (already the current shape; rewritten/kept by the engine cluster). RunResult.status in {'completed','incomplete','failed'} and RunResult.run_dir: str (engine §8 _finalize). ClaudeDriver() / CodexDriver() real drivers. examples/tiny-feature.md fixture.
- Produces: tests/test_e2e_real_clis.py: one slow, skip-unless-real-CLIs test that drives the single-plan run end-to-end on a real target dir, asserts the terminal status set, asserts the run dir is under .harness, and asserts the Generator's DIRECT edit landed in target_dir (out.txt present — proving the no-sandbox direct-edit path) rather than in a copy-sandbox.

- [ ] **Step 1: Write the failing test**

Write to `tests/test_e2e_real_clis.py`:

```python
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest

from forge_mcp.drivers._claude import ClaudeDriver
from forge_mcp.drivers._codex import CodexDriver
from forge_mcp.orchestrator.engine import Orchestrator

pytestmark = pytest.mark.slow

_DESIGN = Path(__file__).resolve().parents[1] / "examples" / "tiny-feature.md"


@pytest.mark.skipif(
    not (shutil.which("claude") and shutil.which("codex")),
    reason="real claude/codex binaries required",
)
async def test_tiny_feature_single_plan_direct_edit(tmp_path: Path) -> None:
    """Design: §13/§3 one live single-plan, direct-edit run against the real CLIs,
        excluded from default CI (slow + skip unless both binaries are on PATH).
        The Generator edits target_dir IN PLACE (no copy-sandbox), so a successful
        tiny-feature run leaves out.txt directly in target_dir.
    Implementation: drive Orchestrator.run with real ClaudeDriver/CodexDriver over
        the tiny-feature design (one plan: write out.txt). Assert the terminal
        status set, the run dir lives under .harness, and — when the run completed —
        that out.txt landed directly in target_dir (the direct-edit proof).
    Example: a completed run returns RunResult(status='completed') with
        (target_dir / 'out.txt') present and a run_dir containing '.harness'.
    """
    target = tmp_path / "scratch-app"
    target.mkdir()
    design = _DESIGN.read_text()
    result = await Orchestrator().run(
        target_dir=target,
        design_text=design,
        design_fingerprint=hashlib.sha256(design.encode()).hexdigest(),
        max_iterations=2,
        max_runtime_minutes=20,
        claude_runner=ClaudeDriver(),
        codex_runner=CodexDriver(),
        when=time.localtime(),
    )
    assert result.status in {"completed", "incomplete", "failed"}
    assert ".harness" in result.run_dir
    # Direct-edit proof: there is no copy-sandbox, so a completed tiny-feature run
    # must have written out.txt straight into target_dir.
    if result.status == "completed":
        assert (target / "out.txt").is_file()
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_e2e_real_clis.py -v -m slow`
Expected: FAIL (the new behavior is not implemented yet — attribute/param/value mismatch).

- [ ] **Step 3: Implement**

**Removal / prune notes:**

Within tests/test_e2e_real_clis.py: remove the old test name `test_tiny_feature_end_to_end` and its stale docstring text citing '§17' and 'wave'-era flow (current lines 23-27). No imports are removed (hashlib, shutil, time, Path, pytest, ClaudeDriver, CodexDriver, Orchestrator all stay in use). No fixture/conftest changes — conftest.py only wires scripts/ onto sys.path and is untouched.

Apply:

```python
COMPLETE NEW FILE — tests/test_e2e_real_clis.py (see test_code; it IS the file content). The changes vs the current file (lines 1-43) are three:

1. Rename the test `test_tiny_feature_end_to_end` → `test_tiny_feature_single_plan_direct_edit` and rewrite its docstring to cite §13/§3 single-plan, direct-edit (the current docstring cites the stale 0001 §17 and 'wave'-era framing).

2. Add the direct-edit assertion at the end: when `result.status == 'completed'`, assert `(target / 'out.txt').is_file()`. This is the load-bearing new check — it proves the Generator edited target_dir IN PLACE (no copy-sandbox), which is the core §3/§5 behavior of this refactor. It is guarded by the `completed` branch so the test stays honest/skippable: if the real run does not converge it does not falsely fail.

3. Keep everything else identical — the imports (ClaudeDriver, CodexDriver, Orchestrator), the `pytest.mark.slow` module mark, the `skipif(not (which('claude') and which('codex')))` guard, the `Orchestrator().run(...)` call with the single-plan kwargs (target_dir/design_text/design_fingerprint/max_iterations/max_runtime_minutes/claude_runner/codex_runner/when), and the two existing assertions (status set + '.harness' in run_dir). The call site already uses the single-plan signature from §15, so no signature edit is needed — only the test name, docstring, and the new direct-edit assertion change. The test remains runnable and skippable exactly as today (real CLIs required; slow-marked; excluded from default CI).
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_e2e_real_clis.py -v -m slow`
Expected: PASS (this module's own tests are green).

- [ ] **Step 5: Commit**

```bash
git add 'tests/test_e2e_real_clis.py' 'tests/test_e2e_real_clis.py'
git commit -m "test(e2e): drive the single-plan direct-edit run and assert in-place out.txt"
```

---

### Task 18: Run the full ci.sh gate and make it green — final task of the refactor (§13)

**Files:**
- Test: `scripts/ci.sh`

**Interfaces:**
- Consumes: All prior cluster tasks have landed: models.py/phases.py/engine.py/drivers/state/artifacts/gitguard rewrites (A1–A5), the scheduler.py/sandbox.py deletions and orphan prune (this cluster, task 2), the prompt regeneration (task 1), and the e2e update (task 3). scripts/ci.sh runs: ruff check src tests scripts; ruff format --check src tests scripts; pyright; python scripts/check_docstrings.py src tests scripts; pytest.
- Produces: A green full gate: `bash scripts/ci.sh` exits 0 with ruff check clean, ruff format --check clean, pyright clean (0 errors), check_docstrings.py clean (all non-trivial changed defs carry Design:/Implementation:/Example:), and the full pytest suite passing (the deleted test modules gone, the rewritten modules green, the slow e2e skipped by default). This is the §13 success gate — the ONLY task at which the complete ci.sh is green.

- [ ] **Step 1: Run the full success gate**

Run: `bash scripts/ci.sh` (or `uv run ruff check . && uv run ruff format --check src tests scripts && uv run pyright && uv run python scripts/check_docstrings.py src tests scripts && uv run pytest`)
Expected: PASS — ruff, ruff-format, pyright, docstrings, and the full pytest suite all green.

Reference / fix-up notes:

```text
Do NOT modify scripts/ci.sh — it is already the correct gate (verified):

  #!/usr/bin/env bash
  set -euo pipefail
  uv run ruff check src tests scripts
  uv run ruff format --check src tests scripts
  uv run pyright
  uv run python scripts/check_docstrings.py src tests scripts
  uv run pytest

This task RUNS that gate and resolves any residual failures the cross-cluster integration surfaced, in this exact order (ci.sh is `set -euo pipefail`, so it stops at the first failing stage — fix in order):

  1. `uv run ruff check src tests scripts` — expect PASS. The most likely residual failure is F401 (unused import) from the cross-cluster prune. If ruff reports an unused import in a file YOUR task owns (none here) fix it; if it is in an earlier cluster's file, that cluster's prune was incomplete — report it (§8 lists the exact engine imports to prune: shutil, capture_state, detect_non_progress, fingerprint, apply_amendments, run_verification, the six scheduler + six sandbox symbols, EvalResult/TriageResult/PlanSet, the TYPE_CHECKING GapTriage).

  2. `uv run ruff format --check src tests scripts` — expect PASS. If a rewritten file is mis-formatted, run `uv run ruff format src tests scripts` to normalize, then re-check.

  3. `uv run pyright` — expect '0 errors, 0 warnings, 0 informations'. Likely residuals: a dangling reference to a deleted symbol (Change/Conflict/WriterMap/PlanSet) — these trace to an earlier cluster's incomplete rewrite; report with the file:line pyright prints.

  4. `uv run python scripts/check_docstrings.py src tests scripts` — expect PASS. Every non-trivial new/changed def must carry Design:/Implementation:/Example: with >=5 non-whitespace chars each (§14). Pydantic models and trivial stubs are exempt.

  5. `uv run pytest` — expect all tests passing, the slow e2e (tests/test_e2e_real_clis.py) DESELECTED/SKIPPED by default (no real CLIs in CI; it is `@pytest.mark.slow` + skipif), and zero collection errors (the three deleted test modules must be gone — confirmed by task 2).

Expected final result: `bash scripts/ci.sh` exits 0. This is the single point at which the full gate is green (§11 'the full ci.sh gate is green only at the final task').

This task creates/modifies NO source files. If a stage fails because a file this cluster owns is wrong, fix it here (e.g. a prompt regression breaking test_prompts.py, an e2e edit breaking collection, or an orphan import the deletion task missed). If it fails inside a file owned by an earlier cluster, that is an integration regression to report — do not silently patch another cluster's module, as that would mask an incomplete upstream rewrite.
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "ci: run the full ci.sh gate green for the single-plan direct-edit refactor"
```

---
