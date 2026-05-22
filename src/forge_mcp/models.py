"""All public Pydantic v2 models for forge-mcp (§7).

RemovedDecision is intentionally absent (§15). RunState lives in
`state.py` because it owns the atomic-write contract.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


class RunForgeInput(BaseModel):
    """Validated input accepted by the `run_forge` MCP tool.

    Design: §6.2 keeps the design document source as an xor between a path and
        inline content while bounding iteration and runtime caps. §C11 risk 7
        adds a CALLER-side TTL recommendation: clients invoking run_forge via
        `session.experimental.call_tool_as_task(...)` SHOULD set `ttl` (in
        milliseconds) to at least `max_runtime_minutes * 60 * 1000`, otherwise
        the server may age the task out before the run terminates. This is
        intentionally NOT enforced server-side — TTL semantics are governed by
        the caller's clock, not the server's.
    Implementation: extra fields are forbidden, bounds live on annotated
        fields, and a model validator enforces the document-source xor.
    Example: RunForgeInput(target_dir='/repo', design_doc_content='Build it').
    """

    model_config = ConfigDict(extra="forbid")

    target_dir: str
    design_doc_path: str | None = None
    design_doc_content: str | None = None
    max_iterations: Annotated[int, Field(ge=1, le=100)] = 10
    max_runtime_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 600
    verify_command: str | None = None
    verify_timeout_seconds: Annotated[int, Field(ge=1, le=24 * 60 * 60)] = 1800
    resume: bool = False
    network_access: bool = True

    @model_validator(mode="after")
    def _exactly_one_design_doc(self) -> RunForgeInput:
        """Enforce exactly one design document source (§6.2).

        Design: the wire schema avoids top-level combinators, so xor validation
            happens after Pydantic has parsed the plain object.
        Implementation: compare booleans for path/content presence and reject
            both true or both false with a user-facing ValueError.
        Example: RunForgeInput(target_dir='/t') raises ValidationError.
        """
        has_path = self.design_doc_path is not None
        has_content = self.design_doc_content is not None
        if has_path == has_content:
            raise ValueError(
                "exactly one of design_doc_path or design_doc_content must be provided"
            )
        return self


_WS = re.compile(r"\s+")


class EvalGap(BaseModel):
    """One evaluator-reported implementation gap.

    Design: §7 uses gaps as the durable handoff between evaluator, triage, and
        remediation, so titles must be canonical for matching.
    Implementation: the title validator collapses all whitespace to a single
        space while preserving the rest of the evaluator payload verbatim.
    Example: EvalGap(title='a\n b', severity='high', ...).title == 'a b'.
    """

    title: str = Field(min_length=1)
    severity: str
    design_doc_section: str
    current_state: str
    expected_state: str
    suggested_fix: str

    @field_validator("title", mode="before")
    @classmethod
    def _canon(cls, value: object) -> object:
        """Canonicalize titles for stable triage joins.

        Design: §11.1 title matching is conservative and whitespace-insensitive
            enough to avoid newline drift from model output.
        Implementation: only string inputs are modified; non-strings are left
            for Pydantic to reject or coerce according to normal rules.
        Example: _canon('line1\nline2') returns 'line1 line2'.
        """
        return _WS.sub(" ", value).strip() if isinstance(value, str) else value


class EvalResult(BaseModel):
    """Structured evaluator output for one iteration.

    Design: §7 separates the summary, no-gaps assertion, and concrete gaps so
        the loop can distinguish true completion from malformed optimism.
    Implementation: gaps defaults to an empty list for no-gap payloads and is
        later rendered to both JSON and markdown artifacts.
    Example: EvalResult(no_gaps=True, summary='all requested behavior exists').
    """

    no_gaps: bool
    gaps: list[EvalGap] = Field(default_factory=list)
    summary: str


FaultKind = Literal["contradiction", "infeasibility", "deprecated_dependency", "ambiguity", "other"]


class GapTriage(BaseModel):
    """One triage decision for an evaluator gap.

    Design: §11.1 accepts design-flaw classification only with a fault kind and
        citations; code-bug rows are coerced away from accidental fault data.
    Implementation: branch validation runs after parsing and records coercion
        drift in a PrivateAttr so it never serializes into handoff artifacts.
    Example: GapTriage(gap_title='t', design_fault=False, explanation='bug').
    """

    gap_title: str = Field(min_length=1)
    design_fault: bool
    fault_kind: FaultKind | None = None
    cited_sections: list[str] = Field(default_factory=list)
    explanation: str
    _coercion_log: list[str] = PrivateAttr(default_factory=list)

    @field_validator("gap_title", mode="before")
    @classmethod
    def _canon(cls, value: object) -> object:
        """Canonicalize triage titles for evaluator joins.

        Design: §11.1 pairs triage rows to evaluator gaps by canonical title.
        Implementation: collapse whitespace for strings and otherwise defer to
            Pydantic validation.
        Example: _canon('gap\n name') returns 'gap name'.
        """
        return _WS.sub(" ", value).strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _enforce_branches(self) -> GapTriage:
        """Enforce design-fault versus code-bug branch rules.

        Design: §11.1 requires strict true-branch evidence and conservative
            false-branch coercion to prevent inflated design-flaw claims.
        Implementation: missing kind/citations raise for design faults; code
            bugs drop any fault_kind while recording a drift warning source.
        Example: design_fault=False with fault_kind='ambiguity' becomes None.
        """
        if self.design_fault:
            if self.fault_kind is None:
                raise ValueError("design_fault=True requires fault_kind")
            if not self.cited_sections:
                raise ValueError("design_fault=True requires non-empty cited_sections")
        elif self.fault_kind is not None:
            self._coercion_log.append(
                f"coerced fault_kind={self.fault_kind!r} to None on code-bug row"
            )
            object.__setattr__(self, "fault_kind", None)
        return self


class TriageResult(BaseModel):
    """Structured triage output over evaluator gaps.

    Design: §7 keeps triage rows in their own artifact so design-flaw decisions
        are auditable apart from the evaluator result.
    Implementation: rows default to empty and are matched by title by the pure
        triage policy module.
    Example: TriageResult(triages=[], summary='all gaps are code bugs').
    """

    triages: list[GapTriage] = Field(default_factory=list)
    summary: str


class DesignFlawGap(BaseModel):
    """A promoted evaluator gap classified as a design flaw.

    Design: §7 and §11.1 preserve the original gap plus strict citation data so
        incomplete results can honestly report unresolved design issues.
    Implementation: citation lists require at least one entry; deeper substring
        validation belongs to pure triage policy.
    Example: DesignFlawGap(gap=gap, iteration_n=1, fault_kind='ambiguity', ...).
    """

    gap: EvalGap
    iteration_n: int = Field(ge=1)
    fault_kind: FaultKind
    cited_sections: list[str] = Field(min_length=1)
    explanation: str


class IterationArtifacts(BaseModel):
    """Artifact index entry for one iteration directory.

    Design: §13 exposes durable handoff artifacts while deliberately omitting
        any removed removed decision path from the public result.
    Implementation: required contract path plus optional files discovered by
        result building from iteration-N directories.
    Example: IterationArtifacts(n=1, contract_path='/run/iteration-1/contract.md').
    """

    n: int = Field(ge=1)
    contract_path: str
    summary_path: str | None = None
    eval_json_path: str | None = None
    eval_md_path: str | None = None
    triage_json_path: str | None = None
    git_violation_path: str | None = None
    verify_path: str | None = None
    sessions_path: str | None = Field(
        default=None, description="Path to iteration-N/sessions.json when present (§C2)."
    )
    # §R4.3 — optional forge:// URI companions; populated by build_result only
    # when the orchestrator has a harness_token. R-Inv 3 absence of run_log_uri.
    contract_uri: str | None = None
    summary_uri: str | None = None
    eval_json_uri: str | None = None
    eval_md_uri: str | None = None
    triage_json_uri: str | None = None
    sessions_uri: str | None = Field(
        default=None, description="forge:// URI for iteration-N/sessions.json (§R4.3)."
    )
    git_violation_uri: str | None = None
    verify_uri: str | None = None


class ArtifactIndex(BaseModel):
    """Public artifact paths returned by `run_forge`.

    Design: §13 intentionally omits run.log from the tool boundary because it
        is sensitive, while keeping safe file paths useful to operators.
    Implementation: paths are strings for JSON transport and optional entries
        are set only when their artifacts exist.
    Example: ArtifactIndex(plan_path='/run/plan/plan.md', status_log_path='...').
    """

    plan_path: str
    plan_sessions_path: str | None = Field(
        default=None, description="Path to plan/sessions.json when present (§C2)."
    )
    iterations: list[IterationArtifacts] = Field(default_factory=list)
    status_log_path: str
    state_json_path: str
    git_state_path: str | None = None
    git_uncommitted_path: str | None = None
    unresolved_gaps_overflow_path: str | None = None
    design_flaw_gaps_overflow_path: str | None = None
    # §R4.2 — optional forge:// URI companions. run.log/run_log_uri DELIBERATELY
    # ABSENT (R-Inv 3, allowlist not denylist).
    plan_uri: str | None = None
    plan_sessions_uri: str | None = None
    status_log_uri: str | None = None
    state_json_uri: str | None = None
    git_state_uri: str | None = None
    git_uncommitted_uri: str | None = None
    unresolved_gaps_overflow_uri: str | None = None
    design_flaw_gaps_overflow_uri: str | None = None


class VerificationSummary(BaseModel):
    """Public summary of the last iteration's verification run (§H1).

    Design: §H1 surfaces the deterministic gate's outcome on the tool boundary
        without leaking the bounded output_tail that VerificationOutcome keeps
        for evaluator context and verify.txt.
    Implementation: small object-root model; exit_code is None iff timed_out.
    Example: VerificationSummary(command='uv run pytest', exit_code=0,
        passed=True, timed_out=False).
    """

    command: str
    exit_code: int | None = None
    passed: bool
    timed_out: bool


class RunResult(BaseModel):
    """Normal terminal return shape for the MCP tool.

    Design: §6.3 treats completed, incomplete, and failed runs as normal tool
        returns; only pre-run failures become MCP errors.
    Implementation: failed-only diagnostic fields are optional and traceback is
        bounded before construction to avoid schema errors masking failures.
    Example: RunResult(status='completed', run_id='abcd1234', ...).
    """

    status: Literal["completed", "incomplete", "failed"]
    run_id: str = Field(min_length=8, max_length=8, pattern=r"^[0-9a-f]{8}$")
    task_id: str | None = Field(
        default=None,
        description="Best-effort MCP task id when run via call_tool_as_task (§C1.4, §C3).",
    )
    run_dir: str
    iterations_used: int = Field(ge=0)
    runtime_seconds: int = Field(ge=0)
    completed_phases: list[str] = Field(default_factory=list)
    unresolved_gaps: list[EvalGap] = Field(default_factory=list)
    design_flaw_gaps: list[DesignFlawGap] = Field(default_factory=list)
    artifacts: ArtifactIndex
    warnings: list[str] = Field(default_factory=list)
    message: str
    failed_phase: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    traceback_truncated: str | None = Field(default=None, max_length=4096)
    verification: VerificationSummary | None = None
    resumed_from_iteration: int | None = None
