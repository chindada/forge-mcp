"""§11.6 exactly-one-retry helper for schema-bearing driver calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..errors import OutputSchemaError

T = TypeVar("T")


async def with_schema_retry(call: Callable[[bool], Awaitable[T]]) -> T:
    """Run an output-schema-bearing driver call with exactly one retry.

    Design: §11.6 compensates for SDK schema mismatch by retrying once with the
        pinned prompt suffix; repeated schema failure becomes terminal failure.
    Implementation: call with retry=False, catch OutputSchemaError once, then
        call with retry=True and let all exceptions propagate.
    Example: er = await with_schema_retry(lambda r: evaluator.evaluate(ctx, retry=r)).
    """
    try:
        return await call(False)
    except OutputSchemaError:
        return await call(True)


async def with_transient_retry(
    call: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[BaseException], bool],
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Retry a driver call on transient transport errors (§H5).

    Design: transient SDK/transport blips should not end a long run, while
        cancellation and runtime-cap timeout paths must propagate untouched.
    Implementation: invoke call up to max_attempts, back off exponentially for
        predicate-approved exceptions, and re-raise the final/non-transient one.
    Example: await with_transient_retry(call, is_transient=lambda e: True).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
        except (asyncio.CancelledError, TimeoutError):
            raise
        except BaseException as exc:  # noqa: BLE001 - predicate decides transience.
            if not is_transient(exc) or attempt >= max_attempts:
                raise
            await asyncio.sleep(base_delay * 2 ** (attempt - 1))
