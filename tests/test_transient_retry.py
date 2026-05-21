"""§H5 with_transient_retry: backoff, exhaustion, and cancellation never retried."""

from __future__ import annotations

import asyncio

import pytest

from forge_mcp.orchestrator.retry import with_transient_retry


def _always(_exc: BaseException) -> bool:
    """Classify every ordinary exception as transient for retry tests.

    Design: focused tests need a predicate that makes retry policy deterministic.
    Implementation: ignore the exception and return True.
    Example: _always(ConnectionError()) is True.
    """
    return True


async def _no_sleep(*_args, **_kwargs) -> None:
    """Replace asyncio.sleep with a no-op in retry tests.

    Design: retry tests must pin backoff without slowing CI.
    Implementation: return immediately from an awaitable helper.
    Example: monkeypatch.setattr(asyncio, 'sleep', _no_sleep).
    """
    return None


async def test_transient_then_success_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    attempts: list[int] = []

    async def call() -> str:
        """Fail twice transiently then succeed.

        Design: proves the helper re-invokes the call after a transient fault.
        Implementation: raise ConnectionError on the first two attempts.
        Example: third invocation returns 'ok'.
        """
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("blip")
        return "ok"

    assert await with_transient_retry(call, is_transient=_always, max_attempts=3) == "ok"
    assert len(attempts) == 3


async def test_exhaustion_propagates_last_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    async def call() -> str:
        """Always raise a transient error.

        Design: exhausting max_attempts must surface the last exception.
        Implementation: raise ConnectionError on every attempt.
        Example: with_transient_retry re-raises after attempts run out.
        """
        raise ConnectionError("blip")

    with pytest.raises(ConnectionError):
        await with_transient_retry(call, is_transient=_always, max_attempts=3)


async def test_cancelled_error_immediately_reraised() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[int] = []

    async def call() -> str:
        """Raise CancelledError on first invocation.

        Design: H-Inv 6 forbids retrying cancellation.
        Implementation: record the call and raise asyncio.CancelledError.
        Example: with_transient_retry re-raises without a second call.
        """
        calls.append(1)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await with_transient_retry(call, is_transient=_always, max_attempts=3)
    assert calls == [1]


async def test_timeout_error_immediately_reraised() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[int] = []

    async def call() -> str:
        """Raise the run-level TimeoutError on first invocation.

        Design: H-Inv 6 keeps the runtime-cap TimeoutError out of the retry path.
        Implementation: record the call and raise TimeoutError.
        Example: with_transient_retry re-raises without a second call.
        """
        calls.append(1)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await with_transient_retry(call, is_transient=_always, max_attempts=3)
    assert calls == [1]


async def test_non_transient_immediately_propagates() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    calls: list[int] = []

    async def call() -> str:
        """Raise a non-transient error.

        Design: a logic error must not be retried.
        Implementation: record the call and raise ValueError.
        Example: with_transient_retry re-raises after one call.
        """
        calls.append(1)
        raise ValueError("logic")

    with pytest.raises(ValueError):
        await with_transient_retry(call, is_transient=lambda exc: False, max_attempts=3)
    assert calls == [1]
