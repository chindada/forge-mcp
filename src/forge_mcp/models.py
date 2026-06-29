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


class RemediationResult(BaseModel, extra="forbid"):
    """The remediation contract (Markdown plan) produced by one Remediation turn."""

    contract: str
