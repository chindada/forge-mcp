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
