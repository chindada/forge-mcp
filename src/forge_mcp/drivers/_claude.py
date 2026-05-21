"""§10.1 Claude SDK seam — the only place claude_agent_sdk is touched."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

CLAUDE_SETTING_SOURCES = ["user", "project", "local"]
SCHEMA_RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous response did not match the required "
    "output schema. Re-emit the full structured object that satisfies the "
    "schema exactly. Do not include any prose outside the structured channel."
)
_GIT_MUTATION_RE = re.compile(
    r"\bgit\b[^\n;&|]*\b(commit|push|branch|tag|worktree|rebase|reset\s+--hard)\b"
)


@dataclass(frozen=True)
class StructuredResult:
    """Canonical drain of one Claude turn.

    Design: §10.1 prefers the StructuredOutput channel but preserves text for
        fallback parsing and diagnostics.
    Implementation: frozen dataclass with optional structured dict and joined
        text payload.
    Example: StructuredResult(structured={'ok': True}, text='').
    """

    structured: dict | None
    text: str


@dataclass(frozen=True)
class ClaudeTurn:
    """Structured result plus raw SDK messages for recovery paths.

    Design: §11.4 off-cwd-write recovery needs raw tool_use blocks, while most
        callers need only the drained result.
    Implementation: frozen dataclass composed of StructuredResult and messages.
    Example: turn = await runner.run_with_messages(...); turn.messages.
    """

    result: StructuredResult
    messages: list[Any]


@runtime_checkable
class ClaudeRunner(Protocol):
    """Protocol seam for the Claude SDK (§5.2, §C2).

    Design: concrete drivers depend only on this protocol, never SDK imports,
        so tests can substitute small fakes. §C2 adds a forensic-only
        last_session_id read that does not carry live context across phases.
    Implementation: `run` returns drained content; `run_with_messages` also
        returns raw messages for write-recovery helpers; implementations leave
        last_session_id as None when the SDK omits it.
    Example: await runner.run(...); sid = runner.last_session_id.
    """

    last_session_id: str | None

    async def run(self, *, prompt: str, options: Any, system: str) -> StructuredResult: ...
    async def run_with_messages(self, *, prompt: str, options: Any, system: str) -> ClaudeTurn: ...
    async def interrupt(self) -> None: ...
    async def aclose(self) -> None: ...
    def terminate(self) -> None: ...


def build_options(
    *,
    setting_sources: list[str] | None = None,
    output_format: dict | None = None,
    add_dirs: list[Path] | None = None,
    disallowed_tools: tuple[str, ...] = (),
    mcp_servers: dict | None = None,
    cwd: Path | None = None,
    cli_path: Path | None = None,
    run_log_path: Path | None = None,
    hooks: dict | None = None,
) -> Any:
    """Build ClaudeAgentOptions for every Claude call site (§10.1).

    Design: a single chokepoint prevents option drift and threads the §6.5
        Claude CLI runtime hatch into actual SDK calls.
    Implementation: import the SDK lazily and populate only non-None options;
        `run_log_path` is accepted for call-site symmetry; hooks thread §H10.
    Example: build_options(setting_sources=CLAUDE_SETTING_SOURCES).
    """
    from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

    kwargs: dict[str, Any] = {
        "permission_mode": "bypassPermissions",
        "tools": {"type": "preset", "preset": "claude_code"},
    }
    if setting_sources is not None:
        kwargs["setting_sources"] = setting_sources
    if output_format is not None:
        kwargs["output_format"] = output_format
    if add_dirs:
        kwargs["add_dirs"] = [str(path) for path in add_dirs]
    if disallowed_tools:
        kwargs["disallowed_tools"] = list(disallowed_tools)
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if cli_path is not None:
        kwargs["cli_path"] = str(cli_path)
    if hooks is not None:
        kwargs["hooks"] = hooks
    return ClaudeAgentOptions(**kwargs)


async def _deny_git_mutation_hook(
    input_data: Any, tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    """PreToolUse hook denying git-mutating Bash commands (§H10, Rule 11).

    Design: deterministic in-band guard for Claude phases, layered atop prompt
        forbids and post-iteration gitguard checks.
    Implementation: pass non-Bash tools; deny Bash commands matching mutating
        git subcommands with a permissionDecision reason.
    Example: await _deny_git_mutation_hook({'tool_name':'Bash'}, 'id', None).
    """
    _ = (tool_use_id, context)
    if not isinstance(input_data, dict):
        input_data = getattr(input_data, "model_dump", lambda: {})()
    if input_data.get("tool_name") != "Bash":
        return {}
    command = str(input_data.get("tool_input", {}).get("command", ""))
    if not _GIT_MUTATION_RE.search(command):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Rule 11: git mutations are forbidden in target_dir.",
        }
    }


def git_deny_hooks() -> dict:
    """Build the PreToolUse hook map denying git mutations (§H10.2).

    Design: centralizes SDK hook construction in the Claude seam so callers do
        not import SDK hook types directly.
    Implementation: lazily import HookMatcher and match only the Bash tool.
    Example: build_options(hooks=git_deny_hooks()).
    """
    from claude_agent_sdk import HookMatcher  # type: ignore

    return {"PreToolUse": [HookMatcher(matcher="Bash", hooks=cast(Any, [_deny_git_mutation_hook]))]}


def is_transient_error(exc: BaseException) -> bool:
    """Classify a Claude-seam exception as a transient transport fault (§H5.2).

    Design: retry only transport-shaped failures — connection reset, timeout,
        broken pipe, and the SDK's own connection error. Filesystem/permission
        OSErrors and schema/validation/logic errors must propagate immediately
        (§H19 note 5: a mis-classified logic error would retry a doomed call).
    Implementation: match the stdlib transport tuple, then lazily import the
        SDK's CLIConnectionError and match it too; the bare OSError base is
        deliberately NOT in the tuple.
    Example: is_transient_error(ConnectionResetError()) is True.
    """
    if isinstance(exc, (ConnectionError, TimeoutError, BrokenPipeError)):
        return True
    try:
        from claude_agent_sdk import CLIConnectionError  # type: ignore
    except ImportError:
        return False
    return isinstance(exc, CLIConnectionError)


def maybe_append_retry_suffix(prompt: str, retry: bool) -> str:
    """Append the verbatim retry suffix iff retry=True (§11.6).

    Design: schema-bearing calls retry exactly once with pinned wording, so the
        suffix lives in one SDK seam constant.
    Implementation: string concatenation on retry and identity otherwise.
    Example: maybe_append_retry_suffix('p', True).endswith(SCHEMA_RETRY_SUFFIX).
    """
    return prompt + SCHEMA_RETRY_SUFFIX if retry else prompt


def truncate_for_warning(text: str, max_len: int = 1000) -> str:
    """Cap recovery descriptors so warnings stay bounded.

    Design: §11.4 recovery messages are useful but must not bloat RunResult.
    Implementation: return original text under the limit or append a truncation
        marker containing the original length.
    Example: truncate_for_warning('abc', 10) == 'abc'.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...[truncated; total {len(text)} chars]"


def drain_text(messages: list[Any]) -> StructuredResult:
    """Join the canonical assistant answer, structured channel preferred.

    Design: §10.1 output_format may emit only StructuredOutput tool_use blocks,
        so drivers must not rely on plain text alone.
    Implementation: scan content blocks, keep the last StructuredOutput dict,
        and concatenate text block contents as fallback.
    Example: drain_text(messages).structured returns a dict or None.
    """
    structured: dict | None = None
    text_parts: list[str] = []
    for msg in messages:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "name", None) == "StructuredOutput":
                inp = getattr(block, "input", None)
                if isinstance(inp, dict):
                    structured = inp
            elif getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
    return StructuredResult(structured=structured, text="".join(text_parts))


def collect_writes_to_basename(messages: list[Any], basename: str) -> str | None:
    """Recover intended file content from Write tool_use blocks.

    Design: §11.4 recovers when an agent writes plan.md/contract.md outside
        cwd despite explicit instructions.
    Implementation: scan Write blocks, match by final path basename, and return
        the last non-empty content string.
    Example: collect_writes_to_basename(messages, 'plan.md').
    """
    found: str | None = None
    for msg in messages:
        for block in getattr(msg, "content", None) or []:
            if getattr(block, "name", None) != "Write":
                continue
            inp = getattr(block, "input", None) or {}
            if Path(str(inp.get("file_path") or "")).name == basename and inp.get("content"):
                found = str(inp["content"])
    return found


def run_logger(run_dir: Path) -> logging.Logger:
    """Build the per-run file logger.

    Design: §13 keeps run.log private and out of RunResult while preserving
        detailed forensic output on disk.
    Implementation: name loggers by run id, disable propagation, and attach one
        FileHandler for run.log.
    Example: logger = run_logger(Path('.harness/abcd1234')).
    """
    logger = logging.getLogger(f"forge_mcp.run.{run_dir.name}")
    logger.propagate = False
    if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        handler = logging.FileHandler(run_dir / "run.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


class ClaudeRunnerImpl:
    """Production implementation of ClaudeRunner over claude_agent_sdk.

    Design: §5.2 confines direct SDK usage to this seam and creates a fresh
        session per call.
    Implementation: lazily import ClaudeSDKClient, collect all messages, and
        return drained structured/text content.
    Example: await ClaudeRunnerImpl().run(prompt='hi', options=o, system='').
    """

    def __init__(self) -> None:
        """Create an empty runner with no in-flight client.

        Design: a runner can be closed by lifecycle without holding global SDK
            state between calls.
        Implementation: store the current client opportunistically during run.
        Example: runner = ClaudeRunnerImpl().
        """
        self._client: Any | None = None
        self.last_session_id: str | None = None

    def _reset_session_capture(self) -> None:
        """Reset last_session_id at the start of a new Claude call (§C2.2).

        Design: each public Claude turn creates a fresh SDK session, so the
            exposed id must describe only the most recent call.
        Implementation: assign None immediately before opening the stream.
        Example: self._reset_session_capture(); await client.query(...).
        """
        self.last_session_id = None

    def _absorb_system_message(self, msg: object) -> None:
        """Capture session_id from an init SystemMessage fail-soft (§C2.2).

        Design: §C11 risk 4 says missing or malformed SDK fields must not fail
            the run; a null sessions.json entry is useful enough forensic data.
        Implementation: support dict and object messages, require subtype init,
            and ignore all exceptions.
        Example: self._absorb_system_message({'subtype': 'init', 'session_id': 's'}).
        """
        try:
            subtype = msg.get("subtype") if isinstance(msg, dict) else getattr(msg, "subtype", None)
            if subtype != "init":
                return
            sid = (
                msg.get("session_id") if isinstance(msg, dict) else getattr(msg, "session_id", None)
            )
            if isinstance(sid, str) and sid:
                self.last_session_id = sid
        except Exception:
            return

    async def run(self, *, prompt: str, options: Any, system: str) -> StructuredResult:
        """Run one Claude turn and return the drained result.

        Design: most call sites need only structured/text output and should not
            inspect SDK message internals.
        Implementation: delegate to run_with_messages and return `.result`.
        Example: result = await runner.run(prompt='x', options=o, system='s').
        """
        return (await self.run_with_messages(prompt=prompt, options=options, system=system)).result

    async def run_with_messages(self, *, prompt: str, options: Any, system: str) -> ClaudeTurn:
        """Run one Claude turn and retain raw messages for recovery.

        Design: planner/remediation recovery needs Write tool_use blocks while
            preserving fresh-session semantics.
        Implementation: use ClaudeSDKClient as an async context manager, query,
            collect receive_messages, then drain them.
        Example: turn = await runner.run_with_messages(prompt='x', options=o, system='s').
        """
        from claude_agent_sdk import ClaudeSDKClient  # type: ignore

        messages: list[Any] = []
        self._reset_session_capture()
        async with ClaudeSDKClient(options=options) as client:
            self._client = client
            await client.query(prompt=prompt)  # type: ignore[call-arg]
            async for msg in client.receive_messages():
                self._absorb_system_message(msg)
                messages.append(msg)
        self._client = None
        return ClaudeTurn(result=drain_text(messages), messages=messages)

    async def aclose(self) -> None:
        """Grace-close any in-flight session.

        Design: §8.5 lifecycle calls this before escalating to terminate.
        Implementation: SDK context managers own teardown, so this is a no-op.
        Example: await runner.aclose().
        """
        return None

    async def interrupt(self) -> None:
        """Best-effort SDK-native interrupt of an in-flight turn (§H10).

        Design: graceful interrupt before hard teardown improves cancellation
            forensics but must never fail a run.
        Implementation: if a tracked client exposes interrupt(), await it and
            suppress all errors; no client means no-op.
        Example: await runner.interrupt().
        """
        client = self._client
        if client is None:
            return
        interrupt = getattr(client, "interrupt", None)
        if interrupt is None:
            return
        try:
            await interrupt()
        except Exception:
            pass

    def terminate(self) -> None:
        """Force-clear any tracked SDK client.

        Design: §8.5 provides an escalation hook after close timeouts.
        Implementation: best-effort clear because SDK subprocess ownership is
            internal to claude_agent_sdk.
        Example: runner.terminate().
        """
        self._client = None
