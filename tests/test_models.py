# tests/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_mcp.models import (
    EvalGap,
    GapSummary,
    GapTriage,
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
