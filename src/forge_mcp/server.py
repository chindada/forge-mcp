"""forge-mcp stdio server using low-level Server task support (§C1.4 Path B)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.experimental.task_context import ServerTaskContext
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from mcp.types import (
    TASK_OPTIONAL,
    CallToolResult,
    CreateTaskResult,
    ErrorData,
    ListResourcesResult,
    Resource,
    TextContent,
    Tool,
    ToolExecution,
)
from pydantic import AnyUrl, ValidationError

from . import subscriptions
from .config import RunConfig
from .drivers._claude import ClaudeRunnerImpl
from .drivers._codex import CodexRunnerImpl
from .drivers.evaluator import EvaluatorDriver
from .drivers.generator import GeneratorDriver
from .drivers.planner import PlannerDriver
from .errors import tag
from .ids import is_run_id
from .models import RunForgeInput, RunResult
from .orchestrator import Orchestrator
from .preflight import prepare_run
from .resources import (
    _ResourceScope,
    compute_harness_token,
    decode_uri,
    expand_scope_to_resources,
    list_active_runs,
    match_artifact,
    resolve_harness_dir,
)

INVALID_PARAMS = -32602
SERVER_ERROR = -32000
RUN_FORGE_DESCRIPTION = "Run the Planner / Generator / Evaluator loop (§C1)."

server = Server("forge-mcp")
server.experimental.enable_tasks()

# §R3.3 — startup-bound resource discovery config for completed runs.
try:
    _RESOURCE_CONFIG: RunConfig = RunConfig.from_env()
except ValueError as _exc:
    import sys as _sys

    # §L14 step 5 — stderr hint before re-raising opaque server-spawn failures.
    print(
        f"Invalid forge-mcp config: {_exc}. Run `forge doctor` for diagnostics.",
        file=_sys.stderr,
    )
    raise
_RESOURCE_LOGGER: logging.Logger = logging.getLogger("forge_mcp.resources")
_RESOURCE_NOT_FOUND_CODE = -32002  # §R-Decision 8 — MCP-spec, not exported.


def _resource_not_found(uri: AnyUrl) -> McpError:
    """Build the MCP resource-not-found error shape (§R1.1).

    Design: §R-Decision 8 / §R-Inv 5 require -32002 on every resource reject
        path, distinct from invalid-params and server-error tool failures.
    Implementation: return McpError(ErrorData(...)); callers use `raise` so
        all handler branches share exactly one wire code and message shape.
    Example: raise _resource_not_found(uri).
    """
    return McpError(
        ErrorData(code=_RESOURCE_NOT_FOUND_CODE, message=f"resource not found: {uri}", data=None)
    )


def _note_current_session_connected() -> None:
    """Record the in-flight request's session as connection-level present (§S6).

    Design: S-Decision 10 makes list_changed connection-level; a session must be
        tracked the moment it issues any request so register/deregister
        broadcasts reach it even if it never subscribes to a URI.
    Implementation: read server.request_context.session under a LookupError guard
        (no active request context outside a live call) and note it on the
        module-level subscription registry.
    Example: _note_current_session_connected() at the top of each handler.
    """
    try:
        session = server.request_context.session
    except LookupError:
        return
    subscriptions._REGISTRY.note_connected(session)


class _Drivers:
    """Small container for injected phase drivers.

    Design: §8.1 expects a drivers bundle while keeping construction local to
        the server entrypoint.
    Implementation: instantiate concrete drivers with SDK runner seams.
    Example: drivers = _Drivers(); drivers.planner.write_plan.
    """

    def __init__(self) -> None:
        """Create concrete driver instances.

        Design: each runner seam owns SDK interactions and can be closed by
            lifecycle through the driver attributes.
        Implementation: use separate ClaudeRunnerImpl instances for planner and
            evaluator plus one CodexRunnerImpl for generator.
        Example: _Drivers().generator.
        """
        self.planner = PlannerDriver(ClaudeRunnerImpl())
        self.generator = GeneratorDriver(CodexRunnerImpl())
        self.evaluator = EvaluatorDriver(ClaudeRunnerImpl())


@dataclass(frozen=True)
class ToolSchemaForTests:
    """Compatibility wrapper exposing MCP-style schema attribute names.

    Design: §18 tests pin `inputSchema`/`outputSchema` semantics independent of
        the server registration layer.
    Implementation: copy the low-level Tool schema dictionaries into camelCase
        attributes for tests only.
    Example: tool_for_tests().inputSchema['type'] == 'object'.
    """

    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]


@server.list_resources()
async def list_resources_handler(request: types.ListResourcesRequest) -> ListResourcesResult:
    """Enumerate run artifacts across active and configured harnesses (§R1.1).

    Design: §R1.1 sources are active-run registry first, then optional
        FORGE_HARNESS_ROOTS for completed runs. Pagination is cursor-based and
        eager (R-Decision 7), with OSError logged and skipped per scope/root.
    Implementation: deduplicate scopes by (harness_token, run_id), expand via
        stdlib-only resources.py helpers, sort Resource rows by URI, and slice
        a fixed page size of fifty using a forgiving numeric cursor.
    Example: result = await list_resources_handler(ListResourcesRequest(...)).
    """
    _note_current_session_connected()
    cursor = request.params.cursor if request.params is not None else None
    scopes_by_key: dict[tuple[str, str], _ResourceScope] = {
        (scope.harness_token, scope.run_id): scope for scope in list_active_runs()
    }

    for root_token, root_dir in _RESOURCE_CONFIG.harness_root_tokens.items():
        try:
            run_id_dirs = list(root_dir.iterdir())
        except OSError as exc:
            _RESOURCE_LOGGER.warning("root iterdir failed: %s root=%r", exc, root_dir)
            continue
        for run_id_dir in run_id_dirs:
            if not run_id_dir.is_dir() or not is_run_id(run_id_dir.name):
                continue
            key = (root_token, run_id_dir.name)
            if key in scopes_by_key:
                continue
            scopes_by_key[key] = _ResourceScope(
                run_id=run_id_dir.name,
                harness_dir=root_dir,
                harness_token=root_token,
            )

    rows: list[Resource] = []
    for scope in scopes_by_key.values():
        try:
            for uri_str, name, mime_type, _subpath in expand_scope_to_resources(scope):
                rows.append(Resource(uri=AnyUrl(uri_str), name=name, mimeType=mime_type))
        except OSError as exc:
            _RESOURCE_LOGGER.warning("expand failed: %s scope=%r", exc, scope)
            continue

    rows.sort(key=lambda resource: str(resource.uri))
    page_size = 50
    try:
        start = int(cursor) if cursor is not None else 0
    except ValueError:
        start = 0
    start = max(0, min(start, len(rows)))
    items = rows[start : start + page_size]
    next_cursor = str(start + page_size) if start + page_size < len(rows) else None
    return ListResourcesResult(resources=items, nextCursor=next_cursor)


@server.read_resource()
async def read_resource_handler(uri: AnyUrl) -> list[ReadResourceContents]:
    """Resolve a forge:// URI and serve the artifact's contents (§R1.1).

    Design: §R1.1 read-only access serves one allowlisted artifact under a
        registered or configured harness. §R6 containment runs before reading:
        abspath commonpath, realpath commonpath, leaf symlink rejection, then
        is_file. Every reject path maps to -32002 (R-Inv 5).
    Implementation: decode URI, check allowlist, resolve token to harness dir,
        validate containment, and read JSON strictly while text/log artifacts
        use replacement decoding. No SDK/driver/orchestrator callback occurs.
    Example: await read_resource_handler(AnyUrl('forge://token/run/state.json')).
    """
    _note_current_session_connected()
    return _read_resource_contents_sync(uri)


@server.subscribe_resource()
async def subscribe_resource_handler(uri: AnyUrl) -> None:
    """Validate and register one resource subscription (§S3/§S4).

    Design: subscribe uses the same decode and allowlist gates as read_resource
        so readable URIs and subscribable URIs stay in parity.
    Implementation: decode_uri, match_artifact, then insert the current request
        session into the in-memory subscription registry.
    Example: await subscribe_resource_handler(AnyUrl('forge://t/r/state.json')).
    """
    _note_current_session_connected()
    try:
        _harness_token, _run_id, subpath = decode_uri(str(uri))
    except ValueError:
        raise _resource_not_found(uri) from None
    if match_artifact(subpath) is None:
        raise _resource_not_found(uri)
    subscriptions._REGISTRY.subscribe(server.request_context.session, str(uri))


@server.unsubscribe_resource()
async def unsubscribe_resource_handler(uri: AnyUrl) -> None:
    """Idempotently remove one resource subscription (§S3).

    Design: unsubscribe intentionally does not validate resource allowlisting so
        hosts can safely clean up speculative or stale URI subscriptions.
    Implementation: remove the exact URI string for the current session.
    Example: await unsubscribe_resource_handler(AnyUrl('forge://t/r/state.json')).
    """
    _note_current_session_connected()
    subscriptions._REGISTRY.unsubscribe(server.request_context.session, str(uri))


def _read_resource_contents_sync(uri: AnyUrl) -> list[ReadResourceContents]:
    """Synchronously validate and read one resource artifact (§R6).

    Design: §R6's four containment checks are ordinary filesystem operations;
        keeping them in a sync helper preserves their exact ordering and avoids
        accidental async framework path substitutions.
    Implementation: decode URI, check allowlist, resolve token, run abspath and
        realpath commonpath checks, reject leaf symlinks, require a file, then
        decode content according to MIME.
    Example: contents = _read_resource_contents_sync(AnyUrl('forge://t/r/state.json')).
    """
    try:
        harness_token, run_id, subpath = decode_uri(str(uri))
    except ValueError:
        raise _resource_not_found(uri) from None

    pattern = match_artifact(subpath)
    if pattern is None:
        raise _resource_not_found(uri)

    harness_dir = resolve_harness_dir(harness_token, _RESOURCE_CONFIG.harness_root_tokens)
    if harness_dir is None:
        raise _resource_not_found(uri)

    abs_run_root = Path(os.path.abspath(harness_dir / run_id))
    abs_full = Path(os.path.abspath(harness_dir / run_id / subpath))
    try:
        if os.path.commonpath([str(abs_full), str(abs_run_root)]) != str(abs_run_root):
            raise _resource_not_found(uri)
        real_run_root = Path(os.path.realpath(abs_run_root))
        real_full = Path(os.path.realpath(abs_full))
        if os.path.commonpath([str(real_full), str(real_run_root)]) != str(real_run_root):
            raise _resource_not_found(uri)
    except ValueError:
        raise _resource_not_found(uri) from None
    if abs_full.is_symlink():
        raise _resource_not_found(uri)
    if not abs_full.is_file():
        raise _resource_not_found(uri)

    json_mime = pattern.mime == "application/json"
    try:
        text = abs_full.read_text(encoding="utf-8", errors="strict" if json_mime else "replace")
    except (OSError, UnicodeDecodeError):
        raise _resource_not_found(uri) from None
    return [ReadResourceContents(content=text, mime_type=pattern.mime)]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Advertise run_forge with optional task execution (§C1.4).

    Design: FastMCP lacks taskSupport exposure, so Path B registers the tool on
        the low-level Server API while preserving the same Pydantic schemas.
    Implementation: derive schemas from RunForgeInput and RunResult and attach
        ToolExecution(taskSupport=TASK_OPTIONAL).
    Example: tools = await list_tools(); tools[0].name == 'run_forge'.
    """
    return [
        Tool(
            name="run_forge",
            description=RUN_FORGE_DESCRIPTION,
            inputSchema=RunForgeInput.model_json_schema(),
            outputSchema=RunResult.model_json_schema(),
            execution=ToolExecution(taskSupport=TASK_OPTIONAL),
        )
    ]


@server.call_tool(validate_input=False)
async def handle_tool(name: str, arguments: dict[str, Any]) -> CallToolResult | CreateTaskResult:
    """Dispatch one low-level tool call (§C1.4, §6.3).

    Design: one tool keeps dispatch simple; validation/pre-run errors still map
        to isError text results with no numeric code on the wire.
    Implementation: route run_forge to its handler and return CallToolResult for
        unknown tools so transport tests observe the §6.3 wire shape.
    Example: await handle_tool('run_forge', {'target_dir': '/repo'}).
    """
    if name != "run_forge":
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )
    return await run_forge_handler(arguments)


async def run_forge_handler(arguments: dict[str, Any]) -> CallToolResult | CreateTaskResult:
    """Validate, prepare, then run inline or as an MCP task (§C1.4).

    Design: os.umask(0o077) remains first; both direct and task calls share one
        orchestrator entry so §8.5 cancellation forensics stay centralized.
    Implementation: validate with RunForgeInput, prepare the run, and use
        ctx.experimental.run_task only when the request is task-augmented.
    Example: await run_forge_handler({'target_dir': '/repo', 'design_doc_content': 'x'}).
    """
    os.umask(0o077)
    try:
        inputs = RunForgeInput(**arguments)
    except ValidationError as exc:
        message = tag("invalid_params", str(exc))
        raise McpError(ErrorData(code=INVALID_PARAMS, message=message)) from exc
    config = RunConfig.from_env()
    try:
        prepared = await prepare_run(inputs, config)
        ctx = server.request_context
        # §S6 — track before register_active_run broadcasts.
        subscriptions._REGISTRY.note_connected(ctx.session)
        # §R3.2 — compute once so task-mode and direct-call branches share identity.
        harness_token = compute_harness_token(prepared.harness_dir)
        if _client_requested_task_mode(ctx):

            async def work(task: ServerTaskContext) -> CallToolResult:
                """Run the orchestrator inside the server task context (§C1.4).

                Design: task mode survives client disconnect while preserving
                    fresh phase sessions and normal RunResult construction.
                    §R3.2 threads the precomputed harness_token through.
                Implementation: delegate to _orchestrator_entry and adapt the
                    model to CallToolResult for task completion storage.
                Example: result = await work(task).
                """
                result = await _orchestrator_entry(
                    prepared, inputs, config, ctx, task=task, harness_token=harness_token
                )
                return _result_to_call_tool_result(result)

            return await ctx.experimental.run_task(work)
        result = await _orchestrator_entry(
            prepared, inputs, config, ctx, task=None, harness_token=harness_token
        )
        return _result_to_call_tool_result(result)
    except McpError:
        raise
    except Exception as exc:
        # §W2 / finding 4 — boundary catch-all; BaseException still propagates.
        message = tag("infra_failure", f"run_forge failed: {type(exc).__name__}: {exc}")
        raise McpError(ErrorData(code=SERVER_ERROR, message=message)) from exc


def _client_requested_task_mode(ctx: Any) -> bool:
    """Return True when the request arrived through task augmentation (§C1.4).

    Design: SDK task-mode naming is isolated here because the API is marked
        experimental and may change across mcp releases.
    Implementation: prefer the current ctx.experimental.is_task property and
        fall back to a callable is_task_mode_active if a future SDK exposes it.
    Example: if _client_requested_task_mode(server.request_context): ...
    """
    experimental = getattr(ctx, "experimental", None)
    if experimental is None:
        return False
    is_task = getattr(experimental, "is_task", None)
    if isinstance(is_task, bool):
        return is_task
    is_active = getattr(experimental, "is_task_mode_active", None)
    if callable(is_active):
        try:
            return bool(is_active())
        except Exception:
            return False
    return False


def _extract_task_id(task: ServerTaskContext | None) -> str | None:
    """Best-effort lift of task.task_id or task.id (§C11 risk 9).

    Design: clients know their CreateTaskResult id; this field exists only for
        offline correlation and must not fail a run.
    Implementation: duck-type both observed spellings and require a non-empty
        string before returning.
    Example: task_id = _extract_task_id(task).
    """
    if task is None:
        return None
    tid = getattr(task, "task_id", None) or getattr(task, "id", None)
    return tid if isinstance(tid, str) and tid else None


async def _orchestrator_entry(
    prepared: Any,
    inputs: RunForgeInput,
    config: RunConfig,
    ctx: Any,
    *,
    task: ServerTaskContext | None,
    harness_token: str | None = None,
) -> RunResult:
    """Construct and run Orchestrator with optional task state (§C5).

    Design: server.py is the only runtime owner of MCP task context; engine and
        status receive it as a duck-typed optional value. §R3.2 forwards the
        precomputed harness_token into Orchestrator for resource discovery.
    Implementation: construct fresh concrete drivers and pass task plus
        best-effort task_id and harness_token into Orchestrator.
    Example: result = await _orchestrator_entry(prepared, inputs, config, ctx,
        task=None, harness_token='aBcDeFgHiJkL').
    """
    notifier = subscriptions.RegistryNotifier(subscriptions._REGISTRY)  # §S5.3
    return await Orchestrator(
        prepared,
        inputs,
        config,
        ctx,
        _Drivers(),
        task=task,
        task_id=_extract_task_id(task),
        harness_token=harness_token,
        notifier=notifier,
    ).run()


def _result_to_call_tool_result(result: RunResult) -> CallToolResult:
    """Adapt a RunResult model to low-level CallToolResult (§18).

    Design: direct and task-completion paths expose the same structuredContent
        while failed terminal runs remain normal tool results per §6.3.
    Implementation: model_dump for structuredContent and a short text summary
        for clients that only render content blocks.
    Example: wire = _result_to_call_tool_result(result).
    """
    return CallToolResult(
        content=[TextContent(type="text", text=result.message)],
        structuredContent=result.model_dump(mode="json"),
        isError=False,
    )


def tool_for_tests() -> ToolSchemaForTests:
    """Return the registered run_forge tool schema for tests (§18, §C1.4).

    Design: production code does not inspect registration internals, but tests
        need a stable helper after the Path-B low-level Server migration.
    Implementation: build the same schema dictionaries as list_tools.
    Example: tool_for_tests().outputSchema['type'] == 'object'.
    """
    return ToolSchemaForTests(
        inputSchema=RunForgeInput.model_json_schema(),
        outputSchema=RunResult.model_json_schema(),
    )


# Backward-compatible name for older tests/importers; production uses `server`.
mcp = server
