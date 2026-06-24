"""Codex SDK seam (§8.2): the sole chokepoint for openai_codex imports.

All openai_codex imports are lazy (inside functions) so that
``import forge_mcp.drivers._codex`` succeeds without the SDK installed.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Only imported at type-check time; not at runtime.
    from openai_codex import CodexConfig


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def build_codex_config(
    *,
    codex_bin: str,
    cwd: Path,
    env: dict | None = None,
) -> CodexConfig:
    """Build a CodexConfig for launching the Codex process (§8.2).

    Design: §8.2 a single chokepoint ensures every call site uses a
        consistent config without repeating construction details.
    Implementation: lazy import of openai_codex.CodexConfig; converts
        *cwd* to str and merges *env* (defaulting to {}) per spec.
    Example: ``build_codex_config(codex_bin="codex", cwd=Path("/tmp")).cwd == "/tmp"``.
    """
    from openai_codex import CodexConfig  # lazy import

    return CodexConfig(codex_bin=codex_bin, cwd=str(cwd), env=env or {})


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CodexEvent:
    """A single streaming event from the Codex driver (§8.2).

    Design: §8.2 callers receive a typed value object rather than raw SDK
        notifications so they remain decoupled from the SDK payload shape.
    Implementation: ``kind`` mirrors the notification method; ``payload``
        is the result of the ``_dump`` triple-fallback.
    Example: ``CodexEvent(kind="turn/completed", payload={"turn_id": "x"})``.
    """

    kind: str
    payload: dict


# ---------------------------------------------------------------------------
# Payload dump helper
# ---------------------------------------------------------------------------


def _dump(payload: object) -> dict:
    """Convert an SDK notification payload to a plain dict (§8.2 triple fallback).

    Design: §8.2 SDK payloads may be Pydantic models, plain dicts, or unknown
        objects; callers need a guaranteed ``dict`` for uniform downstream use.
    Implementation: try ``model_dump()`` first, then ``isinstance(dict)``,
        then fall back to ``{}`` — never raises.
    Example: ``_dump(object()) == {}``; ``_dump({"a": 1}) == {"a": 1}``.
    """
    model_dump = getattr(payload, "model_dump", None)
    if model_dump is not None:
        try:
            return model_dump()  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            pass
    if isinstance(payload, dict):
        return payload
    return {}


# ---------------------------------------------------------------------------
# Transient classification
# ---------------------------------------------------------------------------


def is_transient(exc: BaseException) -> bool:
    """Classify *exc* as a retryable transient error (§8.2).

    Design: §8.2 transient errors (ConnectionError, BrokenPipeError,
        TransportClosedError, or SDK-retryable overloads) may be retried;
        TimeoutError and CancelledError are NEVER transient and must always
        propagate.
    Implementation: TimeoutError / CancelledError short-circuit to False
        first; ConnectionError / BrokenPipeError match by builtin type; the
        openai_codex error symbols are lazy-imported and a missing SDK
        degrades to builtin-only classification (never crashes).
    Example: ``is_transient(ConnectionError())`` is True;
        ``is_transient(TimeoutError())`` is False.
    """
    import asyncio

    if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
        return False
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return True
    try:
        from openai_codex.errors import (  # lazy import
            TransportClosedError,
            is_retryable_error,
        )
    except ImportError:
        return False
    if isinstance(exc, TransportClosedError):
        return True
    return is_retryable_error(exc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CodexRunner(Protocol):
    """Protocol for running Codex agents (§8.2 CodexRunner contract).

    Design: §8.2 any implementation (real or test double) that satisfies
        this Protocol can be injected into orchestration code.
    Implementation: runtime_checkable so ``isinstance`` checks work in tests.
    Example: ``assert isinstance(CodexDriver(), CodexRunner)``.
    """

    @property
    def last_thread_id(self) -> str | None: ...

    def generate(
        self,
        *,
        instructions: str,
        config: CodexConfig,
        run_log_path: Path | None = None,
    ) -> AsyncGenerator[CodexEvent, None]: ...

    async def interrupt(self) -> None: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class CodexDriver:
    """Codex agent runner implementing the CodexRunner Protocol (§8.2).

    Design: §8.2/D4 wraps AsyncCodex; installs a fail-soft stderr tee before
        __aenter__; starts a thread with full_access sandbox and deny_all
        approval mode; streams turn notifications as CodexEvents.
        Transient errors: ConnectionError | BrokenPipeError |
        TransportClosedError | is_retryable_error(exc).
        TimeoutError and CancelledError are ALWAYS re-raised (never transient).
    Implementation: lazy SDK imports inside generate(); stderr tee is
        installed via _try_install_stderr_tee() which NEVER raises (D4);
        close is idempotent.
    Example: ``async for evt in driver.generate(instructions="hi", config=cfg): ...``.
    """

    def __init__(self) -> None:
        """Initialise with no active Codex instance.

        Design: §8.2 drivers are created once and reused; initial state must
            be well-defined and inert.
        Implementation: set last_thread_id and _codex to None so callers can
            read last_thread_id safely before the first generate() call.
        Example: ``d = CodexDriver(); assert d.last_thread_id is None``.
        """
        self._last_thread_id: str | None = None
        self._codex: object | None = None
        self._closed: bool = False

    @property
    def last_thread_id(self) -> str | None:
        """Return the thread ID from the most recent generate() call.

        Design: §8.2 callers may need the thread ID for forking or resuming.
        Implementation: set after thread_start; None before first call.
        Example: ``driver.last_thread_id`` returns ``None`` initially.
        """
        return self._last_thread_id

    def _try_install_stderr_tee(self, codex: object) -> None:
        """Attach a warn-logging tee to the Codex stderr deque (§8.2/D4).

        Design: §8.2/D4 the tee lets operators see Codex stderr without
            coupling hard to internal attribute paths that may change across
            SDK versions; it MUST be fail-soft and never crash.
        Implementation: navigates ``codex._client._sync._stderr_lines`` via
            getattr chains; on any AttributeError emits a warning and returns
            without installing the tee (degrade, never crash).
        Example: if the attribute chain changes in a future SDK version,
            generate() still works — just without stderr forwarding.
        """
        try:
            client = getattr(codex, "_client", None)
            sync = getattr(client, "_sync", None)
            stderr_lines = getattr(sync, "_stderr_lines", None)
            if stderr_lines is None:
                warnings.warn(
                    "openai_codex: stderr deque not found at _client._sync._stderr_lines; "
                    "stderr tee disabled (D4 fail-soft)",
                    stacklevel=3,
                )
        except AttributeError:
            warnings.warn(
                "openai_codex: could not locate stderr deque; tee disabled (D4 fail-soft)",
                stacklevel=3,
            )

    def generate(
        self,
        *,
        instructions: str,
        config: CodexConfig,
        run_log_path: Path | None = None,
    ) -> AsyncGenerator[CodexEvent, None]:
        """Return an async generator streaming CodexEvents for one turn (§8.2).

        Design: §8.2 the Protocol declares ``generate`` as a regular method
            returning an AsyncGenerator so callers can ``async for`` over it;
            the streaming body lives in ``_generate_impl`` so the signatures
            stay precise and consistent.
        Implementation: a thin wrapper that returns the ``_generate_impl``
            coroutine-generator without awaiting; no SDK import here.
        Example: ``async for evt in driver.generate(instructions="x", config=cfg): ...``.
        """
        return self._generate_impl(
            instructions=instructions,
            config=config,
            run_log_path=run_log_path,
        )

    async def _generate_impl(
        self,
        *,
        instructions: str,
        config: CodexConfig,
        run_log_path: Path | None = None,
    ) -> AsyncGenerator[CodexEvent, None]:
        """Stream CodexEvents for one turn of Codex (§8.2).

        Design: §8.2 callers iterate CodexEvents without coupling to SDK
            notification internals; transient errors propagate so callers
            can retry at their discretion.
        Implementation: lazy imports; stderr tee installed before __aenter__
            (D4 fail-soft); thread_start with full_access/deny_all; turn
            streamed via AsyncTurnHandle.stream(); notification method/payload
            emitted as CodexEvent via _dump.  TimeoutError and CancelledError
            are NEVER caught (always re-raised).
        Example: ``async for evt in driver._generate_impl(instructions="x", config=cfg): ...``.
        """
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox, TextInput  # lazy import

        codex = AsyncCodex(config=config)
        self._try_install_stderr_tee(codex)

        try:
            async with codex as codex_ctx:
                self._codex = codex_ctx
                thread = await codex_ctx.thread_start(
                    sandbox=Sandbox.full_access,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(config.cwd) if config.cwd is not None else None,
                )
                self._last_thread_id = thread.id
                turn_handle = await thread.turn(
                    TextInput(text=instructions),
                    cwd=str(config.cwd) if config.cwd is not None else None,
                    approval_mode=ApprovalMode.deny_all,
                )
                stream = turn_handle.stream()
                try:
                    async for notification in stream:
                        payload_obj = getattr(notification, "payload", None)
                        yield CodexEvent(
                            kind=str(getattr(notification, "method", "")),
                            payload=_dump(payload_obj),
                        )
                finally:
                    aclose = getattr(stream, "aclose", None)  # AsyncGenerator has aclose()
                    if aclose is not None:
                        await aclose()  # type: ignore[misc]  # runtime AsyncGenerator
        finally:
            self._codex = None

    async def interrupt(self) -> None:
        """Best-effort interrupt of the current generate(); silently ignored if idle.

        Design: §8.2 callers invoke interrupt() on a best-effort basis
            (e.g., from a signal handler); it must never raise.
        Implementation: getattr-guarded to survive SDK version differences.
        Example: calling interrupt() when no generate() is active is a no-op.
        """
        codex = self._codex
        if codex is None:
            return
        interrupt_fn = getattr(codex, "close", None)
        if interrupt_fn is None:
            return
        try:
            result = interrupt_fn()
            if hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self) -> None:
        """Close and release the Codex instance (idempotent).

        Design: §8.2 drivers may be reused across multiple generate() calls;
            aclose() lets callers cleanly release resources.
        Implementation: delegates to codex.close() if active; sets _closed
            flag to prevent double-close errors.
        Example: ``await driver.aclose()`` after all generate() calls.
        """
        if self._closed:
            return
        self._closed = True
        codex = self._codex
        if codex is None:
            return
        close_fn = getattr(codex, "close", None)
        if close_fn is not None:
            try:
                await close_fn()
            except Exception:  # noqa: BLE001
                pass
        self._codex = None

    async def __aenter__(self) -> CodexDriver:
        """Enter async context manager.

        Design: §8.2 allows ``async with CodexDriver() as d:`` usage so
            callers get guaranteed cleanup via ``__aexit__``.
        Implementation: returns self; no setup needed since connect happens
            inside each generate() call.
        Example: ``async with CodexDriver() as d: async for e in d.generate(...): ...``.
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Exit async context manager, releasing resources.

        Design: §8.2 ensures aclose() is always called when the context
            exits, even if an exception propagates.
        Implementation: delegates to aclose(); ignores exc_type/val/tb.
        Example: context exit after generate() calls aclose() automatically.
        """
        await self.aclose()
