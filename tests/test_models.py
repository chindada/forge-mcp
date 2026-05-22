import pytest
from pydantic import ValidationError

from forge_mcp.models import (
    ArtifactIndex,
    DesignFlawGap,
    EvalGap,
    EvalResult,
    GapTriage,
    IterationArtifacts,
    RunForgeInput,
    RunResult,
    TriageResult,
)


def test_xor_both_rejected():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/t", design_doc_path="/d.md", design_doc_content="x")


def test_xor_neither_rejected():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/t")


def test_xor_accepts_path():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    m = RunForgeInput(target_dir="/t", design_doc_path="/d.md")
    assert m.design_doc_content is None


def test_xor_accepts_content():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    m = RunForgeInput(target_dir="/t", design_doc_content="x")
    assert m.design_doc_path is None


def test_extra_forbidden():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunForgeInput.model_validate(
            {"target_dir": "/t", "design_doc_content": "x", "extra": "nope"}
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_iterations": 0},
        {"max_iterations": 101},
        {"max_runtime_minutes": 0},
        {"max_runtime_minutes": 24 * 60 + 1},
    ],
)
def test_caps_bounded(kwargs):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/t", design_doc_content="x", **kwargs)


def test_evalgap_title_newlines_collapsed():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    g = EvalGap(
        title="line1\nline2",
        severity="high",
        design_doc_section="§6",
        current_state="a",
        expected_state="b",
        suggested_fix="c",
    )
    assert "\n" not in g.title
    assert g.title == "line1 line2"


def test_gaptriage_design_fault_requires_kind_and_citations():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        GapTriage(
            gap_title="t",
            design_fault=True,
            fault_kind=None,
            cited_sections=[],
            explanation="x",
        )
    with pytest.raises(ValidationError):
        GapTriage(
            gap_title="t",
            design_fault=True,
            fault_kind="ambiguity",
            cited_sections=[],
            explanation="x",
        )


def test_gaptriage_code_bug_coerces():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    g = GapTriage(
        gap_title="t",
        design_fault=False,
        fault_kind="ambiguity",
        cited_sections=["§5"],
        explanation="x",
    )
    assert g.fault_kind is None
    assert any("ambiguity" in line for line in g._coercion_log)


def test_runresult_traceback_bound_enforced():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ai = ArtifactIndex(plan_path="/p", status_log_path="/s", state_json_path="/j")
    with pytest.raises(ValidationError):
        RunResult(
            status="failed",
            run_id="abcd1234",
            run_dir="/p",
            iterations_used=0,
            runtime_seconds=1,
            artifacts=ai,
            message="m",
            traceback_truncated="x" * 4097,
        )


def test_runresult_run_id_pattern():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ai = ArtifactIndex(plan_path="/p", status_log_path="/s", state_json_path="/j")
    with pytest.raises(ValidationError):
        RunResult(
            status="completed",
            run_id="ZZZZZZZZ",
            run_dir="/p",
            iterations_used=1,
            runtime_seconds=1,
            artifacts=ai,
            message="ok",
        )


def test_artifact_index_has_no_run_log_field():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert "run_log_path" not in ArtifactIndex.model_fields


def test_iteration_artifacts_no_removed_removed_tool_surface():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert "removed_removed_tool_surface_decision_path" not in IterationArtifacts.model_fields


def test_runresult_no_removed_removed_tool_surface_decisions():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert "removed_removed_tool_surface_decisions" not in RunResult.model_fields


def test_designflawgap_requires_citations():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    gap = EvalGap(
        title="t",
        severity="high",
        design_doc_section="§6",
        current_state="a",
        expected_state="b",
        suggested_fix="c",
    )
    with pytest.raises(ValidationError):
        DesignFlawGap(
            gap=gap,
            iteration_n=1,
            fault_kind="ambiguity",
            cited_sections=[],
            explanation="x",
        )


def test_models_imported_for_public_api():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert EvalResult(no_gaps=True, summary="ok").gaps == []
    assert TriageResult(summary="ok").triages == []


def test_run_forge_input_new_optional_fields_default() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    inp = RunForgeInput(target_dir="/repo", design_doc_content="d")
    assert inp.verify_command is None
    assert inp.verify_timeout_seconds == 1800
    assert inp.resume is False
    assert inp.network_access is True


def test_verify_timeout_seconds_bounds_enforced() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunForgeInput(target_dir="/repo", design_doc_content="d", verify_timeout_seconds=0)
    with pytest.raises(ValidationError):
        RunForgeInput(
            target_dir="/repo", design_doc_content="d", verify_timeout_seconds=24 * 60 * 60 + 1
        )


def test_long_run_continuity_optional_fields_are_in_schema():
    """§C3 optional task/session path fields appear without being required.

    Design: clients can discover task_id and sessions paths while old payloads
        remain constructible.
    Implementation: inspect Pydantic JSON schemas for properties and required.
    Example: 'task_id' in RunResult.model_json_schema()['properties'].
    """
    assert "task_id" in RunResult.model_json_schema()["properties"]
    assert "task_id" not in RunResult.model_json_schema().get("required", [])
    assert "sessions_path" in IterationArtifacts.model_json_schema()["properties"]
    assert "sessions_path" not in IterationArtifacts.model_json_schema().get("required", [])
    assert "plan_sessions_path" in ArtifactIndex.model_json_schema()["properties"]
    assert "plan_sessions_path" not in ArtifactIndex.model_json_schema().get("required", [])


def test_iteration_artifacts_uri_companions_default_none():
    """§R4.3 optional companions stay None by default.

    Design: URI companions are additive and must not break old payloads.
    Implementation: instantiate with only existing required path fields.
    Example: IterationArtifacts(...).contract_uri is None.
    """
    from forge_mcp.models import IterationArtifacts

    art = IterationArtifacts(n=1, contract_path="/x/iteration-1/contract.md")
    assert art.contract_uri is None
    assert art.summary_uri is None
    assert art.eval_json_uri is None
    assert art.eval_md_uri is None
    assert art.triage_json_uri is None
    assert art.sessions_uri is None
    assert art.git_violation_uri is None
    assert art.verify_uri is None


def test_iteration_artifacts_uri_companions_set():
    """§R4.3 explicit URI assignment works.

    Design: Result builders can populate URI companions when tokenized.
    Implementation: pass a contract_uri through the Pydantic model.
    Example: art.contract_uri starts with forge://.
    """
    from forge_mcp.models import IterationArtifacts

    art = IterationArtifacts(
        n=1,
        contract_path="/x/iteration-1/contract.md",
        contract_uri="forge://aBcDeFgHiJkL/12345678/iteration-1/contract.md",
    )
    assert art.contract_uri == "forge://aBcDeFgHiJkL/12345678/iteration-1/contract.md"


def test_artifact_index_uri_companions_default_none():
    """§R4.2 artifact-index URI companions default to None.

    Design: URI companions are optional and nullable in the schema.
    Implementation: construct ArtifactIndex with pre-existing required paths.
    Example: ArtifactIndex(...).plan_uri is None.
    """
    from forge_mcp.models import ArtifactIndex

    idx = ArtifactIndex(
        plan_path="/x/plan/plan.md",
        status_log_path="/x/status.log",
        state_json_path="/x/state.json",
    )
    assert idx.plan_uri is None
    assert idx.plan_sessions_uri is None
    assert idx.status_log_uri is None
    assert idx.state_json_uri is None
    assert idx.git_state_uri is None
    assert idx.git_uncommitted_uri is None
    assert idx.unresolved_gaps_overflow_uri is None
    assert idx.design_flaw_gaps_overflow_uri is None


def test_artifact_index_has_no_run_log_uri():
    """§R-Inv 3 keeps run.log structurally unreachable.

    Design: The public result is allowlist-based and excludes run.log.
    Implementation: inspect Pydantic model fields for absent path and URI.
    Example: 'run_log_uri' not in ArtifactIndex.model_fields.
    """
    from forge_mcp.models import ArtifactIndex

    assert "run_log_uri" not in ArtifactIndex.model_fields
    assert "run_log_path" not in ArtifactIndex.model_fields
