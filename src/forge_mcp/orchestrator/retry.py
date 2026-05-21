"""§11.6 exactly-one-retry helper for schema-bearing driver calls."""

from __future__ import annotations

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
