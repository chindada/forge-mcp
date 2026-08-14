# 0004 — Claude MCP Isolation

## Problem

Before this change, every Claude session created by forge-mcp loaded the user,
project, and local setting sources without constraining MCP configuration. If
those settings enabled forge-mcp, the child Claude started another `forge
serve`, whose preflight skill probe started another Claude, causing unbounded
process recursion.

## Scope and invariant

All Claude sessions created by forge-mcp are MCP-isolated. No MCP server from
user, project, local, `.mcp.json`, or plugin configuration may start inside a
forge-created Claude session, and forge-mcp does not pass any explicit MCP server
to those sessions.

The existing skill and settings behavior remains intact: sessions continue to use
`skills="all"` by default and load the `user`, `project`, and `local` setting
sources. The Claude skill probe continues to read `SystemMessage.data["skills"]`
and match both bare names and namespaced names such as
`superpowers:writing-plans`.

The supported boundary is `claude-agent-sdk >=0.1.74,<1` and Claude Code CLI
`>=2.1.153`. Preflight must fail closed for older or unrecognized versions and
must not launch the live skill probe after either compatibility check fails.

## Verified SDK mechanism

`claude-agent-sdk` 0.1.74 introduced `strict_mcp_config`; the installed 0.2.137
SDK exposes it alongside the existing explicit-server option:

- `mcp_servers` contains MCP servers explicitly supplied by the SDK caller.
- `strict_mcp_config=True` maps to the Claude CLI's `--strict-mcp-config` flag
  and excludes all other MCP configuration, including user/global settings,
  project `.mcp.json`, and plugin-provided servers.

The SDK separately maps `skills` and `setting_sources` to skill enablement and
filesystem settings discovery. Therefore MCP isolation does not require removing
the settings sources needed by skill discovery.

Claude Code 2.1.153 fixed custom-agent frontmatter MCP servers bypassing
`--strict-mcp-config`. Because forge-mcp allows an external CLI through
`FORGE_CLAUDE_BIN` and `PATH`, the CLI version is part of the isolation contract.

## Design

`forge_mcp.drivers._claude.build_options()` remains the single options
chokepoint. It always constructs `ClaudeAgentOptions` with both
`mcp_servers={}` and `strict_mcp_config=True`. The builder does not expose either
option as a parameter, and no current production caller mutates the returned
options.

The five production callers—the skill probe, planner, evaluator, triage, and
remediator—continue to call the unchanged builder interface. None currently
depends on an MCP server, so no call-site exception is needed.

The configured Claude path is resolved to one absolute path before use, so SDK
child working directories cannot reinterpret it. Preflight requires the SDK
option shape and runs only that binary's bounded `--version` command. An absent
or failing SDK, or a failing CLI, produces a `FAIL` row and disables the live
skill probe so preflight cannot launch a known-unsafe child before `forge serve`
rejects the failed checks.

The resulting process boundary is:

```text
forge-mcp -> ClaudeAgentOptions(
    setting_sources=["user", "project", "local"],
    skills="all",
    mcp_servers={},
    strict_mcp_config=True,
) -> Claude CLI --strict-mcp-config
```

## Error handling

No runtime fallback weakens MCP isolation. An absent SDK, an installed SDK
without the required options, or a Claude CLI older than 2.1.153 or with an
unrecognized version produces a preflight `FAIL`. The live skill probe is skipped
after either failure, and `forge serve` refuses to start.

## Regression and verification

An options-only regression test calls `build_options()` and asserts all four
load-bearing values: empty explicit MCP configuration, strict MCP isolation,
skills enabled, and the settings sources retained. It never launches Claude.
Before this change, the test failed because
`strict_mcp_config` remained at its SDK default of `False`.

The existing skill tests continue to verify that advertised bare and namespaced
skills are reported correctly. Verification consists of the focused seam and
skill tests followed by the repository's complete `make ci` gate. The slow
real-CLI test remains excluded by default and requires both Claude compatibility
checks to pass when deliberately selected.

## Non-goals

- Adding selectively approved MCP servers to forge-created Claude sessions.
- Replacing the live skill probe or changing its matching rules.
- Changing permission mode, tool presets, or settings-source defaults.
