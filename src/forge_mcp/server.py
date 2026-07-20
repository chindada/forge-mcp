"""FastMCP stdio server exposing the ``run_forge`` tool (§4.1/§4.2/D5).

Importing this module does NOT require the Claude or Codex SDKs — both drivers
are constructed call-time inside ``_run_forge_impl`` so the import tree stays
SDK-free at module load.
"""

from __future__ import annotations

import asyncio
import os
import time
from hashlib import sha256
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from forge_mcp import check
from forge_mcp.models import RunForgeInput, RunResult
from forge_mcp.orchestrator.engine import Orchestrator
from forge_mcp.orchestrator.lifecycle import build_run_result

mcp = FastMCP(name="forge-mcp")


@mcp.tool()
async def run_forge(
    target_dir: str,
    design_doc_path: str | None = None,
    design_doc_content: str | None = None,
    max_iterations: int = 10,
    max_runtime_minutes: int = 600,
    *,
    ctx: Context,
) -> RunResult:
    """Invoke the forge orchestrator on *target_dir* against a design document.

    Design: §4.1/D5 the MCP tool is a thin wrapper around ``_run_forge_impl``
        so the body stays unit-testable without a live MCP transport; the spread
        params produce a flat inputSchema whose top-level property names are the
        ``RunForgeInput`` anchor fields (§17).
    Implementation: delegate immediately to ``_run_forge_impl`` passing all
        params through; the ``ctx`` object is forwarded for advisory progress
        reporting but correctness never depends on it.
    Example: ``run_forge(target_dir='/proj', design_doc_content='# d')`` drives
        a single forge run and returns a ``RunResult``.
    """
    return await _run_forge_impl(
        target_dir=target_dir,
        design_doc_path=design_doc_path,
        design_doc_content=design_doc_content,
        max_iterations=max_iterations,
        max_runtime_minutes=max_runtime_minutes,
        ctx=ctx,
    )


async def _run_forge_impl(
    *,
    target_dir: str,
    design_doc_path: str | None = None,
    design_doc_content: str | None = None,
    max_iterations: int = 10,
    max_runtime_minutes: int | float = 600,
    ctx: object,
) -> RunResult:
    """Core implementation of the run_forge tool — the unit-testable seam.

    Design: §4.1 validate input, resolve design text, enforce the runtime cap
        via ``asyncio.wait_for``, and return an honest ``RunResult``; a
        ``TimeoutError`` from the cap must finalise an ``incomplete`` result
        (never raise out of the tool); a ``CancelledError`` (host disconnect)
        runs cleanup and re-raises; ``ctx`` carries advisory progress only.
    Implementation: construct ``RunForgeInput`` to run the XOR validator; read
        the design file if a path was given, else use the inline content; compute
        ``design_fingerprint`` as sha256 hex; construct ``ClaudeDriver`` and
        ``CodexDriver`` call-time (lazy SDK); wrap the orchestrator coroutine in
        ``asyncio.wait_for(timeout=max_runtime_minutes*60)``; on ``TimeoutError``
        build and return an incomplete ``RunResult``; restore ``os.umask`` in a
        ``finally`` block as the outer umask guard.
    Example: ``await _run_forge_impl(target_dir='/t', design_doc_content='# d',
        max_iterations=1, max_runtime_minutes=0.001, ctx=fake_ctx)`` returns a
        ``RunResult`` with ``status='incomplete'`` when the orchestrator exceeds
        the tiny cap.
    """
    # Validate input (runs XOR validator).
    RunForgeInput(
        target_dir=target_dir,
        design_doc_path=design_doc_path,
        design_doc_content=design_doc_content,
        max_iterations=max_iterations,
        max_runtime_minutes=int(max_runtime_minutes),
    )

    # Resolve design text.
    if design_doc_path is not None:
        text = await asyncio.to_thread(Path(design_doc_path).read_text, encoding="utf-8")
    else:
        assert design_doc_content is not None  # guaranteed by XOR validator
        text = design_doc_content

    design_fingerprint = sha256(text.encode()).hexdigest()

    # Outer umask guard (engine also sets it; this is the server-level wrapper).
    # Set before driver construction so any files a driver constructor creates
    # are also covered by the restrictive umask.
    prev_umask = os.umask(0o077)
    try:
        # Construct drivers call-time (lazy SDK import).
        from forge_mcp.drivers._claude import ClaudeDriver
        from forge_mcp.drivers._codex import CodexDriver

        claude_runner = ClaudeDriver()
        codex_runner = CodexDriver()
        orchestrator = Orchestrator()

        result = await asyncio.wait_for(
            orchestrator.run(
                target_dir=Path(target_dir),
                design_text=text,
                design_fingerprint=design_fingerprint,
                max_iterations=max_iterations,
                max_runtime_minutes=int(max_runtime_minutes),
                claude_runner=claude_runner,
                codex_runner=codex_runner,
                when=time.localtime(),
            ),
            timeout=max_runtime_minutes * 60,
        )
    except TimeoutError:
        result = build_run_result(
            status="incomplete",
            run_dir="",
            iterations=0,
            non_completed=[],
            stop_reason="runtime cap exceeded",
            verified=False,
            summary="Run terminated: runtime cap exceeded before completion.",
        )
    except asyncio.CancelledError:
        raise
    finally:
        os.umask(prev_umask)

    return result


def run_forge_input_schema() -> dict:
    """Return the JSON input schema for the ``run_forge`` MCP tool.

    Design: §4.1/§17 callers (tests, schema export) need the FastMCP-generated
        flat ``inputSchema`` to verify the top-level property names are the
        ``RunForgeInput`` anchor fields without running a live MCP transport.
    Implementation: read the schema off the ``Tool`` object registered in the
        module-level ``mcp`` instance's tool manager; ``get_tool`` returns the
        tool synchronously; access its ``parameters`` attribute which holds the
        JSON Schema dict.
    Example: ``run_forge_input_schema()["properties"]`` contains the key
        ``"target_dir"`` at the top level.
    """
    tool = mcp._tool_manager.get_tool("run_forge")
    if tool is None:  # pragma: no cover
        raise RuntimeError("run_forge tool not registered")
    return tool.parameters


def serve() -> None:
    """Run preflight checks and start the MCP stdio server.

    Design: §4.1/§4.4 preflight checks gate the server from starting when the
        environment is misconfigured; any ``FAIL`` result maps to a tagged error
        raised before the server loop begins so the caller gets a clear signal.
    Implementation: call ``check.run_checks(None)`` (no target dir at serve
        time); if ``check.any_fail`` returns True, raise ``RuntimeError`` with
        the failing check details; then call ``mcp.run()`` to start the stdio
        transport.
    Example: in a clean environment ``serve()`` starts the MCP server and
        blocks until stdin closes.
    """
    checks = check.run_checks(None)
    if check.any_fail(checks):
        failing = [c for c in checks if c.status == "FAIL"]
        details = "; ".join(f"{c.label}: {c.detail}" for c in failing)
        raise RuntimeError(f"forge-mcp preflight FAIL: {details}")
    mcp.run()
