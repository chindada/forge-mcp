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
    Example: set(Plan.model_json_schema()['properties'])
        == {'surface', 'verification_command', 'body'}.
    """
    props = set(Plan.model_json_schema()["properties"])
    assert props == {"surface", "verification_command", "body"}


def test_planset_is_deleted():
    """Design: §3 PlanSet is deleted entirely (one plan, no collection).
    Implementation: the symbol must no longer be importable from forge_mcp.models.
    Example: hasattr(forge_mcp.models, 'PlanSet') is False.
    """
    assert not hasattr(models, "PlanSet")
