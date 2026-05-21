import pytest

from forge_mcp.errors import OutputSchemaError


def test_output_schema_error_carries_raw_and_reason():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    err = OutputSchemaError(raw="not json", reason="missing field")
    assert err.raw == "not json"
    assert err.reason == "missing field"
    assert "missing field" in str(err)


def test_output_schema_error_is_exception():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(OutputSchemaError):
        raise OutputSchemaError(raw="", reason="x")
