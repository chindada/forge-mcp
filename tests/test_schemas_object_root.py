"""§18 input/output schemas have object roots and no top-level combinators."""

from __future__ import annotations

from forge_mcp.server import tool_for_tests


def test_input_schema_no_top_level_combinators() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    schema = tool_for_tests().inputSchema
    assert schema.get("type") == "object"
    for combinator in ("oneOf", "allOf", "anyOf"):
        assert combinator not in schema


def test_output_schema_object_root_no_combinators() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    schema = tool_for_tests().outputSchema
    assert schema.get("type") == "object"
    for combinator in ("oneOf", "allOf", "anyOf"):
        assert combinator not in schema


def test_output_format_schemas_are_object_root() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.schemas.eval_result import EVAL_RESULT_SCHEMA
    from forge_mcp.schemas.triage_result import TRIAGE_RESULT_SCHEMA

    assert EVAL_RESULT_SCHEMA["type"] == "object"
    assert TRIAGE_RESULT_SCHEMA["type"] == "object"
