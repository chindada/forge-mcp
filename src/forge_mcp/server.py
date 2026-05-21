"""§5.1 / §6.1 FastMCP server exposing one tool: run_forge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import Field, ValidationError

from .config import RunConfig
from .drivers._claude import ClaudeRunnerImpl
from .drivers._codex import CodexRunnerImpl
from .drivers.evaluator import EvaluatorDriver
from .drivers.generator import GeneratorDriver
from .drivers.planner import PlannerDriver
from .models import RunForgeInput, RunResult
from .orchestrator import Orchestrator
from .preflight import prepare_run

mcp = FastMCP("forge-mcp")
INVALID_PARAMS = -32602


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


@mcp.tool()
async def run_forge(
    target_dir: str,
    design_doc_path: str | None = None,
    design_doc_content: str | None = None,
    max_iterations: Annotated[int, Field(ge=1, le=100)] = 10,
    max_runtime_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 600,
    verify_command: str | None = None,
    verify_timeout_seconds: Annotated[int, Field(ge=1, le=24 * 60 * 60)] = 1800,
    resume: bool = False,
    network_access: bool = True,
    *,
    ctx: Context,
) -> RunResult:
    """Run the Planner→Generator→Evaluator loop on a target_dir (§6.1).

    Design: §6.1 exposes one typed MCP tool; terminal completed/incomplete/
        failed states are normal returns while pre-run validation raises McpError.
    Implementation: set private umask first, validate input, prepare the run,
        build drivers, and delegate to Orchestrator.run.
    Example: await run_forge(target_dir='/repo', design_doc_content='Build x').
    """
    os.umask(0o077)
    try:
        inputs = RunForgeInput(
            target_dir=target_dir,
            design_doc_path=design_doc_path,
            design_doc_content=design_doc_content,
            max_iterations=max_iterations,
            max_runtime_minutes=max_runtime_minutes,
            verify_command=verify_command,
            verify_timeout_seconds=verify_timeout_seconds,
            resume=resume,
            network_access=network_access,
        )
    except ValidationError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    config = RunConfig.from_env()
    prepared = await prepare_run(inputs, config)
    return await Orchestrator(prepared, inputs, config, ctx, _Drivers()).run()


@dataclass(frozen=True)
class ToolSchemaForTests:
    """Compatibility wrapper exposing MCP-style schema attribute names.

    Design: §18 tests pin `inputSchema`/`outputSchema` semantics even though
        FastMCP's internal Tool object uses version-specific attribute names.
    Implementation: copy the internal parameter and output schema dictionaries
        into camelCase attributes for tests only.
    Example: tool_for_tests().inputSchema['type'] == 'object'.
    """

    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]


def tool_for_tests() -> ToolSchemaForTests:
    """Return the registered run_forge tool for schema tests.

    Design: production code does not inspect private tool managers, but §18
        tests need a stable helper to pin schema roots across FastMCP versions.
    Implementation: iterate FastMCP's manager and wrap parameters/output_schema
        in a small compatibility dataclass.
    Example: tool_for_tests().inputSchema['type'] == 'object'.
    """
    for tool in mcp._tool_manager.list_tools():  # noqa: SLF001 - test-only helper.
        if tool.name == "run_forge":
            return ToolSchemaForTests(
                inputSchema=tool.parameters,
                outputSchema=tool.output_schema or {},
            )
    raise AssertionError("run_forge tool missing")
