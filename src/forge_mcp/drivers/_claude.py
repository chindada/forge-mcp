"""Claude SDK seam (§8.1): the sole chokepoint for claude_agent_sdk imports.

All SDK imports are lazy (inside functions) so that
``import forge_mcp.drivers._claude`` succeeds without the SDK installed.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from forge_mcp.gitguard import git_deny_matches
from forge_mcp.schemas import envelope

if TYPE_CHECKING:
    # Only imported at type-check time; not at runtime.
    from collections.abc import Callable
    from pathlib import Path

    from claude_agent_sdk import ClaudeAgentOptions


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StructuredResult:
    """Collected output from a single ClaudeDriver.run() call.

    Design: §8.1 callers need the structured JSON payload, the raw text, and
        the session_id in one value object for downstream processing; §10.3
        skill probes additionally need the init message's advertised skills.
    Implementation: plain dataclass; all fields are optional so partial
        results can be returned on error paths; init_skills defaults to None so
        existing constructors and tests are unaffected.
    Example: ``StructuredResult(structured_output={"x": 1}, text="ok", session_id="abc")``.
    """

    structured_output: dict | None
    text: str
    session_id: str | None
    init_skills: list[str] | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ClaudeRunner(Protocol):
    """Protocol for running Claude agents (§8.1 ClaudeRunner contract).

    Design: §8.1 any implementation (real or test double) that satisfies
        this Protocol can be injected into orchestration code.
    Implementation: runtime_checkable so ``isinstance`` checks work in tests.
    Example: ``assert isinstance(ClaudeDriver(), ClaudeRunner)``.
    """

    last_session_id: str | None

    async def run(self, *, prompt: str, options: ClaudeAgentOptions) -> StructuredResult: ...

    async def interrupt(self) -> None: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Hook builder
# ---------------------------------------------------------------------------


def git_deny_hooks() -> dict:
    """Build the PreToolUse hook dict that denies git-mutating Bash commands.

    Design: §8.1/§9 hooks are the real-time defence layer; they run before
        each tool invocation so mutating git commands are blocked immediately
        rather than rolled back after the fact.
    Implementation: returns ``{"PreToolUse": [HookMatcher(matcher="Bash",
        hooks=[deny_cb])]}`` where *deny_cb* delegates the match decision to
        ``forge_mcp.gitguard.git_deny_matches``.  SDK import is lazy.
    Example: ``git_deny_hooks()["PreToolUse"][0].hooks[0]`` is the callback.
    """
    from claude_agent_sdk import HookMatcher  # lazy import

    async def deny_cb(input_data: dict, tool_use_id: str, context: object) -> dict:
        """Deny git-mutating Bash commands; pass everything else.

        Design: §9 defend-in-depth; called by the SDK before each Bash tool use.
        Implementation: extract the command from *input_data* and call
            ``git_deny_matches``; return the deny envelope on match, ``{}`` to pass.
        Example: ``git commit -m x`` → deny; ``ls -la`` → ``{}``.
        """
        command: str = input_data.get("tool_input", {}).get("command", "")
        if git_deny_matches(command):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"git-mutating command blocked by forge-mcp gitguard: {command!r}"
                    ),
                }
            }
        return {}

    return {"PreToolUse": [HookMatcher(matcher="Bash", hooks=[deny_cb])]}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Options builder
# ---------------------------------------------------------------------------


def build_options(
    *,
    skills: str | list[str] = "all",
    setting_sources: tuple[str, ...] | list[str] = ("user", "project", "local"),
    system: str,
    output_format: dict | None = None,
    cwd: str | None = None,
    add_dirs: list[str] | None = None,
    disallowed_tools: tuple[str, ...] | list[str] = (),
    cli_path: str | None = None,
    hooks: dict | None = None,
    stderr: object = None,
) -> ClaudeAgentOptions:
    """Build a fully-configured ClaudeAgentOptions for forge-mcp runs.

    Design: §8.1 single chokepoint so every call site uses consistent
        defaults (bypassPermissions, claude_code preset, etc.) without
        repeating them.
    Implementation: SDK import is lazy.  *output_format* is passed through
        ``envelope()`` so callers can supply either a bare JSON Schema or an
        already-enveloped one — the result is always correct.
    Example: ``build_options(system="you are an agent")`` returns a valid
        ``ClaudeAgentOptions`` with ``permission_mode="bypassPermissions"``.
    """
    from claude_agent_sdk import ClaudeAgentOptions  # lazy import

    kwargs: dict = {
        "permission_mode": "bypassPermissions",
        "tools": {"type": "preset", "preset": "claude_code"},
        "system_prompt": system,
        "skills": skills,
        "setting_sources": list(setting_sources),
        "disallowed_tools": list(disallowed_tools),
    }
    if output_format is not None:
        kwargs["output_format"] = envelope(output_format)
    if cwd is not None:
        kwargs["cwd"] = cwd
    if add_dirs is not None:
        kwargs["add_dirs"] = add_dirs
    if cli_path is not None:
        kwargs["cli_path"] = cli_path
    if hooks is not None:
        kwargs["hooks"] = hooks
    if stderr is not None:
        kwargs["stderr"] = stderr

    return ClaudeAgentOptions(**kwargs)


def run_log_tee(run_log_path: Path) -> Callable[[str], None]:
    """Return a fail-soft callable that tees Claude CLI stderr into run.log (§8.1).

    Design: §8.1 build_options' ``stderr`` is a Callable[[str],None] that tees the
        Claude subprocess stderr to ``run.log`` for forensics; it must never break a
        run, so write failures are swallowed.
    Implementation: return a closure that opens run_log_path in append mode, writes
        the chunk (newline-terminated), and ignores OSError. The per-call open keeps
        the callable stateless so there is no handle to close.
    Example: ``build_options(stderr=run_log_tee(run_dir / "run.log"), ...)``.
    """

    def _tee(chunk: str) -> None:
        """Append one Claude stderr chunk to run.log, swallowing any I/O error.

        Design: §8.1 forensic-only — a write fault must not disturb the run.
        Implementation: open-append-close per chunk; newline-terminate; ignore OSError.
        Example: ``_tee("traceback line")`` appends it to run.log.
        """
        try:
            with open(run_log_path, "a", encoding="utf-8") as fh:
                fh.write(chunk if chunk.endswith("\n") else f"{chunk}\n")
        except OSError:
            pass

    return _tee


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_TRANSIENT = (ConnectionError, BrokenPipeError)
_MAX_RETRIES = 3
_SCHEMA_MISMATCH_SUFFIX = "\n\nRespond with ONLY a valid JSON object matching the requested schema."


class ClaudeDriver:
    """Claude agent runner implementing the ClaudeRunner Protocol (§8.1).

    Design: §8.1/D6 wraps ClaudeSDKClient + receive_response() to drain a
        single response, collecting text, structured output, and session_id.
        Transient errors (ConnectionError, BrokenPipeError, CLIConnectionError)
        are retried up to 3 times with exponential backoff.  TimeoutError and
        CancelledError are NEVER retried.  A schema-mismatch (structured_output
        is None when output_format was requested) is retried once with a
        pinned prompt suffix.
    Implementation: lazy SDK imports inside run(); interrupt() is
        getattr-guarded for best-effort safety; aclose() calls disconnect().
    Example: ``async with ClaudeDriver() as d: result = await d.run(prompt="hi", options=opts)``.
    """

    def __init__(self) -> None:
        """Initialise with no active client.

        Design: §8.1 drivers are created once and reused across multiple
            run() calls; initial state must be well-defined and inert.
        Implementation: set last_session_id and _client to None so callers
            can read last_session_id safely before the first run().
        Example: ``d = ClaudeDriver(); assert d.last_session_id is None``.
        """
        self.last_session_id: str | None = None
        self._client: object | None = None

    async def run(self, *, prompt: str, options: ClaudeAgentOptions) -> StructuredResult:
        """Run *prompt* with *options*, retrying transient failures.

        Design: §8.1 callers expect a StructuredResult even when the
            underlying SDK has transient connectivity issues; they do NOT
            expect retries on timeout or cancellation.
        Implementation: up to _MAX_RETRIES attempts for transient errors
            (ConnectionError | BrokenPipeError | CLIConnectionError); exactly
            one extra attempt when structured_output is None but output_format
            was set; TimeoutError / CancelledError always re-raised immediately.
        Example: on BrokenPipeError attempt 1 retries attempt 2 transparently.
        """
        from claude_agent_sdk import CLIConnectionError, ClaudeSDKClient  # noqa: I001 lazy

        transient_classes = _TRANSIENT + (CLIConnectionError,)
        attempt = 0
        schema_retry_done = False
        current_prompt = prompt

        while True:
            try:
                result = await self._single_run(
                    prompt=current_prompt, options=options, sdk_client_cls=ClaudeSDKClient
                )
            except (TimeoutError, asyncio.CancelledError):
                raise
            except transient_classes:
                attempt += 1
                if attempt >= _MAX_RETRIES:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            # Schema-mismatch retry: expected structured output but got None.
            if (
                options.output_format is not None
                and result.structured_output is None
                and not schema_retry_done
            ):
                schema_retry_done = True
                current_prompt = prompt + _SCHEMA_MISMATCH_SUFFIX
                continue

            return result

    async def _single_run(
        self,
        *,
        prompt: str,
        options: ClaudeAgentOptions,
        sdk_client_cls: type,
    ) -> StructuredResult:
        """Execute one SDK call and drain receive_response().

        Design: §8.1/D6 use receive_response() (not receive_messages()) for
            single-response workflows; capture session_id and the advertised
            skills (§10.3) from the first SystemMessage with subtype 'init'.
        Implementation: concatenate TextBlock.text from AssistantMessages;
            keep the last non-None structured_output from ResultMessages;
            capture data['skills'] fail-soft (missing/odd type -> None).
        Example: a run producing one AssistantMessage and one ResultMessage
            returns StructuredResult with combined text and structured_output.
        """
        from claude_agent_sdk import (  # lazy
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
        )

        text_parts: list[str] = []
        structured_output: dict | None = None
        session_id: str | None = None
        init_skills: list[str] | None = None

        try:
            async with sdk_client_cls(options=options) as client:
                self._client = client
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, SystemMessage):
                        if msg.subtype == "init" and session_id is None:
                            session_id = msg.data.get("session_id")
                            skills = msg.data.get("skills")
                            if isinstance(skills, list):
                                init_skills = skills
                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                text_parts.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        if isinstance(msg.structured_output, dict):
                            structured_output = msg.structured_output
                        if session_id is None and msg.session_id:
                            session_id = msg.session_id
        finally:
            # Clear only after the context manager's disconnect (__aexit__) has
            # run so a concurrent interrupt()/aclose() during teardown still sees
            # the active client. The finally also covers error/exit paths.
            self._client = None

        self.last_session_id = session_id
        return StructuredResult(
            structured_output=structured_output,
            text="".join(text_parts),
            session_id=session_id,
            init_skills=init_skills,
        )

    async def interrupt(self) -> None:
        """Best-effort interrupt of the current run; silently ignored if not running.

        Design: §8.1 callers invoke interrupt() on a best-effort basis
            (e.g., from a signal handler); it must never raise.
        Implementation: getattr-guarded to survive SDK version differences.
        Example: calling interrupt() when no run is active is a no-op.
        """
        client = self._client
        if client is None:
            return
        interrupt_fn = getattr(client, "interrupt", None)
        if interrupt_fn is None:
            return
        try:
            result = interrupt_fn()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self) -> None:
        """Disconnect and release SDK resources.

        Design: §8.1 drivers are used as long-lived objects; aclose() lets
            callers cleanly release resources without relying on garbage
            collection.
        Implementation: delegates to client.disconnect() if a client exists.
        Example: ``await driver.aclose()`` after all runs are complete.
        """
        client = self._client
        if client is None:
            return
        disconnect_fn = getattr(client, "disconnect", None)
        if disconnect_fn is not None:
            try:
                await disconnect_fn()
            except Exception:  # noqa: BLE001
                pass
        self._client = None

    async def __aenter__(self) -> ClaudeDriver:
        """Enter async context manager.

        Design: §8.1 allows ``async with ClaudeDriver() as d:`` usage so
            callers get guaranteed cleanup via ``__aexit__``.
        Implementation: returns self; no setup needed since connect happens
            inside each run() call.
        Example: ``async with ClaudeDriver() as d: await d.run(...)``.
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Exit async context manager, releasing resources.

        Design: §8.1 ensures aclose() is always called when the context
            exits, even if an exception propagates.
        Implementation: delegates to aclose(); ignores exc_type/val/tb since
            we don't suppress exceptions.
        Example: context exit after a run() calls aclose() automatically.
        """
        await self.aclose()
