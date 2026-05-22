"""§14 `forge serve` and `forge doctor` CLI entry points."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions

from . import doctor as doc
from . import subscriptions
from .config import RunConfig
from .drivers._claude import ClaudeRunnerImpl
from .skills import SkillMissingError, probe_required_skills

app = typer.Typer(no_args_is_help=True)


def _server_version() -> str:
    """Return the installed package version for MCP initialization (§S1).

    Design: capability construction needs the same version source as the CLI
        version command without importing package metadata at module import time.
    Implementation: read importlib.metadata.version and fall back to unknown
        when running directly from a source tree.
    Example: _server_version() returns '0.1.0' or 'unknown'.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version("forge-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_init_options() -> InitializationOptions:
    """Build initialization options with honest §S resource capabilities.

    Design: §S1 requires subscribe and listChanged to be advertised true even
        while the installed MCP SDK hardcodes subscribe=False.
    Implementation: get server capabilities with resources_changed=True, patch
        the resources submodel to subscribe=True, then construct options.
    Example: await server.run(read, write, build_init_options()).
    """
    from .server import server

    caps = server.get_capabilities(
        notification_options=NotificationOptions(resources_changed=True),
        experimental_capabilities={},
    )
    if caps.resources is not None:
        caps = caps.model_copy(
            update={"resources": caps.resources.model_copy(update={"subscribe": True})}
        )
    return InitializationOptions(
        server_name="forge-mcp",
        server_version=_server_version(),
        capabilities=caps,
    )


def _drain_subscription_registry() -> None:
    """Drop all subscriptions during server lifespan teardown (§S7).

    Design: stdio sessions are ephemeral, so disconnect must clear the process
        registry and prevent zombie subscriptions from receiving later updates.
    Implementation: iterate a defensive snapshot and call drop_session.
    Example: _drain_subscription_registry() empties subscriptions._REGISTRY.
    """
    for session in list(subscriptions._REGISTRY._sessions_view()):
        subscriptions._REGISTRY.drop_session(session)


@app.command()
def serve() -> None:
    """Boot the stdio MCP server (§14, §C1.4).

    Design: §5.1 exposes forge-mcp over stdio; §C1.4 Path B uses the low-level
        Server API because FastMCP lacks taskSupport exposure.
    Implementation: import server lazily and run it over mcp.server.stdio.
    Example: forge serve.
    """
    from mcp.server.stdio import stdio_server

    from .server import server

    async def _serve_async() -> None:
        """Run the low-level Server on stdio (§C1.4).

        Design: keep Typer's sync command while server.run is async.
        Implementation: open stdio streams, pass §S-aware initialization
            options, and drain subscriptions on disconnect.
        Example: asyncio.run(_serve_async()).
        """
        async with stdio_server() as (read, write):
            try:
                await server.run(read, write, build_init_options())
            finally:
                _drain_subscription_registry()

    asyncio.run(_serve_async())


@app.command()
def doctor() -> None:
    """Run the environment subset of preflight plus disk-space warning (§14).

    Design: §14 — doctor and preflight call shared check functions so
        environment diagnosis cannot drift from runtime behavior. Coverage
        includes target_dir, .harness writable, claude CLI, codex bin+probe,
        skills, claude auth, and a disk-space warn.
    Implementation: print one line per check, run the skill probe via the
        Claude SDK runner, surface SkillMissingError as a FAIL line, and
        exit 1 if any check fails.
    Example: forge doctor.
    """
    try:
        config = RunConfig.from_env()
    except ValueError as exc:
        # §L14 step 5 — bad lineage/env config becomes a normal doctor failure.
        typer.echo(f"[FAIL] env_config: {exc}")
        sys.exit(1)
    checks: list[tuple[str, str, str]] = [
        doc.check_target_dir_writable(Path.cwd()),
        doc.check_harness_writable(Path.cwd() / ".harness"),
        doc.check_disk_space(Path.cwd()),
        doc.check_claude_cli(config),
        asyncio.run(doc.check_codex(config)),
        doc.check_claude_auth(),
    ]
    try:
        asyncio.run(
            probe_required_skills(runner=ClaudeRunnerImpl(), claude_cli_path=config.claude_cli_path)
        )
        checks.append(("skills", "OK", "superpowers:writing-plans"))
    except SkillMissingError as exc:
        checks.append(("skills", "FAIL", str(exc)))
    except Exception as exc:
        checks.append(("skills", "FAIL", f"probe failed: {exc}"))
    fails = 0
    for label, status, detail in checks:
        typer.echo(f"[{status}] {label}: {detail}")
        if status == "FAIL":
            fails += 1
    typer.echo(f"codex_bin: {config.codex_bin}")
    typer.echo(f"claude_config_dir: {config.claude_config_dir}")
    typer.echo(f"claude_cli_path: {config.claude_cli_path}")
    sys.exit(1 if fails else 0)


@app.command()
def version() -> None:
    """Print the installed forge-mcp version (§5.1).

    Design: §5.1 module map — `forge version` is the third Typer
        command. Reading the metadata avoids drift between pyproject and
        a duplicated in-package __version__ constant.
    Implementation: read importlib.metadata.version('forge-mcp') and echo
        it to stdout. Fall back to 'unknown' if metadata is unavailable
        (e.g. running from a non-installed source tree).
    Example: forge version  # prints '0.1.0'.
    """
    import importlib.metadata

    try:
        typer.echo(importlib.metadata.version("forge-mcp"))
    except importlib.metadata.PackageNotFoundError:
        typer.echo("unknown")


def main() -> None:
    """Run the Typer application.

    Design: pyproject's `forge` script and `python -m forge_mcp` share this
        single entrypoint.
    Implementation: delegate directly to the Typer app object.
    Example: main() parses CLI arguments from sys.argv.
    """
    app()
