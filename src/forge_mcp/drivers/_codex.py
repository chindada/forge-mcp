"""§10.2 Codex SDK seam — shrunk after §15 excision (§15)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable


@dataclass(frozen=True)
class CodexEvent:
    """One streamed event from a Codex session (§10.2).

    Design: normalizes event shape for generator status forwarding.
    Implementation: frozen dataclass with kind and dict payload.
    Example: CodexEvent(kind='turn/started', payload={}).
    """

    kind: str
    payload: dict


@runtime_checkable
class CodexSession(Protocol):
    """Protocol for a streaming Codex session.

    Design: §5.2 lets generator tests fake streaming without importing the SDK.
    Implementation: async iterator of events plus close hook.
    Example: async for event in session: ...
    """

    def __aiter__(self) -> AsyncIterator[Any]: ...
    async def close(self) -> None: ...


@runtime_checkable
class CodexRunner(Protocol):
    """Protocol seam for Codex SDK usage (§5.2, §C2).

    Design: concrete generator code depends on this seam rather than direct SDK
        imports, preserving a single mocking point. §C2 adds capture-only
        exposure of the durable Codex thread id.
    Implementation: one `turn` method, lifecycle hooks, and fail-soft
        last_thread_id when the SDK surface lacks `.id`.
    Example: await runner.turn(instructions='go', ...); tid = runner.last_thread_id.
    """

    @property
    def last_thread_id(self) -> str | None:
        """Expose the most recently retained thread id (§C2.2).

        Design: Protocol models a read-only fail-soft forensic property.
        Implementation: concrete runners may compute it from SDK thread state.
        Example: tid = runner.last_thread_id.
        """
        ...

    async def turn(
        self,
        *,
        instructions: str,
        server_config: Any,
        sandbox_config: Any,
        approval_mode: Any,
        env: dict | None,
        run_log_path: Path | None = None,
    ) -> CodexSession: ...
    async def interrupt(self) -> None: ...
    async def aclose(self) -> None: ...
    def terminate(self) -> None: ...


def build_app_server_config(*, codex_bin: str, cwd: Path, env: dict | None = None) -> Any:
    """Construct the Codex launch config without MCP servers (§10.2).

    Design: §15 removes MCP server attachments; A5 uses the real `codex_bin`
        field name instead of the stale executable kwarg. openai-codex 0.132
        renamed AppServerConfig → CodexConfig, keeping the same
        codex_bin/cwd/env constructor kwargs.
    Implementation: lazily import CodexConfig and pass codex_bin/cwd/env.
    Example: build_app_server_config(codex_bin='codex', cwd=Path('/repo')).
    """
    from openai_codex import CodexConfig  # type: ignore

    return CodexConfig(codex_bin=codex_bin, cwd=str(cwd), env=env or {})


def sandbox_config_for(*, target_dir: Path, iteration_dir: Path, network_access: bool) -> dict:
    """Return the workspace-write thread config overrides with a network toggle (§H7).

    Design: §H7 makes network access a convenience knob, not a security
        boundary. openai-codex 0.132 retired the per-turn SandboxPolicy
        tagged-union; the public `sandbox` arg now carries only the coarse
        Sandbox preset, so the detailed writable roots and network flag travel
        as a `sandbox_workspace_write` override on thread_start(config=...).
    Implementation: build the dict[str, Any] config overrides consumed by
        thread_start — sandbox_mode plus sandbox_workspace_write with the two
        writable roots and the network flag (snake_case wire keys, no aliases).
    Example: sandbox_config_for(target_dir=t, iteration_dir=i, network_access=False).
    """
    return {
        "sandbox_mode": "workspace-write",
        "sandbox_workspace_write": {
            "writable_roots": [str(target_dir), str(iteration_dir)],
            "network_access": network_access,
        },
    }


def is_transient_error(exc: BaseException) -> bool:
    """Classify a Codex-seam exception as a transient transport fault (§H5.2).

    Design: retry only transport-shaped failures — connection reset, timeout,
        broken pipe, the SDK's TransportClosedError, and SDK overload errors
        (ServerBusyError / overloaded JsonRpcError via is_retryable_error).
        Filesystem/permission OSErrors and schema/validation/logic errors must
        propagate (§H19 note 5: mis-classified logic errors retry a doomed call).
    Implementation: match the stdlib transport tuple, then lazily import the SDK
        helpers; the bare OSError base is deliberately NOT in the tuple.
    Example: is_transient_error(ConnectionResetError()) is True.
    """
    if isinstance(exc, (ConnectionError, TimeoutError, BrokenPipeError)):
        return True
    try:
        from openai_codex import TransportClosedError, is_retryable_error  # type: ignore
    except ImportError:
        return False
    if isinstance(exc, TransportClosedError):
        return True
    return bool(is_retryable_error(exc))


def never_approval_mode() -> Any:
    """Return the deny-all approval mode (§10.2).

    Design: generator runs non-interactively under a fixed sandbox policy.
    Implementation: lazy SDK import keeps module import smoke tests independent
        of an installed openai_codex package.
    Example: approval = never_approval_mode().
    """
    from openai_codex import ApprovalMode  # type: ignore

    return ApprovalMode.deny_all


def _make_teeing_deque(original: Any, run_log_path: Path) -> Any:
    """Build a deque(maxlen=400) whose append tees to run.log (§13/B7).

    Design: Codex stderr is forensic; the SDK drains an internal deque(maxlen=400)
        from a daemon thread started in __aenter__, so replace it before open.
    Implementation: preserve buffered lines and maxlen; override append to
        write '[codex-stderr] <line>' to run.log fail-soft.
    Example: sync._stderr_lines = _make_teeing_deque(sync._stderr_lines, path).
    """
    import collections

    class _TeeingDeque(collections.deque):
        """Deque subclass that mirrors appended lines to run.log.

        Design: replacing the SDK deque preserves its append contract.
        Implementation: call super().append then append a prefixed log line.
        Example: tee.append('stderr').
        """

        def append(self, line: Any) -> None:  # type: ignore[override]
            """Append one item and tee it to run.log.

            Design: forensic logging must be best-effort only.
            Implementation: suppress OSError after preserving deque behavior.
            Example: tee.append('boom').
            """
            super().append(line)
            try:
                with run_log_path.open("a") as handle:
                    handle.write(f"[codex-stderr] {line}\n")
            except OSError:
                pass

    teeing = _TeeingDeque(maxlen=getattr(original, "maxlen", 400))
    teeing.extend(original)
    return teeing


class _CodexStreamSession:
    """Adapt an AsyncTurnHandle stream into forge's CodexEvent iterator (A6).

    Design: §10.2 generator consumes event.kind/payload; A6 maps ev.method to
        CodexEvent.kind and closes AsyncCodex when streaming ends.
    Implementation: normalize dict/model_dump payloads and delegate closing to
        the runner's idempotent close helper.
    Example: async for event in _CodexStreamSession(...): ...
    """

    def __init__(self, *, codex: Any, handle: Any, runner: CodexRunnerImpl) -> None:
        """Bind the open codex, turn handle, and owning runner.

        Design: the session owns success-path cleanup of the codex it streams.
        Implementation: store references for iteration and close.
        Example: _CodexStreamSession(codex=c, handle=h, runner=r).
        """
        self._codex = codex
        self._handle = handle
        self._runner = runner

    async def __aiter__(self) -> AsyncIterator[CodexEvent]:
        """Yield normalized CodexEvents, closing the codex on exhaustion.

        Design: A6 maps method to kind; A6-lifecycle closes on stream end.
        Implementation: model_dump pydantic payloads, pass dicts, otherwise {}.
        Example: async for ev in session: ...
        """
        try:
            async for ev in self._handle.stream():
                payload = getattr(ev, "payload", None)
                model_dump = getattr(payload, "model_dump", None)
                if callable(model_dump):
                    dumped = model_dump()
                    payload_dict = dumped if isinstance(dumped, dict) else {}
                elif isinstance(payload, dict):
                    payload_dict = payload
                else:
                    payload_dict = {}
                yield CodexEvent(kind=getattr(ev, "method", ""), payload=payload_dict)
        finally:
            await self._runner._close_codex(self._codex)

    async def close(self) -> None:
        """Idempotently close the underlying codex (§8.5 path symmetry).

        Design: lifecycle close paths must be idempotent.
        Implementation: delegate to the runner's one-shot _close_codex.
        Example: await session.close().
        """
        await self._runner._close_codex(self._codex)


class CodexRunnerImpl:
    """Production CodexRunner over openai_codex (§10.2).

    Design: §5.2 confines direct Codex SDK usage to this seam.
    Implementation: lazy import, create a thread/run per turn, and keep the
        active session for lifecycle cleanup.
    Example: await CodexRunnerImpl().turn(instructions='go', ...).
    """

    def __init__(self) -> None:
        """Construct an empty runner.

        Design: no SDK session exists until a generator turn starts.
        Implementation: store active session/codex for aclose/terminate.
        Example: runner = CodexRunnerImpl().
        """
        self._session: Any | None = None
        self._thread: Any | None = None
        self._codex: Any | None = None
        self._closed = False

    @property
    def last_thread_id(self) -> str | None:
        """Return the retained AsyncThread.id when available (§C2.2).

        Design: Codex thread ids are forensic-only in this round and must never
            terminate a run if the pinned SDK changes shape.
        Implementation: getattr with a None default plus str validation keeps
            malformed values out of sessions.json.
        Example: tid = runner.last_thread_id.
        """
        tid = getattr(self._thread, "id", None)
        return tid if isinstance(tid, str) and tid else None

    async def turn(
        self,
        *,
        instructions: str,
        server_config: Any,
        sandbox_config: Any,
        approval_mode: Any,
        env: dict | None,
        run_log_path: Path | None = None,
    ) -> CodexSession:
        """Spawn one Codex turn under the supplied sandbox config.

        Design: §10.2 generator runs one Codex turn per iteration. openai-codex
            0.132 carries the coarse sandbox preset via the Sandbox enum and the
            §H7 writable-roots/network policy via thread_start(config=...); the
            per-turn SandboxPolicy union of 0.131 is gone, so the detailed config
            is applied at thread start, not on the turn.
        Implementation: open AsyncCodex(config=...), thread_start with the
            workspace-write preset + sandbox_config overrides + approval mode,
            run one turn, and return a stream wrapper that closes on exhaustion.
        Example: session = await runner.turn(instructions='go', ...).
        """
        from openai_codex import AsyncCodex, Sandbox, TextInput  # type: ignore

        _ = env
        self._closed = False
        codex = AsyncCodex(config=server_config)
        self._install_stderr_tee(codex, run_log_path)
        await codex.__aenter__()
        cwd = getattr(server_config, "cwd", None)
        try:
            thread = await codex.thread_start(
                sandbox=Sandbox.workspace_write,
                approval_mode=approval_mode,
                cwd=cwd,
                config=sandbox_config,
            )
            self._thread = thread
            handle = await thread.turn(
                TextInput(text=instructions),
                cwd=cwd,
                approval_mode=approval_mode,
            )
        except BaseException:
            await self._close_codex(codex)
            raise
        session = _CodexStreamSession(codex=codex, handle=handle, runner=self)
        self._session = session
        self._codex = codex
        return cast(CodexSession, session)

    async def _close_codex(self, codex: Any) -> None:
        """Idempotently close the AsyncCodex (A6-lifecycle).

        Design: all close paths share a one-shot guard so double-close is a no-op.
        Implementation: guard with self._closed and suppress close errors.
        Example: await runner._close_codex(codex).
        """
        if self._closed or codex is None:
            return
        self._closed = True
        try:
            await codex.close()
        except Exception:
            pass

    def _install_stderr_tee(self, codex: Any, run_log_path: Path | None) -> None:
        """Replace codex._client._sync._stderr_lines with a teeing deque (B7).

        Design: §13 installs before __aenter__ so the drain thread appends to
            the teeing deque; shifted private SDK layout is non-fatal.
        Implementation: guard private attributes with AttributeError and swap
            in _make_teeing_deque on success.
        Example: self._install_stderr_tee(codex, Path('run.log')).
        """
        if run_log_path is None:
            return
        try:
            sync = codex._client._sync
            sync._stderr_lines = _make_teeing_deque(sync._stderr_lines, run_log_path)
        except AttributeError:
            pass

    async def aclose(self) -> None:
        """Grace-close the active session (§8.5).

        Design: lifecycle closes SDK resources before releasing locks on
            cancellation/failure paths and is idempotent with stream cleanup.
        Implementation: delegate to the one-shot _close_codex and clear refs.
        Example: await runner.aclose().
        """
        await self._close_codex(self._codex)
        self._session = None

    async def interrupt(self) -> None:
        """Best-effort SDK-native interrupt of the active Codex run (§H10).

        Design: interrupt before hard teardown lets an in-flight turn flush
            partial state; missing SDK support is a safe no-op.
        Implementation: call session.interrupt() or session.cancel() when
            present, awaiting awaitable results and suppressing errors.
        Example: await runner.interrupt().
        """
        session = self._session
        if session is None:
            return
        interrupt = getattr(session, "interrupt", None) or getattr(session, "cancel", None)
        if interrupt is None:
            return
        try:
            result = interrupt()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            pass

    def terminate(self) -> None:
        """Force-clear the tracked session (§8.5).

        Design: provides an escalation hook after graceful close timeouts.
        Implementation: best-effort clear because SDK process termination is
            internal to openai_codex.
        Example: runner.terminate().
        """
        self._session = None
        self._codex = None
