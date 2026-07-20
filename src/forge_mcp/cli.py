"""`forge` CLI entry point exposing ``serve`` and ``check`` subcommands (§4.4).

Module-level imports are kept SDK-free so that ``forge check`` and
``python -m forge_mcp`` work without the Claude or Codex SDKs installed.
"""

from __future__ import annotations

from pathlib import Path

import typer

from forge_mcp import check as check_mod

app = typer.Typer()


@app.command()
def serve() -> None:
    """Start the forge-mcp MCP stdio server (§4.4).

    Design: §4.4 ``forge serve`` is the operator-facing entry point for launching
        the MCP server; it delegates entirely to ``server.serve()`` so the CLI
        layer stays thin and the server module owns all startup logic.
    Implementation: import ``forge_mcp.server`` at call time (not module level) to
        keep the module SDK-free; call ``server.serve()`` which runs preflight and
        then starts the FastMCP stdio loop.
    Example: ``forge serve`` blocks until stdin closes in a clean environment.
    """
    from forge_mcp import server

    server.serve()


@app.command()
def check(
    target_dir: str | None = typer.Option(None, help="Target directory to probe."),
    live_claude: bool = typer.Option(
        True,
        "--live-claude/--no-live-claude",
        help=(
            "Probe the live Claude session for skill discovery (default on;"
            " --no-live-claude skips it for a fast offline check)."
        ),
    ),
) -> None:
    """Run preflight checks and report each row (§4.4).

    Design: §4.4 ``forge check`` lets operators verify their environment without
        starting the server; it must run SDK-free since SDK availability is itself
        one of the reported rows.
    Implementation: convert *target_dir* to a Path (or None) and pass to
        ``run_checks`` along with *live_claude*; print each Check row as
        ``[STATUS] label: detail``; raise ``typer.Exit(code=1)`` if ``any_fail``
        returns True.  Pass ``--no-live-claude`` to skip the live Claude probe for
        a fast offline check (useful in CI or tests).
    Example: ``forge check --target-dir /tmp`` prints rows and exits 0 on a clean
        machine (or 1 if e.g. the SDK is absent and mapped to FAIL).
        ``forge check --no-live-claude`` skips the live Claude session probe.
    """
    path = Path(target_dir) if target_dir else None
    checks = check_mod.run_checks(path, probe_claude_live=live_claude)
    for row in checks:
        print(f"[{row.status}] {row.label}: {row.detail}")
    if check_mod.any_fail(checks):
        raise typer.Exit(code=1)


def main() -> None:
    """Invoke the ``forge`` Typer app — the console-script and ``python -m`` entry point.

    Design: §4.4 ``pyproject.toml`` declares ``forge = "forge_mcp.cli:main"`` and
        ``__main__.py`` imports this function; a thin wrapper preserves the option
        to add pre-app logic (e.g. logging setup) without touching the Typer app.
    Implementation: call ``app()`` unconditionally; Typer handles argv parsing and
        dispatches to the correct subcommand.
    Example: ``python -m forge_mcp check`` resolves through ``__main__.py`` →
        ``main()`` → ``app()`` → ``check()``.
    """
    app()
