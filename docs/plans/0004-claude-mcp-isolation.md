# Claude MCP Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent every Claude session spawned by forge-mcp from inheriting or starting any configured MCP server while preserving settings-backed skill discovery.

**Architecture:** Keep `build_options()` as the sole Claude options chokepoint and make MCP isolation an unconditional invariant there. Exercise the returned SDK options directly so the regression never launches Claude, while retaining the existing fake-runner coverage for bare and namespaced skill discovery. Fail preflight before a live skill probe when either the installed SDK or configured Claude CLI cannot honor that invariant.

**Tech Stack:** Python 3.11+, `claude-agent-sdk >=0.1.74,<1`, Claude Code CLI `>=2.1.153`, pytest, Ruff, Pyright.

## Global Constraints

- Never launch a real Claude session during implementation or verification.
- Keep `skills="all"` and `setting_sources=("user", "project", "local")` behavior unchanged.
- Pass no explicit MCP servers and enable the SDK's strict MCP configuration mode on every forge-created Claude options object.
- Never run the live skill probe after a Claude CLI or SDK compatibility failure.
- Resolve the configured Claude binary to one absolute path and never substitute a different
  PATH or target-relative binary.
- Skip opt-in real-CLI tests unless both Claude compatibility checks are OK.
- Regenerate `uv.lock` only for the dependency-floor change; exclude that generated output from review.
- Do not create commits.

---

## File structure

- `src/forge_mcp/drivers/_claude.py` owns the unconditional Claude MCP-isolation invariant.
- `src/forge_mcp/check.py` fails closed on incompatible Claude SDK/CLI versions before any live probe.
- `src/forge_mcp/config.py` gives preflight and SDK callers the same absolute Claude path.
- `tests/test_claude_seam.py` verifies the constructed SDK options without starting a subprocess.
- `tests/test_check.py` verifies the compatibility gates and live-probe suppression with fakes.
- `tests/test_skills.py` verifies exact bare and namespaced init-skill matching results.
- `tests/test_e2e_real_clis.py` gates its opt-in live run on the same compatibility checks.
- `pyproject.toml` and operator documentation declare the supported compatibility floor.

### Task 1: Isolate every forge-created Claude session

**Files:**

- Modify: `src/forge_mcp/drivers/_claude.py:132`
- Test: `tests/test_claude_seam.py`

**Interfaces:**

- Consumes: `build_options(*, skills="all", setting_sources=("user", "project", "local"), system: str, ...) -> ClaudeAgentOptions`.
- Produces: the same public function signature, with returned options always carrying `mcp_servers == {}` and `strict_mcp_config is True`.

- [ ] **Step 1: Write the failing options-only regression test**

```python
def test_build_options_disables_mcp_inheritance_without_hiding_skills():
    """MCP isolation must remain independent from settings-backed skill discovery.

    Design: every forge-created Claude session must reject inherited MCP servers
        while retaining the user, project, and local sources that advertise skills.
    Implementation: construct options only; assert the empty strict MCP boundary
        together with the unchanged skills and setting source values.
    Example: a forge skill-probe session can see `superpowers:writing-plans` but
        cannot load a `forge` MCP declaration from any settings source.
    """
    options = _claude.build_options(system="list skills only")

    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True
    assert options.skills == "all"
    assert options.setting_sources == ["user", "project", "local"]
```

- [ ] **Step 2: Run the regression test against the current implementation**

Run:

```bash
.venv/bin/pytest tests/test_claude_seam.py::test_build_options_disables_mcp_inheritance_without_hiding_skills -q
```

Expected: FAIL at `assert options.strict_mcp_config is True` because the SDK default is `False`. No subprocess is launched.

- [ ] **Step 3: Implement the minimal chokepoint fix**

Add the two load-bearing SDK options to the existing `kwargs` mapping:

```python
kwargs: dict = {
    "mcp_servers": {},
    "strict_mcp_config": True,
    "permission_mode": "bypassPermissions",
    "tools": {"type": "preset", "preset": "claude_code"},
    "system_prompt": system,
    "skills": skills,
    "setting_sources": list(setting_sources),
    "disallowed_tools": list(disallowed_tools),
}
```

Update `build_options()`'s existing Design/Implementation/Example docstring to state that the chokepoint supplies no MCP servers, enables strict isolation, and retains settings-backed skills.

- [ ] **Step 4: Verify the focused behavior and skill matching**

Run:

```bash
.venv/bin/pytest tests/test_claude_seam.py tests/test_skills.py -q
```

Expected: PASS, including the existing `superpowers:writing-plans` regression. No subprocess is launched because all skill tests use `FakeClaudeRunner`.

- [ ] **Step 5: Run the complete repository gate and inspect the diff**

Run:

```bash
make ci
git diff HEAD --check
git diff HEAD -- src/forge_mcp/drivers/_claude.py tests/test_claude_seam.py
git status --short
```

Expected: Ruff, formatting, Pyright, docstring checks, and the non-slow pytest suite all exit 0; the implementation diff contains only the options invariant, its docstring, and the regression test. Leave all changes uncommitted.

### Task 2: Enforce the SDK and CLI compatibility boundary

**Files:**

- Modify: `src/forge_mcp/check.py`
- Modify: `src/forge_mcp/config.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: compatibility references in `docs/specs/0001-forge-mcp-harness.md`, `docs/specs/0002-developer-tooling.md`, and `docs/plans/0001-forge-mcp-harness.md`
- Test: `tests/test_check.py`
- Test: `tests/test_config.py`
- Test: `tests/test_e2e_real_clis.py`
- Test: `tests/test_sdk_contract.py`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Add failing compatibility and probe-suppression tests**

Construct synthetic `claude --version` results for versions below, at, and in an
unrecognized form, and verify that an invalid configured path cannot be masked
by a PATH binary or reinterpreted relative to a target directory. Replace the
live skill probe with a fake and assert `run_checks()` passes `probe_live=False`
after a Claude CLI or SDK contract failure and `True` after both pass. Replace
`ClaudeAgentOptions` with an older synthetic shape and assert both an
incompatible and an absent SDK fail.

- [ ] **Step 2: Run the focused tests and confirm the expected failures**

Run:

```bash
.venv/bin/pytest tests/test_check.py tests/test_config.py tests/test_sdk_contract.py tests/test_skills.py -q
```

Expected: the new old-CLI, SDK-shape, and live-probe suppression assertions fail
against the pre-change checks. No real Claude session is launched.

- [ ] **Step 3: Implement fail-closed preflight checks**

Require Claude Code CLI 2.1.153 or newer using a bounded `--version` command.
Make `claude_bin()` return an absolute path so target working directories cannot
reinterpret the binary after preflight validates it.
Require the installed `ClaudeAgentOptions` shape to include `mcp_servers`,
`strict_mcp_config`, `setting_sources`, and `skills`. Treat an absent SDK as FAIL
because every forge run has Claude stages. Suppress the live skill probe whenever
the CLI or SDK contract result is `FAIL`, and gate the opt-in real-CLI test on
both compatibility checks.

- [ ] **Step 4: Raise the dependency floor and update documentation**

Set `claude-agent-sdk >=0.1.74,<1`, refresh `uv.lock`, and document the SDK/CLI
minimums and fail-closed preflight behavior. Keep historical pinned-version
records intact unless they state the active supported range.

- [ ] **Step 5: Verify focused behavior, then the complete gate**

Run:

```bash
.venv/bin/pytest tests/test_check.py tests/test_config.py tests/test_sdk_contract.py tests/test_skills.py -q
uv lock --check
make ci
git diff HEAD --check
git status --short
```

Expected: all commands exit 0; no test launches a real Claude session, and all
changes remain uncommitted.
