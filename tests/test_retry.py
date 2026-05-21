"""§11.6 with_schema_retry retries exactly once on OutputSchemaError."""

from __future__ import annotations

import pytest

from forge_mcp.errors import OutputSchemaError
from forge_mcp.orchestrator.retry import with_schema_retry


async def test_passes_through_on_first_success() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[bool] = []

    async def fn(retry: bool) -> str:
        """Return success while recording the retry flag.

        Design: the retry helper contract is observable through the boolean
            passed into this callback.
        Implementation: append the flag and return a fixed string.
        Example: await fn(False) returns 'ok'.
        """
        calls.append(retry)
        return "ok"

    assert await with_schema_retry(fn) == "ok"
    assert calls == [False]


async def test_retries_once_on_schema_error() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[bool] = []

    async def fn(retry: bool) -> str:
        """Fail once then succeed on retry.

        Design: with_schema_retry should call retry=True only after a schema
            parsing exception.
        Implementation: raise OutputSchemaError when retry is False.
        Example: await fn(True) returns 'ok'.
        """
        calls.append(retry)
        if not retry:
            raise OutputSchemaError(raw="x", reason="bad")
        return "ok"

    assert await with_schema_retry(fn) == "ok"
    assert calls == [False, True]


async def test_second_schema_error_propagates() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fn(retry: bool) -> str:
        """Always raise an OutputSchemaError.

        Design: a second schema error must propagate to failure handling.
        Implementation: ignore the retry flag and raise the same error.
        Example: await fn(True) raises OutputSchemaError.
        """
        raise OutputSchemaError(raw="x", reason="bad")

    with pytest.raises(OutputSchemaError):
        await with_schema_retry(fn)


async def test_non_schema_exception_propagates_immediately() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[bool] = []

    async def fn(retry: bool) -> str:
        """Always raise a non-schema exception.

        Design: non-schema errors should not trigger the schema retry path.
        Implementation: record the flag and raise RuntimeError.
        Example: await fn(False) raises RuntimeError.
        """
        calls.append(retry)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await with_schema_retry(fn)
    assert calls == [False]
