# 0002 — Developer Tooling (Makefile + README)

**What:** Add a `Makefile` of static-check / dependency targets and rewrite `README.md`
into an accurate, comprehensive operator/contributor guide for the current
single-plan, direct-edit harness (`refactor` branch, after `0003`).

**Status:** Design approved. This document is the normative brief for producing the
`Makefile` and the `README.md`. Every fact the README asserts is pinned to source in
**§3** and was adversarially verified against the installed `refactor` tree (file:line
citations). The `develop` branch's README/Makefile are a **reference only**; this work
deliberately diverges from them because the harness was rewritten to a **single-plan,
direct-edit** model (`0003-single-plan-direct-edit-harness.md`, commit `9460460`): the
multi-plan wave loop, the `CONCURRENCY_CAP`, the copy-sandbox/merge subsystem, the 11-state
machine, and the `doctor`/`version`/`verify_command`/`resume` surface are all gone. The
README must not be produced by copy-paste.

**Audience:** The implementing agent and reviewers. Precision over prose.

**Correctness contract:** The README is read back and diffed against the codebase. Two
disciplines apply throughout: (1) every README claim traces to a §3 fact with a file:line
citation; (2) anything absent from the source (e.g. `verify_command`, `resume`,
`ANTHROPIC_API_KEY`, `CONCURRENCY_CAP`) is absent from the README. Internal labels (state
strings, field names, probe labels, env var names) are quoted verbatim from the source.

---

## §1. Purpose & scope

Two artifacts, plus this design record, and nothing else:

1. **`Makefile`** — a lean set of static-check and dependency targets:
   `help`, `setup-tools`, `setup`, `update`, `fmt`, `lint`, `test`, `build`, `ci`.
   No `run`/`serve` targets (those are CLI subcommands, not build steps).
2. **`README.md`** — a freshly written, comprehensive reference accurate to the
   single-plan harness.

This document (`docs/specs/0002-developer-tooling.md`) is the design record for the above.
**`scripts/ci.sh` is deliberately left untouched** (§4.4): the design adds a `make ci`
target but does not rewrite the existing merge-gate script, keeping the change surgical to
the two new artifacts.

There is no deferred or out-of-scope work: everything described here is implemented in this
change set.

## §2. Constraints & decisions (from brainstorming)

- **`setup-tools` bootstraps `uv` only.** The `codex` binary and the `claude` CLI are
  documented as manual prerequisites in the README; the Makefile never touches global npm.
- **`update` refreshes Python dependencies only** (`uv lock --upgrade` + `uv sync
  --all-extras`). Host tools are re-bootstrapped by re-running `setup-tools`.
- **`scripts/ci.sh` is not modified.** A `make ci` target is added (`= lint test`), but the
  existing five-step `ci.sh` stays as-is. To prevent behavioral drift between the two, the
  Makefile's `lint`/`test` commands are kept **byte-for-byte identical** to `ci.sh`'s steps
  (§4.4). (The alternative — rewriting `ci.sh` to delegate to `make ci` — was considered and
  declined to keep the change scoped to the two new artifacts.)
- **README is a comprehensive reference**, not a quickstart.
- **Every Makefile target carries a three-section `Design / Implementation / Example`
  comment block**, mirroring the Rule-21 docstring discipline that
  `scripts/check_docstrings.py` enforces on Python (§3.8). The `help` target's one-line
  text comes from a trailing `## …` comment on each target line.
- **Best-practice over convenience.** Where the `develop` Makefile took a shortcut, this
  design takes the correct path and says why (§6).

## §3. Verified codebase facts (correctness anchor)

All facts below were confirmed against the `refactor` working tree. The README in §5 may
state only what appears here. Citations are `path:line`.

### §3.1 CLI surface (`forge`)

- Console script: `forge = "forge_mcp.cli:main"` (`pyproject.toml:24`); `python -m
  forge_mcp` routes through the same `main()` (`src/forge_mcp/__main__.py:5,8`).
- Exactly **two** subcommands exist (`@app.command()` at `src/forge_mcp/cli.py:18,35`):
  - **`serve`** — no options; delegates to `server.serve()` (`cli.py:18-32`).
  - **`check`** — options:
    - `--target-dir TEXT` — default `None`; help `"Target directory to probe."`
      (`cli.py:37`).
    - `--live-claude / --no-live-claude` — default `True`; help `"Probe the live Claude
      session for skill discovery (default on; --no-live-claude skips it for a fast offline
      check)."` (`cli.py:38-45`).
  - `check` prints each row as `[{status}] {label}: {detail}` (`cli.py:63-64`) and exits
    `1` if any row FAILs (`cli.py:65-66`).
- **No `doctor` and no `version` subcommand exist** — only `serve` and `check`
  (`cli.py:18,35`).

### §3.2 The `run_forge` MCP tool

- Registered with `@mcp.tool()` (`src/forge_mcp/server.py:26`) on the module-level
  `FastMCP(name="forge-mcp")` (`server.py:23`).
- Signature (`server.py:27-35`), returns `RunResult`:

  | # | Parameter | Type | Default | Notes |
  |---|-----------|------|---------|-------|
  | 1 | `target_dir` | `str` | *(required)* | Directory the Generator reads and writes |
  | 2 | `design_doc_path` | `str \| None` | `None` | Path to the design doc — XOR with `design_doc_content` |
  | 3 | `design_doc_content` | `str \| None` | `None` | Inline design text — XOR with `design_doc_path` |
  | 4 | `max_iterations` | `int` | `10` | Generator→Evaluator→Triage loops for the one plan |
  | 5 | `max_runtime_minutes` | `int` | `600` | Hard wall-clock cap for the whole run |

  (`server.py:28-32`; the same defaults are mirrored on `RunForgeInput`,
  `src/forge_mcp/models.py:22-26`.)
- **Exactly-one-design-doc rule** is a pydantic `@model_validator` on `RunForgeInput`
  (`models.py:28-48`): both set → `ValueError("Provide exactly one of design_doc_path or
  design_doc_content, not both.")` (`models.py:43-45`); neither set → `ValueError("Exactly
  one of design_doc_path or design_doc_content is required.")` (`models.py:47`).
- Runtime cap: the orchestrator coroutine is wrapped in `asyncio.wait_for(...,
  timeout=max_runtime_minutes * 60)` (`server.py:117-129`). On `TimeoutError` the tool does
  **not** raise — it returns an `incomplete` `RunResult` (`server.py:130-139`).
- **No `verify_command`, `verify_timeout_seconds`, `resume`, or `ignore_prior_attempts`
  parameter exists** anywhere in `server.py` or `models.py` (grep: 0 matches).

### §3.3 `RunResult` schema (the tool's return value)

`class RunResult(BaseModel, extra="forbid")` — 8 fields (`src/forge_mcp/models.py:59-69`):

| Field | Type | Default |
|-------|------|---------|
| `status` | `Literal["completed", "incomplete", "failed"]` | *(required)* |
| `run_dir` | `str` | *(required)* |
| `iterations` | `int` | *(required)* |
| `unresolved_gaps` | `list[GapSummary]` | `[]` |
| `failure_kind` | `str \| None` | `None` |
| `stop_reason` | `str \| None` | `None` |
| `verified` | `bool` | *(required)* |
| `summary` | `str` | *(required)* |

- Status meaning, from the single-plan finalizer (`engine.py:215-275`): **`completed`** — the
  one plan reached `done` (`engine.py:241,244-248`); **`incomplete`** — the plan did not
  complete, so `unresolved_gaps` is populated and a `stop_reason` is synthesized from the
  plan's own reason (`engine.py:249-264`); **`failed`** — an orchestrator-internal error
  stopped the run, with `failure_kind` set to the exception type name
  (`engine.py:176-189`, `failure_kind=type(exc).__name__` at `engine.py:188`;
  `build_run_result` only surfaces `failure_kind` when `status == "failed"`,
  `lifecycle.py:121`).
- **`verified`** is `True` **only when** the plan is `done` **and** a `verification_command`
  was declared (`verified = done and (plan.verification_command is not None)`,
  `engine.py:242`) — an honest `False` otherwise.
- `GapSummary(BaseModel, extra="forbid")` fields: `title: str`, `severity: str`,
  `design_doc_section: str` (`models.py:51-56`).
- **Develop-era fields `run_id`, `runtime_seconds`, `artifacts`, `failed_phase`,
  `error_class`, `iterations_used` do not exist** on `refactor` (the schema is exactly the 8
  fields above, `models.py:59-69`).

### §3.4 Run lifecycle (states)

- `RunState = Literal[...]` — **7 states in order**
  (`src/forge_mcp/orchestrator/statemachine.py:11-19`):
  `init, planning, executing, finalizing, completed, incomplete, failed`.
- Terminal states: `frozenset({"completed", "incomplete", "failed"})`
  (`statemachine.py:21`).
- Legal edges (`statemachine.py:26-31`): `init→{planning}`, `planning→{executing}`,
  `executing→{finalizing}`, `finalizing→{completed, incomplete, failed}`.
- `failed` is reachable from **any** non-terminal state (the adjacency check exempts it,
  `statemachine.py:116`); a transition out of a terminal state raises `ValueError`
  (`statemachine.py:111-112`).
- The run **plans once** then runs **one plan loop** directly on `target_dir`: the engine
  transitions `planning → run_planner (→ one Plan)` (`engine.py:139-147`), `executing →
  run_plan_loop` (`engine.py:150-164`), then `finalizing → _finalize` (`engine.py:169-170`).
  There is **no** `scheduling`/`merging`/`amending`/`verifying` run-level state, **no** wave
  loop, and **no** `CONCURRENCY_CAP` (`tests/test_config.py:48` actively asserts
  `CONCURRENCY_CAP` no longer exists on the config module; the `scheduler.py`/`sandbox.py`
  modules are deleted).
- **The iteration loop** `run_plan_loop` (`src/forge_mcp/orchestrator/phases.py:173-398`)
  runs, per iteration: generate (Codex edits `target_dir`) → optional verify → evaluate →
  optional triage → synthesize the verify blocker → fingerprint → optional in-loop
  amendment → completion / non-progress / cap test (`phases.py:215-379`; lines 381-388 are
  the post-loop unreachable fallback, not a per-iteration step). Its
  `PlanLoopResult.terminal_state` is `Literal["done", "incomplete", "failed"]`
  (`phases.py:50`).
- **Non-progress policy** (`src/forge_mcp/convergence.py`): `Signal = Literal["none",
  "NUDGE", "EARLY_STOP"]` (`convergence.py:8`); `detect_non_progress(history, window=2)`
  (`convergence.py:22`) returns `EARLY_STOP` when the last `2*window` fingerprints all equal
  the latest (`convergence.py:37`), `NUDGE` when the last `window` do (`convergence.py:39`),
  else `none`. In the loop, `EARLY_STOP` → `incomplete` (gap-set stable,
  `phases.py:354-362`; amendment thrash, `phases.py:310-318`), `NUDGE` sets the next
  iteration's nudge note (`phases.py:363-364`), and the iteration cap → `incomplete`
  (`phases.py:319-327,367-375`).
- **Each phase is a fresh SDK session.** `ClaudeDriver.run()` drives a fresh `ClaudeSDKClient`
  per call via `_single_run` (`src/forge_mcp/drivers/_claude.py:254-256`); the Codex generator
  builds a fresh `AsyncCodex` (`src/forge_mcp/drivers/_codex.py:378`) and `thread_start`
  (`:384`) per call. No conversation or SDK-session state is carried across phases — the
  driver objects are reused (constructed once, `src/forge_mcp/server.py:113-114`) but each
  call opens a fresh session — so phases hand off **only** through the on-disk artifacts of
  §3.5.

### §3.5 Run artifacts (`<target_dir>/.harness/<run-id>/`)

- Run dir: `<target_dir>/.harness/<run_id>` (`config.py:109,114`), created at mode `0o700`
  (`config.py:116`). `run_id` is `time.strftime("%Y%m%d%H%M%S", when)` (14 digits) with an
  optional `-NN` 2-digit collision suffix (`ids.py:33-34`; shape regex
  `^\d{14}(-\d{2})?$`, `ids.py:8`).
- `.harness/.gitignore` is created once (O_CREAT|O_EXCL) containing exactly `*`
  (`config.py:87-93`), so all run artifacts are self-ignored.
- Run-root files (`src/forge_mcp/artifacts.py`): `state.json` (run-level checkpoint, `:47`),
  `run.log` (`:57`), `spec.md` (`:103`), `spec_amendments.md` (`:113`), `spec.fingerprint`
  (`:123`), `plan.json` (the single structured Plan, `:135`), `plan.md` (the plan body,
  `:146`), `plan_state.json` (the single plan-level state, `:157`).
- `inputs/` subdir: `inputs/design.md` (frozen design, `:80`), `inputs/design.fingerprint`
  (`:90`).
- `iteration-<n>/` subdirs at the run root (`:169`): `contract.md` (`:178`), `summary.md`
  (`:187`), `eval.json` (`:196`), `triage.json` (`:205`), `gap_fingerprint.json` (`:214`),
  `verify.txt` (`:223`). Directories are created on demand by `ensure_iteration_dir`
  (`:261`) at mode `0o700`; the run layout is seeded by `init_run_layout` (`:226`).
- The layout is **flat**: with one plan there is **no `plans/<plan_id>/` nesting** and
  **no** `planset.json`, `plan-<id>.md`, `manifest.json`, `merge.json`,
  `conflict_fingerprint.json`, or `git-violation.txt` — none of those path methods exist on
  `RunLayout` (`artifacts.py:10-223`).

### §3.6 Configuration — environment variables

The package reads **exactly four** environment variables (full-tree grep confirmed):

| Variable | Read at | Default | Purpose |
|----------|---------|---------|---------|
| `FORGE_CLAUDE_BIN` | `config.py:52` | `which("claude")` → `~/.local/bin/claude` (`config.py:51-55`) | Override the `claude` binary path |
| `FORGE_CODEX_BIN` | `config.py:68` | `which("codex")` → `~/.npm-global/bin/codex` (`config.py:67-71`) | Override the `codex` binary path |
| `CLAUDE_CONFIG_DIR` | `config.py:38` | `~/.claude` | Select the Claude config/profile root |
| `CODEX_HOME` | `check.py:245` | `~/.codex` (`check.py:246`) | Codex home used to locate skills during `forge check` |

- **`ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` are not referenced anywhere in the
  package** (0 matches). The README must therefore attribute Claude/Codex authentication to
  those tools' own login mechanisms, not to forge-mcp configuration.

### §3.7 `forge check` probes (preflight)

`run_checks(target_dir, *, probe_claude_live=True)` (`check.py:310`) emits a flat list of
`Check` rows with `status: Literal["OK", "WARN", "FAIL"]`, in this fixed order
(`check.py:343-352`); each probe is isolated by `_safe` so a crash becomes one FAIL row
(`check.py:327-341`):

1. `target writable` — `target_dir` exists, is a dir, is writable (WARN if no `target_dir`)
   (`check.py:343`, `_check_target_writable` `:50-71`).
2. `git available` — `git` on PATH (`check.py:344`, `:74-86`).
3. `claude CLI` — `claude` binary present via `claude_bin()`/PATH (`check.py:345`,
   `:89-108`).
4. `codex binary` — `codex` binary present via `codex_bin()`/PATH (`check.py:346`,
   `:111-129`).
5. `openai_codex importable` — the `openai_codex` package imports (`check.py:347`,
   `:132-148`).
6. `codex --version smoke` — live `codex --version` (WARN if not on PATH, `:163-164`;
   timeout `5.0s` = `_CODEX_VERSION_TIMEOUT`, `check.py:19`) (`check.py:348`, `:151-180`).
7. `SDK contract` — Claude SDK seam symbols import (WARN if SDK absent → Codex-only mode,
   `:205-206`) (`check.py:349`, `:183-206`).
8. `disk space` — free space at `target_dir`/home (WARN below `500 MiB` =
   `_DISK_WARN_BYTES`, `check.py:16`) (`check.py:350`, `:209-227`).
9. `codex-skill:<id>` — filesystem skills under `$CODEX_HOME/skills` (+ plugin cache);
   required ids `("executing-plans", "frontend-design")` (`skills.py:18`) (`check.py:351`,
   `_check_codex_skills` `:230-248`).
10. `claude-skill:<id>` — live Claude session skill discovery (skipped/WARN with
    `--no-live-claude`, `check.py:276-277`); required ids `("writing-plans", "code-review")`
    (`check.py:292`, `skills.py:21`); deadline `20.0s` (`check.py:293`) (`check.py:352`,
    `_check_claude_skills` `:251-307`).

`any_fail` is `any(c.status == "FAIL" …)` — **WARN never fails** (`check.py:38-47`). `forge
serve` runs the same `run_checks(None)` as preflight and raises `RuntimeError` if any row
FAILs before starting `mcp.run()` (`server.py:180-185`).

### §3.8 Build & tooling (pyproject / scripts)

- Build backend: `hatchling` (`pyproject.toml:1-3`); wheel packages `src/forge_mcp`
  (`pyproject.toml:27`) and force-includes `src/forge_mcp/prompts → forge_mcp/prompts`
  (`pyproject.toml:29-30`).
- `requires-python = ">=3.11"`; license MIT (`pyproject.toml:9-10`). Package exposes
  `__version__ = "0.1.0"` (`src/forge_mcp/__init__.py:5`).
- Runtime deps (`pyproject.toml:11-18`): `mcp[cli] >=1.12,<2`, `claude-agent-sdk
  >=0.1.20,<1`, `openai-codex >=0.1.0b2`, `pydantic >=2.7,<3`, `typer >=0.12`, `psutil
  >=5.9`.
- **Dev deps are an extra**, not a PEP-735 group: `[project.optional-dependencies].dev =
  ["pytest >=8", "pytest-asyncio >=0.23", "ruff >=0.5", "pyright >=1.1.350"]`
  (`pyproject.toml:20-21`). → `uv sync --all-extras` installs them.
- ruff: `line-length = 100`, `target-version = "py311"` (`pyproject.toml:33-34`);
  `[tool.ruff.lint].select = ["E","F","I","B","UP","ASYNC"]` (`pyproject.toml:36-37`).
- pyright: `include = ["src","tests","scripts"]`, `typeCheckingMode = "basic"`,
  `pythonVersion = "3.11"`, `extraPaths = ["scripts"]` (`pyproject.toml:40-43`).
- pytest: `asyncio_mode = "auto"`, markers `slow`/`mcp`/`driver`, `testpaths = ["tests"]`,
  `addopts = "-m 'not slow'"` (slow excluded by default) (`pyproject.toml:46-53`).
- `scripts/ci.sh` is exactly 5 steps under `set -euo pipefail` (`ci.sh:1-7`):
  `uv run ruff check src tests scripts` (`:3`) → `uv run ruff format --check src tests
  scripts` (`:4`) → `uv run pyright` (`:5`) → `uv run python scripts/check_docstrings.py src
  tests scripts` (`:6`) → `uv run pytest` (`:7`).
- `scripts/check_docstrings.py` enforces three labels `("Design:", "Implementation:",
  "Example:")` (`:17`), each needing ≥5 non-whitespace chars (`:53`); it also rejects a
  boilerplate template docstring (`:18,81-82`).
- `uv.lock` is committed; **no `.github/` CI pipeline exists** (`.github/` absent). The only
  repo consumer of `ci.sh` is `tests/test_scaffold.py:36-42` (`test_ci_script_executable`),
  which asserts the script *exists and is executable* (`st_mode & 0o111`) — it does not
  inspect the contents; that test is not slow-marked, so it runs in the default `make test`
  gate (`pyproject.toml:53`).

## §4. Makefile design

### §4.1 Conventions

```make
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help setup-tools setup update fmt lint test build ci
```

All targets are phony (no file products except `build`, whose output is `dist/` and which
we keep phony for simplicity). `uv` is invoked directly; the uv cache is left at its
default shared location (see §6) rather than redirected to a project-local path.

### §4.2 Targets

The check/test commands are byte-for-byte the same as `scripts/ci.sh` step-for-step
(§3.8), so `make ci` and the standalone `ci.sh` run the identical gate (§4.4).

| Target | Commands | Source of truth |
|--------|----------|-----------------|
| `help` | `awk` over the `## ` trailing comments; the default goal | self-documenting pattern |
| `setup-tools` | install `uv` only, idempotent: `command -v uv` guard, else `curl -LsSf https://astral.sh/uv/install.sh \| sh` | §2 decision |
| `setup` | `uv sync --locked --all-extras` | §3.8 (dev is an extra); §6 (`--locked`) |
| `update` | `uv lock --upgrade` then `uv sync --all-extras` | §2 decision; context7 §6 |
| `fmt` | `uv run ruff check --fix src tests scripts` then `uv run ruff format src tests scripts` | mutating counterpart of `lint` |
| `lint` | `uv run ruff check src tests scripts` · `uv run ruff format --check src tests scripts` · `uv run pyright` · `uv run python scripts/check_docstrings.py src tests scripts` | `ci.sh:3-6` |
| `test` | `uv run pytest` (slow excluded by `addopts`, §3.8) | `ci.sh:7` |
| `build` | `uv build` | hatchling wheel + sdist |
| `ci` | `lint test` (the aggregate gate; same 5 commands/order as `ci.sh`, §4.4) | `ci.sh` parity |

Notes:

- `setup` uses `--locked`: it installs exactly the pinned `uv.lock` and **fails fast if the
  lock is stale** rather than silently re-resolving — changing dependencies is `update`'s
  job. `update`'s post-upgrade `sync` omits `--locked` because it just rewrote the lock.
- `lint` is read-only and runs all four static checks; `fmt` is the mutating counterpart
  (autofix + format). They are kept separate so verification never silently rewrites files.
- No `test-slow`/`e2e`/`clean` targets: out of the requested set and not needed here. Slow
  tests remain runnable directly with `uv run pytest -m slow` (documented in the README,
  not wrapped).

### §4.3 Comment discipline (Design / Implementation / Example per target)

Each target is preceded by a three-section comment block and carries a trailing `## …` for
`help`. The `help` `awk` matches only target lines (regex `^[a-zA-Z][a-zA-Z0-9_-]*:.*## ` —
the `_-` in the class is required so `setup-tools:` matches), so the `#` comment-block lines
are ignored. Canonical form:

```make
# fmt — apply ruff autofixes and format sources in place.
#
# Design: separate the mutating "fix it" action from the read-only `lint` gate so
#   verification never silently rewrites files; mirrors §3.8's fmt/lint split.
# Implementation: `ruff check --fix` first (lint/import autofixes), then `ruff format`,
#   both over `src tests scripts` — the same roots ci.sh and lint use.
# Example: `make fmt` rewrites imports and reformats; inspect with `git diff`.
fmt: ## Apply ruff autofixes + format (mutates files)
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts
```

This applies the same three-section discipline that `scripts/check_docstrings.py` enforces
on Python defs (§3.8) to every Make target. It is a documentation convention; the docstring
checker does not parse Makefiles.

### §4.4 Relationship to `scripts/ci.sh`

`scripts/ci.sh` is **left exactly as-is** — the existing 5-step `set -euo pipefail` script
(§3.8). The Makefile adds a `ci` target (`ci: lint test`) whose expansion is the **same five
commands in the same order** as `ci.sh`. The two definitions are deliberately kept identical
so they cannot behave differently:

- `make lint` = `ci.sh:3-6` (ruff check · ruff format --check · pyright · check_docstrings),
- `make test` = `ci.sh:7` (pytest),
- `make ci` = `lint` then `test` = the same five commands as `ci.sh`.

The tradeoff is accepted explicitly: the gate is expressed in two places (the Makefile and
`ci.sh`), kept in sync by hand. This was chosen over making `ci.sh` `exec make ci` in order
to keep the change scoped to the two new artifacts and not modify a script outside the ask.
The sole consumer, `tests/test_scaffold.py:36-42`, asserts only that `ci.sh` *exists and is
executable*, so leaving it untouched keeps that test green.

## §5. README design

`README.md` is rewritten from scratch, comprehensive and accurate to the single-plan
harness. Section order and the verified content each section must carry:

1. **Title + summary** — `# forge-mcp`; one paragraph: a stdio MCP server exposing a single
   `run_forge` tool that drives a Planner (Claude) — which produces one plan — then a
   Generator (Codex) / Evaluator (Claude) iteration loop that implements a feature from a
   design document **directly into** `target_dir`,
   persisting all artifacts under `<target_dir>/.harness/<run-id>/` (§3.2, §3.5). State
   `__version__` `0.1.0` (§3.8).
2. **How it works** — the Planner produces **one** plan; the single iteration loop
   (`generate → verify → evaluate → triage → converge`, applying design-fault amendments
   in-loop) runs directly on `target_dir`; honest non-convergence stops early as
   `incomplete` (§3.4). Each phase runs in a fresh SDK session and hands off **only** through
   on-disk artifacts (§3.4, §3.5). The Generator edits the repo in place and leaves its changes **uncommitted**
   for the human's own git to review (§3.4; `0003` §7).
3. **Prerequisites** — Python ≥3.11; `uv`; `git`; the `codex` binary
   (`npm install -g @openai/codex`); the `claude` CLI. Note codex/claude are manual
   prerequisites (§2).
4. **Setup** — `make setup-tools` (installs `uv`), `make setup` (installs deps), then
   `uv run forge check` to verify the environment; list the 10 probes briefly and that exit
   `0` means OK while any `FAIL` must be fixed (§3.7).
5. **Register with Claude Code** — `claude mcp add forge-mcp -- uv run --directory "$PWD"
   forge serve`; show the optional `-e FORGE_CODEX_BIN=… -e CLAUDE_CONFIG_DIR=…` variant
   (server name before `-e` flags). `forge serve` runs preflight and refuses to start if
   any check FAILs (§3.1, §3.7).
6. **The `run_forge` tool** — the §3.2 5-row parameter table; the exactly-one-design-doc
   rule with both verbatim error messages; the §3.3 `RunResult` table with the three
   `status` meanings and the `verified` semantics; the runtime-cap behavior (timeout →
   `incomplete`, not an error).
7. **Run lifecycle** — the §3.4 7 states and terminal set; a Mermaid diagram built **only**
   from the verified legal edges (`init→planning→executing→finalizing→{completed, incomplete,
   failed}`); a note that `failed` is reachable from any non-terminal state; the
   `NUDGE`/`EARLY_STOP` non-progress policy. **No** wave loop, `CONCURRENCY_CAP`, or
   merge/sandbox language appears.
8. **Artifacts** — the §3.5 flat `.harness/<run-id>/` tree (run-root files, `inputs/`,
   `iteration-<n>/`), and that `.harness` is self-ignored via its `*` `.gitignore`. **No**
   `plans/<id>/`, `planset.json`, `manifest.json`, or `merge.json` appears.
9. **CLI reference** — `forge serve` and `forge check [--target-dir TEXT]
   [--live-claude/--no-live-claude]` with the `[STATUS] label: detail` output format and
   exit-1-on-FAIL behavior; `python -m forge_mcp` equivalence (§3.1).
10. **Configuration** — the §3.6 four-variable table. A short note: forge-mcp reads only
    these; Claude and Codex authentication is configured through those tools' own login
    mechanisms (no `ANTHROPIC_API_KEY` handling in forge-mcp).
11. **Development** — the `make help` target table; `make fmt` / `make lint` / `make test`
    / `make ci`; that `make ci` runs the same gate as `scripts/ci.sh`; slow tests via `uv
    run pytest -m slow`; the Rule-21 docstring policy enforced by `check_docstrings.py`,
    plus ruff and pyright (§3.8). `make build` for the wheel + sdist.
12. **License** — MIT (§3.8).

The README references `develop`'s structure as inspiration only; all content is rewritten
and validated against §3. No `develop`-era surface (`doctor`, `version`, `verify_command`,
`resume`, `ignore_prior_attempts`, `run_id`/`runtime_seconds`/`artifacts`, the wave loop,
`CONCURRENCY_CAP`, `plans/<id>/`) appears.

## §6. Toolchain rationale (best-practice, context7-grounded)

- **`uv sync --locked --all-extras` for `setup`.** Current uv guidance (and uv's own
  CI/Docker examples) install from the lockfile with `--locked`, which asserts
  `uv.lock` is consistent with `pyproject.toml` and installs the pinned set — reproducible
  onboarding. `--all-extras` pulls the `dev` extra (§3.8). We do **not** add `--dev`: that
  flag selects a PEP-735 dependency *group*, which this project does not define (its dev
  tools live in `[project.optional-dependencies]`, an extra).
- **`uv lock --upgrade` + `uv sync` for `update`.** uv prefers already-locked versions on
  a plain `sync`/`lock`; `--upgrade` is the documented way to move every package to the
  latest version permitted by `pyproject` constraints, then `sync` installs the result.
- **Shared uv cache (no `UV_CACHE_DIR` override).** uv's default shared cache accelerates
  installs across projects; redirecting it to a project-local directory (as `develop` did,
  `UV_CACHE_DIR ?= .harness/uv-cache`) forfeits that benefit. We keep the default — the
  correct choice, not the convenient one.
- **`fmt`/`lint` separation** keeps verification non-mutating — a CI run never rewrites the
  tree it is checking.
- **`make ci` kept identical to `ci.sh`** (§4.4): rather than expand scope by rewriting the
  merge-gate script, the Makefile reproduces its exact commands; the two are synchronized by
  the byte-for-byte discipline of §3.8.

## §7. Done criteria

1. `make help` lists every target with its one-line description.
2. `make lint` runs exactly the four checks of `ci.sh:3-6`; `make test` runs `ci.sh:7`;
   `make ci` runs all five in the same order as `ci.sh`. `scripts/ci.sh` is unchanged and
   stays present + executable so `tests/test_scaffold.py::test_ci_script_executable` keeps
   passing.
3. `make setup-tools` is idempotent (no-op when `uv` is present); `make setup` installs from
   the lockfile; `make update` re-locks then installs.
4. `make build` produces a wheel + sdist under `dist/`.
5. Every Makefile target has a `Design / Implementation / Example` comment block plus a
   `## ` help line.
6. Every factual statement in `README.md` matches a §3 fact; no `develop`-era or
   non-existent surface appears.
