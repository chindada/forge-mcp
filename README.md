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

Place the server name before any `-e` environment flags to avoid parsing errors.

```sh
claude mcp add --scope user forge-mcp \
  -e CLAUDE_CONFIG_DIR=$HOME/.claude \
  -e FORGE_CODEX_BIN=$(which codex) \
  -- uv run --directory $PWD forge serve
```

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

### Task-mode callers (`call_tool_as_task`)

When invoking `run_forge` via `session.experimental.call_tool_as_task(...)`
(MCP task augmentation, §C1.4), set the `ttl` parameter to at least
`max_runtime_minutes * 60 * 1000` milliseconds. The server does not enforce a
floor — TTL semantics are governed by the caller's clock — so a too-short TTL
will cause the server to age the task out before the run terminates. See
§C11 risk 7 in the long-run continuity design brief.

```python
ttl_ms = max_runtime_minutes * 60 * 1000
await session.experimental.call_tool_as_task(
    "run_forge",
    {"target_dir": "/repo", "design_doc_content": "...", "max_runtime_minutes": 60},
    ttl=ttl_ms,
)
```

## Reading run artifacts over MCP

`forge-mcp` exposes a standard MCP resources surface so a host can read on-disk
artifacts (`plan.md`, `eval.md`, `state.json`, `sessions.json`, `status.log`, …)
over the MCP transport instead of filesystem-poking. Two RPC methods are
advertised on the same stdio server that owns `run_forge`:

- `list_resources` — enumerate active and optionally past-run artifacts, with
  cursor pagination via `nextCursor` and a page size of 50.
- `read_resource(uri: AnyUrl)` — read one artifact by its `forge://` URI.

URI shape: `forge://<harness_token>/<run_id>/<artifact_subpath>`, where
`harness_token` is a 12-character `base64url(sha256(abs_harness_dir))[:12]` and
`run_id` is the existing 8-character hex id.

Set `FORGE_HARNESS_ROOTS=/abs/path1,/abs/path2` to make completed runs under
known harness dirs appear in `list_resources` across server restarts. Past runs
remain readable by stable URI; this setting only affects discovery.

`run.log` and `run.lock` are never served. Path traversal, symlink escape, and
non-allowlisted subpaths all yield `McpError(-32002)`. Capability flags are
advertised honestly: `resources.subscribe=false` and
`resources.listChanged=false`.

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

## Cross-run learning (§L)

Each `run_forge` invocation auto-detects prior terminal runs on the same
design document (matched by SHA-256 fingerprint over the canonicalized
design text) and feeds the planner a structured digest of what didn't
work. The digest is rendered to `inputs/prior_attempts.md` and read by
the planner through the same `add_dirs=[inputs/]` surface as `design.md`.

**Anti-anchoring is load-bearing.** The planner prompt directs the model
to propose a _different_ implementation strategy when prior gaps recur,
not to refine a multiply-failed approach.

**Controls:**

- `ignore_prior_attempts: bool = False` on `RunForgeInput` — per-call
  opt-out.
- `FORGE_LINEAGE_TOP_K` env var (default 4, validated `[0, 10]`,
  `0` = kill switch) — per-deployment opt-out.

**Threat model.** Lineage is bounded to a single `target_dir` — two
tenants would need to share both `target_dir` AND write byte-identical
design docs for any cross-tenant bleed. Operators with sensitive
workloads should set `FORGE_LINEAGE_TOP_K=0`. See
`docs/specs/forge-mcp-cross-run-learning.md` §L16 for the full risk
catalog.

### Resource subscriptions

forge-mcp advertises MCP resource `subscribe: true` and `listChanged: true`.
Hosts may subscribe to allowlisted `forge://<token>/<run-id>/<artifact>` URIs and
will receive `notifications/resources/updated` after durable artifact writes.
Subscriptions are in-memory and session-scoped: after reconnecting, hosts should
re-list resources, re-read the artifacts they care about, and re-subscribe.

`inputs/design.md` and `inputs/design.fingerprint` are readable and subscribable
for allowlist parity, but they are written before a host can subscribe and do not
produce later `resources/updated` notifications. Run list changes are announced
with `listChanged` when active runs become discoverable or are pruned.
