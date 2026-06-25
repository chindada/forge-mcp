# 0002 — Developer Tooling (Makefile + README)

**What:** Add a `Makefile` of static-check / dependency targets and rewrite `README.md`
into an accurate, comprehensive operator/contributor guide for the current `refactor`
branch. Consolidate the existing `scripts/ci.sh` so the local checks and the merge gate
share one definition.

**Status:** Design approved. This document is the normative brief for producing the
Makefile, the README, and the `scripts/ci.sh` consolidation. Every fact the README asserts
is pinned to source in **§3** and was adversarially verified against the installed
`refactor` tree (file:line citations). The `develop` branch's README/Makefile are a
**reference only**; this work deliberately diverges from them because the `refactor`
branch changed the CLI surface, the `run_forge` parameters, the `RunResult` schema, the run
states, and the environment variables. The README must not be produced by copy-paste.

**Audience:** The implementing agent and reviewers. Precision over prose.

**Correctness contract:** The README is read back and diffed against the codebase. Two
disciplines apply throughout: (1) every README claim traces to a §3 fact with a file:line
citation; (2) anything absent from the source (e.g. `verify_command`, `resume`,
`ANTHROPIC_API_KEY`) is absent from the README. Internal labels (state strings, field
names, probe labels, env var names) are quoted verbatim from the source.

---

## §1. Purpose & scope

Three deliverables, and only these three:

1. **`Makefile`** — a lean set of static-check and dependency targets:
   `help`, `setup-tools`, `setup`, `update`, `fmt`, `lint`, `test`, `build`, `ci`.
   No `run`/`serve` targets (those are CLI subcommands, not build steps).
2. **`README.md`** — a freshly written, comprehensive reference accurate to `refactor`.
3. **`scripts/ci.sh`** — rewritten to delegate to `make ci`, so the local merge gate and
   the Makefile cannot drift.

This document (`docs/specs/0002-developer-tooling.md`) is the design record for the above.
There is no deferred or out-of-scope work: everything described here is implemented in this
change set.

## §2. Constraints & decisions (from brainstorming)

- **`setup-tools` bootstraps `uv` only.** The `codex` binary and the `claude` CLI are
  documented as manual prerequisites in the README; the Makefile never touches global npm.
- **`update` refreshes Python dependencies only** (`uv lock --upgrade` + `uv sync`). Host
  tools are re-bootstrapped by re-running `setup-tools`.
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
  forge_mcp` routes through the same `main()` (`src/forge_mcp/__main__.py:5-8`).
- Exactly **two** subcommands exist (`@app.command()` in `src/forge_mcp/cli.py`):
  - **`serve`** — no options; starts the MCP stdio server (`cli.py:18-19`).
  - **`check`** — options:
    - `--target-dir TEXT` — default `None`; help `"Target directory to probe."`
      (`cli.py:37`).
    - `--live-claude / --no-live-claude` — default `True`; help `"Probe the live Claude
      session for skill discovery (default on; --no-live-claude skips it for a fast offline
      check)."` (`cli.py:38-45`).
  - `check` prints each row as `[{status}] {label}: {detail}` and exits `1` if any row
    FAILs (`cli.py:64-66`).
- **No `doctor` and no `version` subcommand exist** (`cli.py:18-66`).

### §3.2 The `run_forge` MCP tool

- Registered with `@mcp.tool()` on the module-level `FastMCP(name="forge-mcp")`
  (`src/forge_mcp/server.py:23,26`).
- Signature (`server.py:27-35`), returns `RunResult`:

  | # | Parameter | Type | Default | Notes |
  |---|-----------|------|---------|-------|
  | 1 | `target_dir` | `str` | *(required)* | Directory the Generator reads and writes |
  | 2 | `design_doc_path` | `str \| None` | `None` | Path to the design doc — XOR with `design_doc_content` |
  | 3 | `design_doc_content` | `str \| None` | `None` | Inline design text — XOR with `design_doc_path` |
  | 4 | `max_iterations` | `int` | `10` | Planner→Generator→Evaluator loops |
  | 5 | `max_runtime_minutes` | `int` | `600` | Hard wall-clock cap for the whole run |

- **Exactly-one-design-doc rule** is a pydantic `@model_validator` on `RunForgeInput`
  (`src/forge_mcp/models.py:28-48`): both set → `ValueError("Provide exactly one of
  design_doc_path or design_doc_content, not both.")`; neither set → `ValueError("Exactly
  one of design_doc_path or design_doc_content is required.")`.
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

- Status meaning: **`completed`** — all gaps resolved and verification passed
  (`engine.py:729-733`); **`incomplete`** — a cap or a non-progress break stopped the run
  first, `unresolved_gaps` populated (`engine.py:738`); **`failed`** — a fatal error
  stopped the run, `failure_kind` set to the exception type name (`engine.py:338-351`,
  `lifecycle.py:115`).
- `GapSummary(BaseModel, extra="forbid")` fields: `title: str`, `severity: str`,
  `design_doc_section: str` (`models.py:51-56`).
- **Develop-era fields `run_id`, `runtime_seconds`, `artifacts`, `failed_phase`,
  `error_class`, `iterations_used` do not exist** on `refactor`.

### §3.4 Run lifecycle (states)

- `RunState = Literal[...]` — **11 states in order**
  (`src/forge_mcp/orchestrator/statemachine.py:11-23`):
  `init, planning, scheduling, executing, merging, amending, verifying, finalizing,
  completed, incomplete, failed`.
- Terminal states: `frozenset({"completed", "incomplete", "failed"})`
  (`statemachine.py:25`).
- Legal edges (`statemachine.py:29-36`): `init→{planning}`, `planning→{scheduling}`,
  `scheduling→{executing}`, `executing→{merging}`, `merging→{amending}`,
  `amending→{scheduling, verifying}`, `verifying→{finalizing}`,
  `finalizing→{completed, incomplete, failed}`.
- `failed` is reachable from **any** non-terminal state (the adjacency check exempts it,
  `statemachine.py:122`); a transition out of a terminal state raises `ValueError`
  (`statemachine.py:117-118`).
- The **wave loop** is `scheduling → executing → merging → amending`, repeating back to
  `scheduling` for the next wave or exiting forward to `verifying`
  (`engine.py:417,420,442,452`).
- **Concurrency:** `CONCURRENCY_CAP = 4` (`config.py:12`) bounds each wave to ≤4 plans and
  is enforced by `asyncio.Semaphore` in the scheduler (`engine.py:409,427`,
  `scheduler.py:254`). Hard-coded; not env-overridable.
- **Non-progress policy** (`src/forge_mcp/convergence.py`): `Signal = Literal["none",
  "NUDGE", "EARLY_STOP"]`; `detect_non_progress(history, window=2)` returns `EARLY_STOP`
  when the last `2*window` fingerprints all equal the latest, `NUDGE` when the last
  `window` do, else `none`. This drives honest early-stop → `incomplete`.

### §3.5 Run artifacts (`<target_dir>/.harness/<run-id>/`)

- Run dir: `<target_dir>/.harness/<run_id>` (`config.py:111,116`), created at mode `0o700`.
  `run_id` is `time.strftime("%Y%m%d%H%M%S", when)` (14 digits) with an optional
  `-NN` 2-digit collision suffix (`ids.py:33-34`).
- `.harness/.gitignore` is written once containing exactly `*` (`config.py:87-95`), so all
  run artifacts are self-ignored.
- Run-root files (`src/forge_mcp/artifacts.py`): `state.json` (durable checkpoint, :45),
  `run.log` (:55), `conflict_fingerprint.json` (:65), `spec.md` (:111),
  `spec_amendments.md` (:121), `spec.fingerprint` (:131), `planset.json` (:143),
  `plan-<plan_id>.md` (:154).
- `inputs/` subdir: `inputs/design.md` (frozen design, :88), `inputs/design.fingerprint`
  (:98).
- `plans/<plan_id>/`: `state.json` (:172), `manifest.json` (:181), `merge.json` (:190),
  and `iteration-<n>/` subdirs (:201).
- `iteration-<n>/`: `contract.md` (:210), `summary.md` (:219), `eval.json` (:228),
  `triage.json` (:237), `gap_fingerprint.json` (:246), `verify.txt` (:255),
  `git-violation.txt` (:264).

### §3.6 Configuration — environment variables

The package reads **exactly four** environment variables (full-tree grep confirmed):

| Variable | Read at | Default | Purpose |
|----------|---------|---------|---------|
| `FORGE_CLAUDE_BIN` | `config.py:53-57` | `which("claude")` → `~/.local/bin/claude` | Override the `claude` binary path |
| `FORGE_CODEX_BIN` | `config.py:69-73` | `which("codex")` → `~/.npm-global/bin/codex` | Override the `codex` binary path |
| `CLAUDE_CONFIG_DIR` | `config.py:40` | `~/.claude` | Select the Claude config/profile root |
| `CODEX_HOME` | `check.py:245` | `~/.codex` | Codex home used to locate skills during `forge check` |

- **`ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` are not referenced anywhere in the
  package** (0 matches). The README must therefore attribute Claude/Codex authentication to
  those tools' own login mechanisms, not to forge-mcp configuration.

### §3.7 `forge check` probes (preflight)

`run_checks(target_dir, *, probe_claude_live=True)` emits a flat list of `Check` rows with
`status: Literal["OK", "WARN", "FAIL"]`, in this fixed order (`check.py:343-352`); each is
isolated so a crash becomes one FAIL row:

1. `target writable` — `target_dir` exists, is a dir, is writable (WARN if no `target_dir`).
2. `git available` — `git` on PATH.
3. `claude CLI` — `claude` binary present (via `FORGE_CLAUDE_BIN`/PATH).
4. `codex binary` — `codex` binary present (via `FORGE_CODEX_BIN`/PATH).
5. `openai_codex importable` — the `openai_codex` package imports.
6. `codex --version smoke` — live `codex --version` (WARN if not on PATH; timeout `5.0s`).
7. `SDK contract` — Claude SDK seam symbols import (WARN if SDK absent → Codex-only mode).
8. `disk space` — free space at `target_dir`/home (WARN below `500 MiB`).
9. `codex-skill:<id>` — filesystem skills under `$CODEX_HOME/skills`; required ids
   `("executing-plans", "frontend-design")` (`skills.py:18`).
10. `claude-skill:<id>` — live Claude session skill discovery (skipped/WARN with
    `--no-live-claude`); required ids `("writing-plans", "code-review")` (`check.py:292`);
    deadline `20.0s` (`check.py:293`).

`any_fail` is `any(c.status == "FAIL" …)` — **WARN never fails** (`check.py:47`). `forge
serve` runs the same `run_checks(None)` as preflight and raises `RuntimeError` if any row
FAILs before starting `mcp.run()` (`server.py:180-185`).

### §3.8 Build & tooling (pyproject / scripts)

- Build backend: `hatchling`; wheel packages `src/forge_mcp` and force-includes
  `src/forge_mcp/prompts → forge_mcp/prompts` (`pyproject.toml:2-3,26-30`).
- `requires-python = ">=3.11"`; license MIT (`pyproject.toml:9-10`). Package exposes
  `__version__ = "0.1.0"` (`src/forge_mcp/__init__.py:5`).
- Runtime deps: `mcp[cli]`, `claude-agent-sdk`, `openai-codex`, `pydantic`, `typer`,
  `psutil` (`pyproject.toml:11-18`).
- **Dev deps are an extra**, not a PEP-735 group: `[project.optional-dependencies].dev =
  ["pytest >=8", "pytest-asyncio >=0.23", "ruff >=0.5", "pyright >=1.1.350"]`
  (`pyproject.toml:20-21`). → `uv sync --all-extras` installs them.
- ruff: `line-length = 100`, `target-version = "py311"`, `lint.select = ["E","F","I","B",
  "UP","ASYNC"]` (`pyproject.toml:33-37`).
- pyright: `include = ["src","tests","scripts"]`, `typeCheckingMode = "basic"`,
  `pythonVersion = "3.11"` (`pyproject.toml:40-43`).
- pytest: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `addopts = "-m 'not slow'"`
  (slow excluded by default), markers `slow`/`mcp`/`driver` (`pyproject.toml:46-53`).
- `scripts/ci.sh` is exactly 5 steps under `set -euo pipefail` (`ci.sh:1-7`):
  `uv run ruff check src tests scripts` → `uv run ruff format --check src tests scripts` →
  `uv run pyright` → `uv run python scripts/check_docstrings.py src tests scripts` →
  `uv run pytest`.
- `scripts/check_docstrings.py` enforces three labels `("Design:", "Implementation:",
  "Example:")`, each needing ≥5 non-whitespace chars (`check_docstrings.py:17,53`).
- `uv.lock` is committed; **no `.github/` CI pipeline exists** (`.github/` absent). The only
  repo consumer of `ci.sh` is `tests/test_scaffold.py:36-42` (`test_ci_script_executable`),
  which asserts the script *exists and is executable* — it does not inspect the contents;
  that test is not slow-marked, so it runs in the default `make test` gate
  (`pyproject.toml:52`).

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
(§3.8), so `make ci` and the merge gate are identical.

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
| `ci` | `lint test` (the merge gate; consumed by `scripts/ci.sh`, §4.4) | `ci.sh` consolidation |

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

### §4.4 `scripts/ci.sh` consolidation

`scripts/ci.sh` is rewritten to delegate to the Makefile so the 5-step gate has one
definition:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec make ci
```

`make ci` runs `lint` then `test` — the identical 5 commands in the identical order as the
current `ci.sh` (§3.8), so behavior is preserved. The `cd` makes the script robust to the
caller's working directory (make must find the Makefile). The sole consumer,
`tests/test_scaffold.py:36-42`, asserts only that `ci.sh` *exists and is executable* (not
its contents), so this rewrite is safe **provided the implementer keeps the file present and
preserves its executable bit** (`chmod +x scripts/ci.sh`).

## §5. README design

`README.md` is rewritten from scratch, comprehensive and accurate to `refactor`. Section
order and the verified content each section must carry:

1. **Title + summary** — `# forge-mcp`; one paragraph: a stdio MCP server exposing a single
   `run_forge` tool that drives a Planner (Claude) → Generator (Codex) → Evaluator (Claude)
   loop to implement a feature from a design document into `target_dir`, persisting all
   artifacts under `<target_dir>/.harness/<run-id>/` (§3.2, §3.5). State `__version__`
   `0.1.0` (§3.8).
2. **How it works** — Planner produces a plan set; the wave loop
   (`scheduling→executing→merging→amending`, ≤4 plans/wave) generates and evaluates;
   honest non-convergence stops early as `incomplete` (§3.4). Each phase runs in a fresh
   session; phases hand off through on-disk artifacts.
3. **Prerequisites** — Python ≥3.11; `uv`; `git`; the `codex` binary
   (`npm install -g @openai/codex`); the `claude` CLI. Note codex/claude are manual
   prerequisites (§2).
4. **Setup** — `make setup-tools` (installs `uv`), `make setup` (installs deps), then
   `uv run forge check` to verify the environment; list the 10 probes briefly and that exit
   `0` means OK while any `FAIL` must be fixed (§3.7).
5. **Register with Claude Code** — `claude mcp add forge-mcp -- uv run --directory "$PWD"
   forge serve`; show the optional `-e FORGE_CODEX_BIN=… -e CLAUDE_CONFIG_DIR=…` variant
   (server name before `-e` flags). `forge serve` runs preflight and refuses to start if
   any check FAILs (§3.2, §3.7).
6. **The `run_forge` tool** — the §3.2 5-row parameter table; the exactly-one-design-doc
   rule with both verbatim error messages; the §3.3 `RunResult` table with the three
   `status` meanings; the runtime-cap behavior (timeout → `incomplete`, not an error).
7. **Run lifecycle** — the §3.4 states and terminal set; a Mermaid diagram built **only**
   from the verified legal edges; a note that `failed` is reachable from any non-terminal
   state; the `NUDGE`/`EARLY_STOP` non-progress policy.
8. **Artifacts** — the §3.5 `.harness/<run-id>/` tree (run-root files, `inputs/`,
   `plans/<plan_id>/`, `iteration-<n>/`), and that `.harness` is self-ignored via its `*`
   `.gitignore`.
9. **CLI reference** — `forge serve` and `forge check [--target-dir PATH]
   [--live-claude/--no-live-claude]` with the `[STATUS] label: detail` output format and
   exit-1-on-FAIL behavior; `python -m forge_mcp` equivalence (§3.1).
10. **Configuration** — the §3.6 four-variable table. A short note: forge-mcp reads only
    these; Claude and Codex authentication is configured through those tools' own login
    mechanisms (no `ANTHROPIC_API_KEY` handling in forge-mcp).
11. **Development** — the `make help` target table; `make fmt` / `make lint` / `make test`
    / `make ci`; that `ci.sh` now delegates to `make ci`; slow tests via `uv run pytest -m
    slow`; the Rule-21 docstring policy enforced by `check_docstrings.py`, ruff, and pyright
    (§3.8). `make build` for the wheel + sdist.
12. **License** — MIT (§3.8).

The README references `develop`'s structure as inspiration only; all content is rewritten
and validated against §3. No `develop`-era surface (`doctor`, `version`, `verify_command`,
`resume`, `FORGE_LINEAGE_TOP_K`, `FORGE_HARNESS_ROOTS`, `run_id`/`runtime_seconds`) appears.

## §6. Toolchain rationale (best-practice, context7-grounded)

- **`uv sync --locked --all-extras` for `setup`.** Current uv guidance (and uv's own
  CI/Docker examples) install from the lockfile with `--locked`, which asserts
  `uv.lock` is consistent with `pyproject.toml` and installs the pinned set — reproducible
  onboarding. `--all-extras` pulls the `dev` extra (§3.8). We do **not** add `--dev`: that
  flag selects a PEP-735 dependency *group*, which this project does not define.
- **`uv lock --upgrade` + `uv sync` for `update`.** uv prefers already-locked versions on
  a plain `sync`/`lock`; `--upgrade` is the documented way to move every package to the
  latest version permitted by `pyproject` constraints, then `sync` installs the result.
- **Shared uv cache (no `UV_CACHE_DIR` override).** uv's default shared cache accelerates
  installs across projects; redirecting it to a project-local directory (as `develop` did)
  forfeits that benefit. We keep the default — the correct choice, not the convenient one.
- **One source of truth for the gate** (§4.4): `make ci` defines the checks; `ci.sh`
  delegates. This removes the drift that two independent definitions invite.
- **`fmt`/`lint` separation** keeps verification non-mutating — a CI run never rewrites the
  tree it is checking.

## §7. Done criteria

1. `make help` lists every target with its one-line description.
2. `make lint` runs exactly the four checks of `ci.sh:3-6`; `make test` runs `ci.sh:7`;
   `make ci` == the full former `ci.sh`. Running `scripts/ci.sh` and `make ci` produce the
   same steps in the same order. The rewritten `scripts/ci.sh` stays present and executable
   so `tests/test_scaffold.py::test_ci_script_executable` keeps passing.
3. `make setup-tools` is idempotent (no-op when `uv` is present); `make setup` installs from
   the lockfile; `make update` re-locks then installs.
4. `make build` produces a wheel + sdist under `dist/`.
5. Every Makefile target has a `Design / Implementation / Example` comment block plus a
   `## ` help line.
6. Every factual statement in `README.md` matches a §3 fact; no `develop`-era or
   non-existent surface appears.
