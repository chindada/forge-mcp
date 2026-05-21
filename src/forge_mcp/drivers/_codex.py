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
    """Protocol seam for Codex SDK usage (§5.2).

    Design: concrete generator code depends on this seam rather than direct SDK
        imports, preserving a single mocking point.
    Implementation: one `turn` method and lifecycle close/terminate hooks.
    Example: await runner.turn(instructions='go', ...).
    """

    async def turn(
        self,
        *,
        instructions: str,
        server_config: Any,
        sandbox_policy: Any,
        approval_mode: Any,
        env: dict | None,
    ) -> CodexSession: ...
    async def aclose(self) -> None: ...
    def terminate(self) -> None: ...


def build_app_server_config(*, codex_bin: str, cwd: Path, env: dict | None = None) -> Any:
    """Construct the Codex app-server config without MCP servers (§10.2).

    Design: §15 removes MCP server attachments, so no TOML override translator
        or auto-approval helper exists in this seam.
    Implementation: lazily import AppServerConfig and pass executable/cwd/env.
    Example: build_app_server_config(codex_bin='codex', cwd=Path('/repo')).
    """
    from openai_codex import AppServerConfig  # type: ignore

    return AppServerConfig(executable=codex_bin, cwd=str(cwd), env=env or {})  # type: ignore[call-arg]


def sandbox_policy_for(*, target_dir: Path, iteration_dir: Path) -> Any:
    """Return a workspace-write sandbox with network access (§10.2).

    Design: generator may edit target_dir and write iteration artifacts, but no
        other filesystem roots should be writable.
    Implementation: lazily import SandboxPolicy and pass the two writable roots.
    Example: sandbox_policy_for(target_dir=t, iteration_dir=i).
    """
    from openai_codex import SandboxPolicy  # type: ignore

    return SandboxPolicy(
        mode="workspaceWrite",
        writable_roots=[str(target_dir), str(iteration_dir)],
        network_access=True,
    )


def never_approval_mode() -> Any:
    """Return the deny-all approval mode (§10.2).

    Design: generator runs non-interactively under a fixed sandbox policy.
    Implementation: lazy SDK import keeps module import smoke tests independent
        of an installed openai_codex package.
    Example: approval = never_approval_mode().
    """
    from openai_codex import ApprovalMode  # type: ignore

    return ApprovalMode.deny_all


class _RunLogTeeingStderr:
    """Tee Codex subprocess stderr into run.log (§10.2).

    Design: kept after §15 because stderr is load-bearing for debugging
        generator crashes.
    Implementation: best-effort monkey patch of a private SDK deque append;
        missing attributes are ignored.
    Example: with _RunLogTeeingStderr(session, run_log): ...
    """

    def __init__(self, session: Any, run_log_path: Path) -> None:
        """Bind a session and log path for later patching.

        Design: per-session scope avoids global monkey patches.
        Implementation: store original append only after __enter__ succeeds.
        Example: _RunLogTeeingStderr(session, Path('run.log')).
        """
        self._session = session
        self._path = run_log_path
        self._original: Any = None

    def __enter__(self) -> _RunLogTeeingStderr:
        """Install the stderr tee when the SDK exposes the expected deque.

        Design: private SDK structure may change, so this helper degrades
            silently rather than failing a run.
        Implementation: replace deque.append with a wrapper writing run.log.
        Example: with tee: await run().
        """
        try:
            deque = self._session._stderr_deque
            self._original = deque.append

            def _tee(line: str) -> None:
                """Append one stderr line to both sinks.

                Design: preserve SDK behavior while adding forensic logging.
                Implementation: call original append then write a prefixed line.
                Example: _tee('stderr text').
                """
                self._original(line)
                with self._path.open("a") as handle:
                    handle.write(f"[codex-stderr] {line}\n")

            deque.append = _tee  # type: ignore[method-assign]
        except AttributeError:
            pass
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Restore the original append hook on exit.

        Design: local patching must not leak across Codex sessions.
        Implementation: if an original was captured, assign it back best-effort.
        Example: tee.__exit__(None, None, None).
        """
        if self._original is not None:
            try:
                self._session._stderr_deque.append = self._original  # type: ignore[attr-defined]
            except AttributeError:
                pass


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
        Implementation: store active session for aclose/terminate.
        Example: runner = CodexRunnerImpl().
        """
        self._session: Any | None = None

    async def turn(
        self,
        *,
        instructions: str,
        server_config: Any,
        sandbox_policy: Any,
        approval_mode: Any,
        env: dict | None,
    ) -> CodexSession:
        """Spawn one Codex turn under the supplied policy.

        Design: §10.2 generator runs one Codex turn per iteration.
        Implementation: create AsyncCodex, create a thread, start a run, and
            return the streaming session.
        Example: session = await runner.turn(instructions='go', ...).
        """
        from openai_codex import AsyncCodex  # type: ignore

        codex = AsyncCodex(
            app_server_config=server_config,  # type: ignore[call-arg]
            sandbox_policy=sandbox_policy,  # type: ignore[call-arg]
            approval_mode=approval_mode,  # type: ignore[call-arg]
            env=env,  # type: ignore[call-arg]
        )
        thread = await codex.threads.create()  # type: ignore[attr-defined]
        self._session = await thread.runs.create(developer_instructions=instructions)
        return cast(CodexSession, self._session)

    async def aclose(self) -> None:
        """Grace-close the active session (§8.5).

        Design: lifecycle closes SDK resources before releasing locks on
            cancellation/failure paths.
        Implementation: call session.close if present and suppress cleanup
            errors so terminal handling can continue.
        Example: await runner.aclose().
        """
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    def terminate(self) -> None:
        """Force-clear the tracked session (§8.5).

        Design: provides an escalation hook after graceful close timeouts.
        Implementation: best-effort clear because SDK process termination is
            internal to openai_codex.
        Example: runner.terminate().
        """
        self._session = None
