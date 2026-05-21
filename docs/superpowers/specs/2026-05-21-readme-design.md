# README rewrite — design spec

**Date:** 2026-05-21
**Topic:** Replace the 9-line `README.md` with a full operational README modeled on [chindada/harness-mcp](https://github.com/chindada/harness-mcp/blob/main/README.md)'s README, substituted with forge-mcp's specifics (tool name, env-var prefix, CLI commands, no Playwright).
**Status:** Brainstorm approved by user (framing = "Mirror harness-mcp tone", diff handling = "Pure mirror", all four JCs kept). Ready for implementation via writing-plans.

## Goal

Produce a `README.md` that:

- Reads as a polished operational guide (install → verify → register → run → inspect).
- Mirrors the harness-mcp README's section structure and tone 1:1.
- Contains zero references to harness-mcp, `run_harness`, `HARNESS_*` env vars, `harness-mcp serve|doctor`, or Playwright. forge-mcp's reality is stated as the truth; readers don't need to know harness-mcp exists.
- Preserves the four small expansions ("JCs") that go one step beyond a pure structural mirror because they reflect genuine forge-mcp behavior worth surfacing.

## Decisions baked in (from brainstorming)

1. **Framing: Mirror harness-mcp tone.** Polished operational README treating the project as installable end-to-end. The install path (`uv sync` → `forge doctor` → `claude mcp add` → run example) is functional today even if the orchestrator loop is mid-implementation.
2. **Diff handling: Pure mirror.** No "What changed from harness-mcp" subsection, no inline diff callouts, no design-doc pointer. (Consequence: the current README's `See docs/specs/forge-mcp-design.md` line is dropped — harness-mcp's README has no equivalent.)
3. **All four JCs kept** (see "Judgment calls" below).

## Constraints

- **Section structure** must match harness-mcp's README order: intro → Installation Steps → Configuration → Security → Development → Troubleshooting → License.
- **Mechanical substitutions everywhere**: `harness-mcp` → `forge-mcp`, `run_harness` → `run_forge`, `HARNESS_*` → `FORGE_*`, `harness-mcp serve|doctor` → `forge serve|doctor`, `chindada/harness-mcp` → `chindada/forge-mcp`.
- **Artifact root stays `.harness/`** (not `.forge/`) — clean-room choice per CLAUDE.md §13 and design §13. Not a leftover.
- **No Playwright references anywhere** — the doctor check list, the troubleshooting bullets, and any side notes drop it. The harness-mcp README's "Playwright installation" check goes away; forge-mcp doctor runs **seven** checks.
- **No `--all-extras` in the basic install step** — `uv sync` is enough for runtime. `--all-extras` appears only under Development. (Diverges from harness-mcp's install step by one word; intentional, matches CLAUDE.md.)
- **No `AGENTS.override.md` 32 KiB cap claim** — that mechanic is harness-mcp-side. forge-mcp lets Codex auto-discover natively and only warns if neither file exists.

## Final README content (verbatim, ready to write)

The body below is the complete file contents. Outer fence is four backticks; inner code fences are three backticks.

````markdown
# forge-mcp

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

| Variable | Owner | Default | Purpose |
|----------|-------|---------|---------|
| `FORGE_CODEX_BIN` | forge-mcp | `codex` | Override codex binary path |
| `FORGE_CLAUDE_CLI_PATH` | forge-mcp | _(none)_ | Explicit path to `claude` CLI; threaded into the SDK at runtime, not just preflight |
| `CLAUDE_CONFIG_DIR` | Claude SDK | `~/.claude` | Selects active Claude profile |
| `ANTHROPIC_API_KEY` | Anthropic | _(none)_ | Direct API key (highest auth priority) |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude SDK | _(none)_ | Pre-generated OAuth token |

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
````

## Judgment calls (all four kept per user)

**JC-1 — `FORGE_CLAUDE_CLI_PATH` runtime-threading qualifier (Configuration table).**
Description column reads: *"Explicit path to `claude` CLI; threaded into the SDK at runtime, not just preflight."*
*Why kept:* genuine forge-mcp behavior per design §6.5 (`build_options` forwards `cli_path` to `ClaudeAgentOptions` via `RunContext.claude_cli_path`). Tells users the override actually takes effect during runs, not just during doctor.

**JC-2 — `RunResult.artifacts` path-omission qualifier (Security).**
Section reads: *"It never crosses the tool boundary — `RunResult.artifacts` deliberately omits its path."*
*Why kept:* useful for a reader thinking about data-leak surface. Backed by `ArtifactIndex` in design §7 (`run_log_path` is deliberately absent).

**JC-3 — `bash scripts/ci.sh` line under Development.**
Adds: *"bash scripts/ci.sh # full gate: ruff + format check + pyright + docstring check + pytest"*
*Why kept:* forge-mcp ships this and CLAUDE.md treats it as the canonical gate. Useful for contributors who want one command instead of four.

**JC-4 — "Lock busy" troubleshooting bullet.**
Adds: *"Lock busy: another run holds `<target_dir>/.harness/run.lock`. Stale recovery kicks in when the holding PID is dead or the lockfile mtime is older than 36 hours."*
*Why kept:* stale-lock confusion is a real user pitfall. The 36h threshold is intentional design (CLAUDE.md and design §6.5 — wider than legacy's 24h to remove a 24h-cap boundary risk).

## Implementation notes for the executing agent

- The current `README.md` is 9 lines. **Overwrite it entirely** with the body inside the outer ```` ```markdown ```` fence above. No partial edits. No preserving the existing `See docs/specs/...` pointer (excluded by Pure-mirror decision).
- No code or non-README file changes anywhere.
- Verification after write — read back the file and confirm:
  - Tool name is `run_forge` (no `run_harness`).
  - All env vars use `FORGE_*` prefix (no `HARNESS_*`).
  - CLI commands are `forge serve` / `forge doctor` (no `harness-mcp serve|doctor`).
  - Clone URL is `https://github.com/chindada/forge-mcp.git`.
  - Doctor section says "seven checks".
  - Zero matches for "Playwright" / "playwright".
  - Basic install step uses `uv sync` (no `--all-extras`).
  - All four JC-N qualifiers are present (the four bullets above each name a verbatim phrase to grep for).

## Out of scope

- No changes to `CLAUDE.md`, the design doc, or any code file.
- No badges, screenshots, or diagrams in the README.
- No `CONTRIBUTING.md` / `CHANGELOG.md` / `SECURITY.md` sidecar files.
- No "What's different from harness-mcp" subsection (excluded by Pure-mirror).
- No design-doc pointer (excluded for same reason).
- No PR / push to GitHub — the README change lands as a local commit pending user approval.
