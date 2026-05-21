# FORGE MCP

forge-mcp is a stdio MCP server that orchestrates an autonomous Planner → Generator → Evaluator loop for implementing features from design documents. It exposes a single `run_forge` tool that blocks until completion and streams progress updates, persisting all artifacts under `<target_dir>/.harness/<run-id>/`.

## Installation Steps

**1. Clone and install dependencies:**

```sh
git clone https://github.com/chindada/forge-mcp.git
cd forge-mcp
uv sync
```

**2. Install the codex binary:**

```sh
npm install -g @openai/codex
```

**3. Verify environment:**

```sh
uv run forge doctor
```

The doctor command runs seven checks: `target_dir` writable, `.harness` writable, disk-space (warn-only), `claude` CLI, `codex` binary + import + live model probe, Claude auth, and the required `superpowers:writing-plans` skill. Exit code 0 means success; any `[FAIL]` must be addressed before proceeding.

**4. Register with Claude Code:**

```sh
claude mcp add forge-mcp -- uv run --directory $PWD forge serve
```

```sh
claude mcp add --scope user forge-mcp \
  -e CLAUDE_CONFIG_DIR=$HOME/.claude \
  -e FORGE_CODEX_BIN=$(which codex) \
  -- uv run --directory $PWD forge serve
```

Place the server name before any `-e` environment flags to avoid parsing errors.

**5. Run the example:**

```sh
# In Claude Code, ask forge-mcp to process examples/tiny-feature.md
# Expected runtime: ~5-10 minutes, resulting in a working /healthz route.
```

**6. Optional project instructions:**
Codex auto-discovers `AGENTS.md` (or `AGENTS.override.md`) from your `target_dir` upward and threads it into the Generator. If neither file exists, forge-mcp emits a warning at run start. Symlink an existing `CLAUDE.md` to `AGENTS.md` for consistency.

**7. Inspect results:**

```sh
ls <target_dir>/.harness/<run-id>/
cat <target_dir>/.harness/<run-id>/iteration-1/eval.md
```

## Configuration

Five environment variables control behavior:

| Variable                  | Owner      | Default     | Purpose                                                                             |
| ------------------------- | ---------- | ----------- | ----------------------------------------------------------------------------------- |
| `FORGE_CODEX_BIN`         | forge-mcp  | `codex`     | Override codex binary path                                                          |
| `FORGE_CLAUDE_CLI_PATH`   | forge-mcp  | _(none)_    | Explicit path to `claude` CLI; threaded into the SDK at runtime, not just preflight |
| `CLAUDE_CONFIG_DIR`       | Claude SDK | `~/.claude` | Selects active Claude profile                                                       |
| `ANTHROPIC_API_KEY`       | Anthropic  | _(none)_    | Direct API key (highest auth priority)                                              |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude SDK | _(none)_    | Pre-generated OAuth token                                                           |

Pass variables via `claude mcp add -e KEY=value` or set in the parent shell.

## Security

The `run.log` file may contain sensitive prompt/output snippets and is written with mode `0600`. It never crosses the tool boundary — `RunResult.artifacts` deliberately omits its path.

## Development

```sh
uv sync --all-extras           # install dev dependencies (ruff, pyright, pytest)
uv run ruff check
uv run pyright
uv run pytest -m "not slow"
bash scripts/ci.sh             # full gate: ruff + format check + pyright + docstring check + pytest
```

End-to-end tests marked `@pytest.mark.slow` exercise the real `claude` and `codex` CLIs and are excluded by default.

## Troubleshooting

- **Claude CLI missing**: `npm install -g @anthropic-ai/claude-code`, or set `FORGE_CLAUDE_CLI_PATH` to an explicit path.
- **Codex missing**: `npm install -g @openai/codex`.
- **SDK import fails**: re-run `uv sync`.
- **Auth fails**: set `ANTHROPIC_API_KEY`, set `CLAUDE_CODE_OAUTH_TOKEN`, or run `claude login`.
- **MCP registration error**: place the server name before `-e` flags: `claude mcp add forge-mcp -e KEY=value -- ...`.
- **Lock busy**: another run holds `<target_dir>/.harness/run.lock`. Stale recovery kicks in when the holding PID is dead or the lockfile mtime is older than 36 hours.

## License

MIT — see `LICENSE`.

## Security model (§H7)

The Generator executes arbitrary commands in `target_dir` for the duration of a
run. `network_access` is a convenience knob (defaulting to `true` so dependency
installation and `verify_command` work), **not a security boundary**. forge-mcp
deliberately does not ship an in-process command denylist (it would be security
theater for an agent that can author and execute scripts). The real isolation
boundary is the deployer's OS/container sandbox: run forge-mcp against a
disposable/scratch checkout inside an OS/container sandbox you control.
