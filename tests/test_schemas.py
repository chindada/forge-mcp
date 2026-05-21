from pathlib import Path

from forge_mcp.schemas.eval_result import EVAL_RESULT_SCHEMA
from forge_mcp.schemas.triage_result import TRIAGE_RESULT_SCHEMA


def test_eval_schema_object_root_no_combinator():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert EVAL_RESULT_SCHEMA["type"] == "object"
    for key in ("oneOf", "allOf", "anyOf"):
        assert key not in EVAL_RESULT_SCHEMA
    assert set(EVAL_RESULT_SCHEMA["required"]) == {"no_gaps", "gaps", "summary"}


def test_triage_schema_object_root_no_combinator():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert TRIAGE_RESULT_SCHEMA["type"] == "object"
    for key in ("oneOf", "allOf", "anyOf"):
        assert key not in TRIAGE_RESULT_SCHEMA
    assert set(TRIAGE_RESULT_SCHEMA["required"]) == {"triages", "summary"}


def test_no_removed_removed_tool_surface_schema_file():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert not Path("src/forge_mcp/schemas/removed_removed_tool_surface_decision.py").exists()
