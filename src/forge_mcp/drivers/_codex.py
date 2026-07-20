"""Codex SDK seam (§8.2): the sole chokepoint for openai_codex imports.

All openai_codex imports are lazy (inside functions) so that
``import forge_mcp.drivers._codex`` succeeds without the SDK installed.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections import deque
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


class _StderrTeeDeque(deque):
    """A bounded deque that mirrors each appended line into a run-log sink (§8.2).

    Design: §8.2 forensic capture of Codex stderr. The SDK keeps its own bounded
        in-memory buffer at ``codex._client._sync._stderr_lines``; swapping in this
        subclass preserves that bounded behaviour (same ``maxlen``) while teeing
        every appended line to ``run.log``. Fail-soft: a write error never disturbs
        the turn.
    Implementation: subclass ``deque`` so the SDK's append-and-evict semantics are
        unchanged; override ``append``/``appendleft``/``extend`` to also write the
        value (one line each) to an open text ``sink``, swallowing OSError/ValueError.
        ``close()`` closes the sink idempotently.
    Example: ``d = _StderrTeeDeque(maxlen=400, sink=fh); d.append("boom")`` buffers
        and tees the line.
    """

    def __init__(self, iterable=(), maxlen: int | None = None, *, sink) -> None:
        """Seed the deque (in-memory only) and remember the tee sink.

        Design: §8.2 the swap must preserve any lines already buffered and the
            original maxlen so the SDK's bounded buffer is unchanged.
        Implementation: delegate to ``deque.__init__(iterable, maxlen)`` (which does
            NOT tee the seed lines), then store the open sink for later appends.
        Example: ``_StderrTeeDeque(existing, maxlen=400, sink=fh)``.
        """
        super().__init__(iterable, maxlen)
        self._sink = sink

    def _tee(self, value: object) -> None:
        """Write one line for *value* to the sink, swallowing any I/O error.

        Design: §8.2 forensic only — a closed/full/broken sink must never break
            the Codex stream.
        Implementation: write ``str(value)`` plus a newline and flush; ignore
            OSError (I/O fault) and ValueError (sink already closed).
        Example: ``self._tee("line")`` appends ``"line\\n"`` to run.log.
        """
        try:
            self._sink.write(f"{value}\n")
            self._sink.flush()
        except (OSError, ValueError):
            pass

    def append(self, value: object) -> None:
        """Tee *value* then append it to the bounded buffer.

        Design: §8.2 the SDK appends stderr lines here; each must reach run.log.
        Implementation: tee first, then ``deque.append`` (preserving maxlen evict).
        Example: ``d.append("err")``.
        """
        self._tee(value)
        super().append(value)

    def appendleft(self, value: object) -> None:
        """Tee *value* then left-append it to the bounded buffer.

        Design: §8.2 cover the appendleft path symmetrically with append.
        Implementation: tee first, then ``deque.appendleft``.
        Example: ``d.appendleft("err")``.
        """
        self._tee(value)
        super().appendleft(value)

    def extend(self, values) -> None:
        """Tee and append each value in *values* (per-item, preserving maxlen).

        Design: §8.2 some SDK paths may batch-extend; tee every line.
        Implementation: iterate and ``append`` each (which tees), rather than the
            C-level batch extend.
        Example: ``d.extend(["a", "b"])`` tees two lines.
        """
        for value in values:
            self.append(value)

    def close(self) -> None:
        """Close the sink idempotently.

        Design: §8.2 the run-log handle opened for the tee must be released when
            the turn ends.
        Implementation: close the sink, swallowing OSError (already closed).
        Example: ``d.close()``.
        """
        try:
            self._sink.close()
        except OSError:
            pass


class CodexDriver:
    """Codex agent runner implementing the CodexRunner Protocol (§8.2).

    Design: §8.2 wraps AsyncCodex; installs a fail-soft stderr tee before
        __aenter__; starts a thread with workspace_write sandbox and deny_all
        approval mode (network access on via the sandbox_workspace_write
        config override); streams turn notifications as CodexEvents.
        Transient errors: ConnectionError | BrokenPipeError |
        TransportClosedError | is_retryable_error(exc).
        TimeoutError and CancelledError are ALWAYS re-raised (never transient).
    Implementation: lazy SDK imports inside generate(); stderr tee is
        installed via _try_install_stderr_tee() which NEVER raises (fail-soft);
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
        self._stderr_tee: _StderrTeeDeque | None = None

    @property
    def last_thread_id(self) -> str | None:
        """Return the thread ID from the most recent generate() call.

        Design: §8.2 callers may need the thread ID for forking or resuming.
        Implementation: set after thread_start; None before first call.
        Example: ``driver.last_thread_id`` returns ``None`` initially.
        """
        return self._last_thread_id

    def _try_install_stderr_tee(self, codex: object, run_log_path: Path | None) -> None:
        """Swap the Codex stderr deque for a run-log-teeing deque (§8.2), fail-soft.

        Design: §8.2 forensic stderr capture: replace the SDK's private bounded
            deque at ``codex._client._sync._stderr_lines`` with a ``_StderrTeeDeque``
            that mirrors each appended line into ``run.log`` while preserving the
            SDK's bounded buffer. This is the single most drift-fragile attribute
            chain in the seam, so it MUST degrade to no-tee (warn-and-continue) and
            never crash.
        Implementation: when ``run_log_path`` is None there is no sink, so skip.
            Otherwise navigate ``_client._sync._stderr_lines``; only when it is a
            ``deque`` open ``run.log`` in append mode and replace the attribute with a
            ``_StderrTeeDeque`` carrying the SAME ``maxlen`` (seeded with the existing
            lines, not re-teed). A missing/wrong-typed deque, or any AttributeError/
            OSError, warns and leaves the SDK untouched. The installed tee is recorded
            on ``self`` so ``_generate_impl`` can close its sink.
        Example: ``_try_install_stderr_tee(codex, run_dir / "run.log")`` tees Codex
            stderr; ``_try_install_stderr_tee(codex, None)`` is a no-op.
        """
        self._stderr_tee = None
        if run_log_path is None:
            return
        try:
            client = getattr(codex, "_client", None)
            sync = getattr(client, "_sync", None)
            existing = getattr(sync, "_stderr_lines", None)
            if sync is None or not isinstance(existing, deque):
                warnings.warn(
                    "openai_codex: stderr deque not found at _client._sync._stderr_lines; "
                    "stderr tee disabled (fail-soft)",
                    stacklevel=3,
                )
                return
            sink = open(run_log_path, "a", encoding="utf-8")
            tee = _StderrTeeDeque(existing, maxlen=existing.maxlen, sink=sink)
            sync._stderr_lines = tee
            self._stderr_tee = tee
        except (AttributeError, OSError):
            warnings.warn(
                "openai_codex: could not install stderr tee; disabled (fail-soft)",
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
            (D4 fail-soft); thread_start with workspace_write/deny_all + network config; turn
            streamed via AsyncTurnHandle.stream(); notification method/payload
            emitted as CodexEvent via _dump.  TimeoutError and CancelledError
            are NEVER caught (always re-raised).
        Example: ``async for evt in driver._generate_impl(instructions="x", config=cfg): ...``.
        """
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox, TextInput  # lazy import

        codex = AsyncCodex(config=config)
        self._try_install_stderr_tee(codex, run_log_path)

        try:
            async with codex as codex_ctx:
                self._codex = codex_ctx
                thread = await codex_ctx.thread_start(
                    sandbox=Sandbox.workspace_write,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(config.cwd) if config.cwd is not None else None,
                    config={"sandbox_workspace_write": {"network_access": True}},
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
            if self._stderr_tee is not None:
                self._stderr_tee.close()
                self._stderr_tee = None

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
