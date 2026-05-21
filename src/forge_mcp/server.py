"""forge-mcp stdio server using low-level Server task support (§C1.4 Path B)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from mcp.server import Server
from mcp.server.experimental.task_context import ServerTaskContext
from mcp.shared.exceptions import McpError
from mcp.types import (
    TASK_OPTIONAL,
    CallToolResult,
    CreateTaskResult,
    ErrorData,
    TextContent,
    Tool,
    ToolExecution,
)
from pydantic import ValidationError

from .config import RunConfig
from .drivers._claude import ClaudeRunnerImpl
from .drivers._codex import CodexRunnerImpl
from .drivers.evaluator import EvaluatorDriver
from .drivers.generator import GeneratorDriver
from .drivers.planner import PlannerDriver
from .models import RunForgeInput, RunResult
from .orchestrator import Orchestrator
from .preflight import prepare_run

INVALID_PARAMS = -32602
RUN_FORGE_DESCRIPTION = "Run the Planner / Generator / Evaluator loop (§C1)."

server = Server("forge-mcp")
server.experimental.enable_tasks()


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
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    config = RunConfig.from_env()
    prepared = await prepare_run(inputs, config)
    ctx = server.request_context
    if _client_requested_task_mode(ctx):

        async def work(task: ServerTaskContext) -> CallToolResult:
            """Run the orchestrator inside the server task context (§C1.4).

            Design: task mode survives client disconnect while preserving fresh
                phase sessions and normal RunResult construction.
            Implementation: delegate to _orchestrator_entry and adapt the model
                to CallToolResult for task completion storage.
            Example: result = await work(task).
            """
            result = await _orchestrator_entry(prepared, inputs, config, ctx, task=task)
            return _result_to_call_tool_result(result)

        return await ctx.experimental.run_task(work)
    result = await _orchestrator_entry(prepared, inputs, config, ctx, task=None)
    return _result_to_call_tool_result(result)


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
) -> RunResult:
    """Construct and run Orchestrator with optional task state (§C5).

    Design: server.py is the only runtime owner of MCP task context; engine and
        status receive it as a duck-typed optional value.
    Implementation: construct fresh concrete drivers and pass task plus
        best-effort task_id into Orchestrator.
    Example: result = await _orchestrator_entry(prepared, inputs, config, ctx, task=None).
    """
    return await Orchestrator(
        prepared,
        inputs,
        config,
        ctx,
        _Drivers(),
        task=task,
        task_id=_extract_task_id(task),
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
