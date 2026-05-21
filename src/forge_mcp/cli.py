"""§14 `forge serve` and `forge doctor` CLI entry points."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from . import doctor as doc
from .config import RunConfig
from .drivers._claude import ClaudeRunnerImpl
from .skills import SkillMissingError, probe_required_skills

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve() -> None:
    """Boot the stdio MCP server (§14).

    Design: §5.1 exposes forge-mcp over FastMCP stdio transport.
    Implementation: import server lazily and run FastMCP with stdio transport.
    Example: forge serve.
    """
    from .server import mcp

    mcp.run(transport="stdio")


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
    config = RunConfig.from_env()
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
