# forge-mcp — Build and Tooling

**What:** A normative enhancement brief that introduces a top-level
`Makefile` as the single developer-facing entry point for the
static-check matrix and the project lifecycle (`setup-tools`, `setup`,
`update`, `fmt`, `lint`, `test`, `test-slow`, `test-all`, `build`,
`clean`, `ci`, `help`). Companion to `forge-mcp-design.md`,
`forge-mcp-long-run-hardening.md`, `forge-mcp-long-run-continuity.md`,
`forge-mcp-resource-surface.md`, `forge-mcp-cross-run-learning.md`,
and `forge-mcp-host-protocol-and-planner-extensions.md`: the base doc
wins on anything it already specifies; each prior companion wins on
anything it specifies; this brief only adds new behavior in its own
`§M*`, `M-Invariant N`, and `M-Decision N` namespaces so code comments
can cite it unambiguously (e.g. `# §M2.3 fmt order`,
`# §M-Inv 3 ci mirrors scripts/ci.sh`).

**Status:** Design complete. No `Makefile` currently exists;
`scripts/ci.sh` is the de facto static-check entry point. This brief
is the implementation contract for that first-time addition. The §18
schema-pin tests continue to derive from
`RunForgeInput.model_json_schema()` /
`RunResult.model_json_schema()` — no shift for this brief.

**Audience:** The implementing agent. Precision over prose; the
Makefile body in §M2 is itself normative — every divergence from those
lines is a defect.

**Scope (1 enhancement, chosen explicitly):** M1 a `Makefile` at the
repo root exposing the static-check + lifecycle target matrix; M2
`scripts/ci.sh` shrunk to an `exec make ci` wrapper; M3 minimal
`README.md` / `CLAUDE.md` updates that point at the new entry point
while preserving the documented raw `uv` invocations. **Explicitly
out of scope:** any `run` / `serve` target (operational concerns
remain in the `forge` CLI — `M-Inv 0`); auto-sync via sentinel files
(`M-Decision 1`); an `update-tools` target wrapping `uv self update`
(future extension, §M9); Windows / PowerShell support (consistent
with the existing `scripts/ci.sh` — no regression).

---

## M0. Thesis, north star, and what does *not* change

### M0.1 The gap this brief closes

The project currently exposes its build matrix as raw `uv` invocations
plus `bash scripts/ci.sh`. Three places this becomes friction:

- **Discovery.** A new contributor cloning forge-mcp finds
  `uv run pytest -m "not slow"` and `bash scripts/ci.sh` documented in
  `CLAUDE.md`, but no single command answers "what targets do I have?"
  The full lifecycle surface is implicit.
- **CI / local drift.** `scripts/ci.sh` is the merge gate but is not
  the local entry point — a contributor's local commands can diverge
  from CI's set, and a passing local run does not guarantee a passing
  CI run. Today the drift surface is small (five commands), but it
  grows with every static check added.
- **Lifecycle gaps.** `uv sync` is documented for first-time setup,
  but there is no equivalent for "refresh all deps to latest
  compatible" (every contributor invents their own
  `uv lock --upgrade` invocation), no `clean` target (caches accrete
  in the repo root), and no single command that completes "from a
  fresh clone to `make test` passing."

A `Makefile` is the standard answer in every direction: GNU Make is
preinstalled on every macOS dev machine via Xcode Command Line Tools
and on every mainstream Linux distribution. Its target syntax is the
closest thing to a universal task-runner DSL. The cost of adding one
to a `uv`-managed project is roughly 50 lines.

### M0.2 The binding constraint (the north star is preserved)

The base brief's north star is *context anxiety* (§1): no operational
target may be added that erodes the four mechanisms (fresh SDK session,
file handoff, agent boundaries, honest non-convergence). The Makefile
is **not an operational entry point**: no target invokes `forge serve`
or otherwise serves the MCP transport. `test-slow` does exercise
orchestrator code via pytest (and its e2e tests internally spawn real
`claude` / `codex` subprocesses), but those subprocesses are bounded
by the test's lifetime, not held open as a server.

`M-Invariant 0 (north star):` the Makefile is not an operational entry
point. No target invokes `forge serve` or otherwise serves the MCP
transport. The operational entry point stays in the `forge` CLI. Test
targets MAY spawn supporting subprocesses (e.g., `claude` / `codex`
during `test-slow`), bounded by the test's lifetime. This is a hard
scoping rule, not a recommendation.

### M0.3 Architectural stance: thin wrapper, no logic in recipes

> **Alternative considered & rejected.** A Python-based task runner
> (`nox`, `tox`, `invoke`). Rejected: adds a Python dependency just
> to run other Python tools, slower startup than `make`, and
> introduces a parallel `pyproject.toml`-side configuration block
> that must stay in sync with `[tool.ruff]` / `[tool.pyright]`.
> `make`'s syntax IS the DSL — no second config language.

> **Alternative considered & rejected.** A `justfile` (`just` task
> runner). Rejected: requires installing `just` first, which becomes
> a `setup-tools` dependency for the *task runner itself* —
> chicken-and-egg with no marginal benefit over `make`, which is
> universally present.

> **Alternative considered & rejected.** Sentinel-driven auto-sync
> (`test` / `lint` / `build` depend on `.venv/.synced`, rebuilt when
> `pyproject.toml` or `uv.lock` changes). Rejected: `uv sync` is
> sub-second on a warm cache and already idempotent; sentinels add a
> class of "why did Make skip my target?" bugs for a benefit too
> small to justify.

`M-Decision 1 (no sentinel-driven auto-sync):` `test`, `lint`, and
`build` MUST NOT depend on a `.venv/.synced` sentinel. The
contributor runs `make setup` explicitly. Rationale: see the
rejected alternative immediately above — `uv sync` is already
idempotent and sub-second on a warm cache, and sentinels add a class
of debugging-hostile "why did Make skip my target?" bugs for a
benefit too small to justify.

> **Alternative considered & rejected.** Folding `claude` / `codex`
> CLI installation into `setup-tools`. Rejected: the static-check
> targets (`fmt`, `lint`, `test`, `build`) do not require those
> CLIs; the slow tests do, and operational use does, but neither
> path is in this brief's scope. `setup-tools` installs only what
> the Makefile's targets need — `uv`. The README continues to
> document `claude` / `codex` installation for slow-test and
> operational use.

The Makefile is a **transcription** of the `uv` invocations
contributors already run by hand. No recipe contains logic that does
not trivially reduce to one or two shell commands. Reading the
Makefile end-to-end teaches the project's build surface in under five
minutes; this spec teaches the *why*.

---

## M1. Target contract

| Target | One-line purpose |
|--------|------------------|
| `help` *(default)* | Show the target list (parsed from `## ` annotations) |
| `setup-tools` | Install `uv` if absent (one-time bootstrap) |
| `setup` | Install project + dev-extras deps from `uv.lock` into `.venv` |
| `update` | Refresh all Python deps to latest compatible, install |
| `fmt` | Safe ruff autofixes + format; mutates files |
| `lint` | All four static checks: `ruff check`, `ruff format --check`, `pyright`, `check_docstrings.py` |
| `test` | Fast tests (`pytest -m "not slow"`); matches CI |
| `test-slow` | Real-CLI end-to-end tests (`pytest -m slow`; claude + codex required) |
| `test-all` | Every test (fast + slow) in one `pytest` invocation |
| `build` | Wheel + sdist to `dist/` via `uv build` |
| `clean` | Remove caches and `dist/`; preserves `.venv` and `uv.lock` |
| `ci` | Merge gate: `lint` + `test` (matches `scripts/ci.sh`) |

`M-Invariant 1 (target set is closed):` exactly these twelve targets.
Adding a new target requires a brief revision. Rationale: every
additional target is one more knob a contributor must learn, and the
set above already covers every documented workflow.

`M-Invariant 2 (.PHONY discipline):` every target is `.PHONY`. No
target corresponds to a real file Make should `stat`. Rationale:
avoids the "Make refuses to rebuild" failure mode where a file or
directory accidentally satisfying a target name (e.g. a `build/`
directory) suppresses the recipe.

`M-Invariant 3 (ci runs these 5 static checks in this order):` the
`ci` target's transitive recipe set (the recipes of `lint` then
`test` expanded) is exactly these 5 commands in order, modulo the
`UV_CACHE_DIR=.harness/uv-cache` prefix that the Makefile sets once
via `export` (the pre-change `scripts/ci.sh` set it per line — the
two are equivalent at the shell-environment level):

1. `uv run ruff check`
2. `uv run ruff format --check`
3. `uv run pyright`
4. `uv run python scripts/check_docstrings.py src tests scripts`
5. `uv run pytest -m "not slow"`

Any divergence is a defect. Inlining the list (rather than referring
to "the body of `scripts/ci.sh`") makes the invariant verifiable
without consulting git history after the wrapper change in §M4.

---

## M2. Recipe semantics (normative)

The full Makefile body — every contributor reads this once, the
implementing agent writes it verbatim:

```make
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

export UV_CACHE_DIR ?= .harness/uv-cache

PYTEST_ARGS ?=
RUFF_ARGS ?=
RUFF_CHECK_ARGS ?=

.PHONY: help setup-tools setup update fmt lint test test-slow test-all build clean ci

help: ## Show this help (default target)
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z][a-zA-Z_-]*:.*## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup-tools: ## Install uv (one-time bootstrap)
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "+ curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		echo "Re-source your shell rc (e.g. 'source ~/.zshrc') for uv on PATH."; \
	else \
		echo "uv already installed: $$(uv --version)"; \
	fi

setup: ## Install project + dev-extras deps from uv.lock into .venv
	uv sync --all-extras

update: ## Refresh all Python deps to latest compatible, install
	uv lock --upgrade
	uv sync --all-extras

fmt: ## Apply safe ruff autofixes + format (mutates files)
	uv run ruff check --fix $(RUFF_CHECK_ARGS) $(RUFF_ARGS)
	uv run ruff format $(RUFF_ARGS)

lint: ## Run all four static checks (ruff + pyright + docstrings)
	uv run ruff check $(RUFF_CHECK_ARGS) $(RUFF_ARGS)
	uv run ruff format --check $(RUFF_ARGS)
	uv run pyright
	uv run python scripts/check_docstrings.py src tests scripts

test: ## Run fast tests (pytest -m "not slow"); matches CI
	uv run pytest -m "not slow" $(PYTEST_ARGS)

test-slow: ## Run slow tests (real claude + codex required)
	uv run pytest -m slow $(PYTEST_ARGS)

test-all: ## Run every test (fast + slow) in one pytest invocation
	uv run pytest $(PYTEST_ARGS)

build: ## Build wheel + sdist to dist/
	uv build

clean: ## Remove caches and dist/ (preserves .venv and uv.lock)
	rm -rf dist .pytest_cache .ruff_cache .pyright_cache .harness/uv-cache
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +

ci: lint test ## Merge gate: lint + test (matches scripts/ci.sh)
```

Notes on individual recipes follow. Every note is binding; deviating
from a note is a defect.

### M2.1 `setup-tools`

Idempotent by construction. The `command -v uv` probe avoids
re-running the installer if `uv` is already present. The "re-source
your shell rc" echo addresses the documented Astral-installer
behavior: the installer modifies `~/.zshrc` / `~/.bashrc` to add
`~/.local/bin` to `PATH`, but subsequent `uv` invocations in the
*same* shell session still fail until the user re-sources. This is an
installer behavior, not a Makefile bug.

`M-Invariant 4 (setup-tools idempotency):` calling `make setup-tools`
twice on the same machine MUST succeed both times. The
check-then-install branch achieves this without any state on disk.

### M2.2 `setup` and `update`

`setup` is `uv sync --all-extras`. The `--all-extras` flag installs
the dev tools (`pytest`, `pytest-asyncio`, `ruff`, `pyright`)
declared in `pyproject.toml`'s `[project.optional-dependencies]`;
bare `uv sync` would skip them (uv auto-installs
`[dependency-groups]`, not `[optional-dependencies]`) and `fmt`,
`lint`, and `test` would then fail with "command not found" inside
the venv. This matches `README.md`'s documented dev-setup invocation
(README line 124).

`update` is the two-step `uv lock --upgrade` then
`uv sync --all-extras`. `uv lock --upgrade` refreshes every
dependency in `uv.lock` to its latest compatible version (verified
against `/astral-sh/uv` docs at design time). `uv sync --all-extras`
applies the new lockfile to `.venv` including dev extras. For the
PyPI-pinned `openai-codex >=0.1.0b2`, `uv lock --upgrade`
re-resolves to the latest compatible published beta within the
floor — verified by the implementing agent during §M7 scenario 4.

`M-Decision 2 (no uv self update in update):` `uv` itself is NOT
auto-upgraded by `make update`. Tool-version pinning is a
reproducibility concern (a new `uv` release can re-resolve a
lockfile, invalidate prior CI runs, or behaviorally drift). Tool
upgrades stay on demand: contributor runs `uv self update` manually,
or a future `update-tools` target adds it explicitly (§M9).

### M2.3 `fmt`

Two-step in canonical ruff order: `ruff check --fix` first (removes
unused imports, fixes import ordering), `ruff format` second
(whitespace, quote style, line length). Reversing the order would
format the file, then re-format it again after the autofix-driven
import removal — wasted work and a marginally different final state.
`RUFF_ARGS` applies to both steps (use for flags accepted by both
subcommands, e.g., `--no-cache`); `RUFF_CHECK_ARGS` applies only to
the `ruff check` step (use for `check`-only flags like
`--unsafe-fixes` that `ruff format` rejects).

`M-Decision 3 (no --unsafe-fixes by default):` `fmt` runs only safe
ruff autofixes. Unsafe fixes can change program semantics (e.g.,
remove a `pass` statement that was the only body of an executable
block). Opt-in only: `make fmt RUFF_CHECK_ARGS=--unsafe-fixes` —
`RUFF_CHECK_ARGS` is appended to `ruff check` only, NOT to
`ruff format` (which rejects `--unsafe-fixes`). Symmetric flags
(e.g., `--no-cache`) go through `RUFF_ARGS`.

### M2.4 `lint`

Four separate recipe lines, one per static check. Make runs each
recipe line in its own shell (Make's default behavior), and
`.SHELLFLAGS := -eu -o pipefail -c` makes that shell abort on the
first failing command — so a `ruff check` failure short-circuits
before `pyright` runs, the same first-failure behavior as
`scripts/ci.sh`. Verified by `M-Invariant 3`.

Four separate lines instead of `cmd1 && cmd2 && cmd3 && cmd4`: Make's
recipe-per-line model produces a cleaner failure message — the
failing line is the line Make highlights — and avoids the
trailing-backslash continuation that obscures which check failed.

### M2.5 `test`, `test-slow`, `test-all`

`test` matches the last line of the pre-change `scripts/ci.sh`
exactly. `test-slow` is the documented `uv run pytest -m slow` from
`CLAUDE.md`. `test-all` is a single `pytest` invocation with no
marker filter (collecting every test, including unmarked, `slow`,
`mcp`, and `driver`).

`test-all` deliberately does NOT chain `test test-slow` as
prerequisites: chained prereq targets can run in parallel under
`make -j`, two concurrent pytest processes both writing to
`.pytest_cache` would race, and even sequentially they pay a
double-startup tax. One pytest process collects everything once.

`PYTEST_ARGS` allows targeted invocations
(`make test PYTEST_ARGS="-k test_foo -x"`) without bypassing the
Makefile.

### M2.6 `build`

`uv build` produces both a wheel and an sdist in `dist/` by default.
Selective build (`uv build --wheel`, `--sdist`) is NOT plumbed
through a `BUILD_ARGS` variable — `uv build` defaults are the right
defaults for a `hatchling` project. Out of scope until a contributor
demonstrates a need.

### M2.7 `clean`

Surgical: removes (if present) the four caches (`.pytest_cache`,
`.ruff_cache`, `.pyright_cache`, `.harness/uv-cache`), the `dist/`
build output, and every `__pycache__` directory under the project's
Python source roots (`src/`, `tests/`, `scripts/`). `rm -rf` and
`find` are silent on missing paths — a fresh clone with no caches
yet still passes `make clean`. The `find` is intentionally scoped to
those three roots — a bare `find . -type d -name __pycache__ …`
would walk into `.venv/lib/**/site-packages/` and delete the 90+
bytecode-cache directories of installed packages, violating
`M-Invariant 5`. `.venv` is preserved (rebuildable from `uv.lock`,
expensive to recreate). `uv.lock` is preserved (it IS the source of
truth — `clean` is not `distclean`).

`M-Invariant 5 (clean preserves .venv and uv.lock):` `make clean`
MUST NOT touch `.venv/` or `uv.lock`. A `make clean && make test`
cycle must succeed without re-running `make setup`.

### M2.8 `ci`

Prerequisite-only: `ci: lint test`. The merge gate. `M-Invariant 3`
ties `ci`'s effect to `scripts/ci.sh`. Adding `build` to `ci` is a
deliberate non-decision: `scripts/ci.sh` does not currently build,
so neither does `ci`. Promoting `build` to the merge gate is a
separate change, §M9.

---

## M3. Variables and environment

```make
export UV_CACHE_DIR ?= .harness/uv-cache
PYTEST_ARGS ?=
RUFF_ARGS ?=
RUFF_CHECK_ARGS ?=
```

| Var | Default | Effect |
|-----|---------|--------|
| `UV_CACHE_DIR` | `.harness/uv-cache` | Project-local uv cache (matches `scripts/ci.sh`). Exported so every `uv` invocation honors it. |
| `PYTEST_ARGS` | empty | Appended to every pytest invocation (`test`, `test-slow`, `test-all`). |
| `RUFF_ARGS` | empty | Appended to both `ruff check` and `ruff format` invocations in `fmt` and `lint`. Use for flags accepted by both subcommands (`--no-cache`, `--config`, `--isolated`). |
| `RUFF_CHECK_ARGS` | empty | Appended to `ruff check` invocations only (in `fmt` and `lint`). Use for `check`-specific flags like `--unsafe-fixes` that `ruff format` rejects. |

`M-Decision 4 (UV_CACHE_DIR is exported, not per-recipe-prefixed):`
the pre-change `scripts/ci.sh` set `UV_CACHE_DIR=…` on every line.
The Makefile sets it once via `export`, applying to every recipe.
Same effect, less repetition. Verified by `M-Invariant 3`.

The `?=` defaults let CI / contributors override without editing the
file:

```sh
make test PYTEST_ARGS="-k bridge -x"
make lint RUFF_ARGS="--no-cache"
make ci UV_CACHE_DIR=/tmp/uv-cache  # off the project tree
```

---

## M4. `scripts/ci.sh` becomes a wrapper

```sh
#!/usr/bin/env bash
set -euo pipefail
exec make ci
```

That is the entire new body. The two-line wrapper preserves the
script's role as the stable CI entry point (any external CI
configuration that calls `bash scripts/ci.sh` continues working) and
eliminates the drift surface: the Makefile is the only place the
static-check matrix is defined.

`M-Invariant 6 (scripts/ci.sh is a wrapper, not a parallel
definition):` `scripts/ci.sh` MUST NOT define the static-check steps
directly. Any step that should run in CI goes in the Makefile; CI
calls `make ci`.

---

## M5. File conventions

### M5.1 `SHELL := bash` and `.SHELLFLAGS := -eu -o pipefail -c`

Pinned at the top. Without `SHELL := bash`, recipes run under
`/bin/sh` — bash-in-POSIX-mode on macOS, `dash` on Debian-derived
Linux. POSIX `sh` does not guarantee `set -o pipefail`, `[[ … ]]`
tests, process substitution `<( … )`, arrays, or other bash
extensions. Pinning to `bash` gives a single, predictable execution
semantics across every documented dev environment.

`.SHELLFLAGS := -eu -o pipefail -c` makes every recipe line behave
like a script with `set -euo pipefail` at the top — `set -e` (abort
on non-zero exit), `set -u` (abort on unset variable expansion),
`set -o pipefail` (abort if any pipeline stage fails). The
`setup-tools` recipe's `if ! command -v uv …` *condition* is the
documented `set -e` exemption (POSIX: the controlling command of a
compound statement). Commands inside the `then` branch (including
`curl … | sh`) remain subject to `set -e` and `pipefail` — an
installer failure aborts the recipe.

`M-Invariant 7 (SHELL is pinned bash):` no recipe may rely on `dash`-
isms or `/bin/sh`-isms; recipes target bash semantics.

### M5.2 `.DEFAULT_GOAL := help`

Bare `make` shows the help banner, not the first defined target. The
help banner is the discovery surface — contributors who do not yet
know the target list reach it first.

### M5.3 Help annotations

Every contributor-facing target line carries a trailing `##
description`. The `help` recipe's `awk` parses these from
`$(MAKEFILE_LIST)`. Adding a target without a `##` annotation hides
it from `make help` — a forcing function for discoverability.

`M-Invariant 8 (every contributor-facing target has a ##
annotation):` if a target appears in the §M1 contract table, its
definition in the Makefile MUST carry a `##` annotation.

### M5.4 Tabs vs spaces

Recipe lines are tab-indented (Make requirement; spaces produce
`missing separator` errors at parse time). Variable assignments and
`.PHONY` lines use no indentation. Editor configuration is unchanged
— the existing project setup handles this correctly.

---

## M6. Non-goals (re-asserted)

- **No `run` / `serve` targets.** Operational targets stay in the
  `forge` CLI. `M-Inv 0`.
- **No auto-sync via sentinels.** `M-Decision 1`.
- **No `uv self update` in `update`.** `M-Decision 2`.
- **No `BUILD_ARGS` plumbing.** §M2.6.
- **No Windows / PowerShell support.** Consistent with `scripts/ci.sh`.
- **No `forge doctor` integration.** `doctor` is runtime preflight,
  not a static check.
- **No `coverage` target.** `pyproject.toml` does not configure
  coverage; adding it is a separate change.
- **No parallel test execution (`pytest -n auto`).** Out of scope; if
  added later, contributors invoke it via
  `make test PYTEST_ARGS="-n auto"` until promoted.

---

## M7. Verification scenarios

The implementing agent MUST run all of these before declaring the
task complete. There is no automated test suite for a Makefile; the
scenarios below ARE the verification. Each scenario reports
PASS / FAIL in the PR description.

1. `make` (no args) prints the help banner via `awk`, exits 0.
2. `make help` matches scenario 1's output.
3. `make setup-tools` on a machine where `uv` exists prints
   `uv already installed: …` and exits 0. (The install branch cannot
   be exercised on a machine that already has `uv` without
   uninstalling first; the implementing agent reads the recipe and
   confirms by inspection that the `curl … | sh` branch is reachable
   when `command -v uv` returns non-zero.)
4. `make update` exits 0 and re-resolves `uv.lock`. The lockfile
   updates if upstream has new commits (e.g., `openai-codex @ main`
   revision shifts); if not, the lockfile may be byte-identical —
   re-resolution is correctness-preserving either way.
5. `make setup` after `make update` is effectively a no-op
   (`uv sync --all-extras` reports "Audited N packages …" or similar).
6. `make fmt` runs cleanly on the freshly-cloned, freshly-formatted
   tree: `git diff --quiet` after `make fmt` succeeds.
7. `make fmt && make lint` is a no-op cycle: after `fmt` mutates
   nothing (because the tree was already clean), `lint` exits 0.
8. `make lint` line-by-line matches the four static-check lines
   (the four `uv run …` invocations) of the pre-change
   `scripts/ci.sh`, modulo the `UV_CACHE_DIR=…` prefix.
   `M-Invariant 3`.
9. `make test` matches `scripts/ci.sh`'s last line. `M-Invariant 3`.
10. `make ci` produces the same outcome (exit code + visible step
    set) as running the pre-change `scripts/ci.sh`.
11. `make build` produces a `dist/forge_mcp-*.whl` and a matching
    `dist/forge_mcp-*.tar.gz` (the version glob is read from
    `pyproject.toml` at build time); both extract cleanly (a quick
    `unzip -l` / `tar tzf` check).
12. After `make clean`, none of `dist/`, `.pytest_cache/`,
    `.ruff_cache/`, `.pyright_cache/`, `.harness/uv-cache/`, or any
    `__pycache__/` directory under `src/`, `tests/`, `scripts/` is
    present (whether or not they existed before — `rm -rf` and
    `find` are silent on missing paths). `.venv/` (including
    `.venv/lib/**/__pycache__`) and `uv.lock` persist.
    `M-Invariant 5`.
13. After `make clean`, `make test` succeeds without re-running
    `make setup` (`.venv` was preserved).

Scenarios 1, 2, 6, 7, 8, 9, 10, 11, 12, 13 are mandatory before
declaring done. Scenario 3 is verified by recipe inspection.
Scenarios 4 and 5 require live network access; if a sandbox blocks
network, the agent records the limitation and runs the scenarios on
an unblocked machine.

---

## M8. Documentation updates (in scope, same change)

### M8.1 `README.md`

If the `Commands` section references `bash scripts/ci.sh` or
`uv run pytest -m "not slow"` directly, update it to point at the
`make` targets (with the raw `uv` invocations kept as the "if Make is
unavailable" fallback for clarity). The implementing agent reads
`README.md` first and produces the smallest delta that preserves
information.

### M8.2 `CLAUDE.md`

Two updates are required:

1. **Source-of-truth section.** Append a paragraph mirroring the
   existing companion-brief paragraphs (resource-surface,
   cross-run-learning) so the stack documentation stays current. The
   paragraph MUST: name this brief
   (`forge-mcp-build-and-tooling.md`); name the `§M*`,
   `M-Invariant N`, `M-Decision N` namespace with citation examples
   (`# §M2.3 fmt order`, `# §M-Inv 3 ci mirrors scripts/ci.sh`);
   state the precedence chain (base + five prior companion briefs
   still win on what they specify; this brief only adds new
   behavior); and re-affirm the §18 schema-pin posture (no shift —
   the Makefile touches no Pydantic schema).

2. **Commands section.** Append a short note that `make help` lists
   targets and that the documented `uv run` commands continue to
   work as the underlying primitives. Do NOT rewrite the existing
   command block — the raw commands remain useful for ad-hoc
   invocation and for any contributor on a Make-less machine.

`M-Invariant 9 (raw uv commands stay documented):` `CLAUDE.md` and
`README.md` continue to list the underlying `uv run …` invocations
alongside the Make targets. The Makefile is a convenience layer, not
a wall.

---

## M9. Forward-looking (NOT this brief)

These are catalogued so future PRs do not re-derive them:

- **`update-tools` target.** If `uv self update` is wanted as a Make
  target, add it as `update-tools` (matching the `setup-tools`
  naming). One line: `uv self update`. `M-Decision 2`.
- **`build` in CI.** If a future CI policy wants to verify the
  package builds on every merge, add `build` to the `ci` target's
  prerequisites and update `M-Invariant 3` accordingly (the
  invariant text becomes "lint + test + build" and the
  pre-change-`scripts/ci.sh` reference is replaced by a new
  baseline).
- **Coverage target.** If `pytest-cov` is adopted, add a `coverage`
  target and a `coverage` line to `ci`'s prerequisites. Update
  `pyproject.toml` `[tool.coverage.*]`.
- **Parallel tests.** `make test PYTEST_ARGS="-n auto"` already
  works once `pytest-xdist` is in dev dependencies. Promoting to a
  default requires adding the dep and recording the decision here.
- **Windows / PowerShell.** Out of scope but not blocked: a future
  `scripts/ci.ps1` could mirror `scripts/ci.sh`'s `exec make ci`
  wrapper using `pwsh`. None of this brief precludes that.

---

## M10. Risks and mitigations

1. **`curl | sh` in `setup-tools` is a security surface.** Mitigation:
   the URL is hardcoded to `https://astral.sh/uv/install.sh` (Astral's
   documented installer), the recipe runs the installer only when
   `uv` is genuinely absent (idempotency check), and the recipe
   explicitly echoes the curl command (`echo "+ curl …"` in §M2's
   `setup-tools` body) before invoking it so the contributor sees
   exactly what will run — even with the `@` recipe-echo suppression
   in place. The README may document the `brew install uv` alternative
   for macOS as a non-normative aside; the brief's normative path is
   one cross-platform installer.

2. **`SHELL := bash` on a system without bash.** Mitigation: every
   documented developer environment (macOS + every mainstream Linux
   distribution) ships bash. The brief disclaims Windows support; a
   future BSD-only contributor can override `SHELL` locally.

3. **`uv lock --upgrade` re-resolving `openai-codex` to a broken
   published beta.** Mitigation: `update` is contributor-initiated, not
   CI-automatic. The contributor runs `make ci` (or `make test`)
   after `make update` and reverts the lockfile if upstream is
   broken. The brief deliberately does NOT chain `update -> test`:
   surfacing a broken upstream is the contributor's signal, not
   Make's.

4. **A new contributor running `make ci` before `make setup`.**
   Mitigation: the help banner makes the
   `setup-tools` → `setup` → `ci` order explicit; bare `make` shows
   it. The more aggressive option (`ci` depends on `setup`) was
   considered and rejected: every `make ci` invocation would re-run
   `uv sync`, which is fast but non-zero, and would obscure where
   wall-clock time is spent.

5. **Drift in `M-Invariant 3` (`ci` ≠ `scripts/ci.sh`).** Mitigation:
   `scripts/ci.sh` is a two-line wrapper after this change — drift
   is impossible by construction. If a future contributor expands
   `scripts/ci.sh` again, they violate `M-Invariant 6` and the brief
   revision process kicks in.

6. **A contributor running `make test-all` under `make -j2`.**
   Mitigation: `test-all` is a single `pytest` recipe (one process),
   not a prerequisite chain, so `-j` has no effect on it (§M2.5).
   Parallelism inside the test run is opt-in via
   `PYTEST_ARGS="-n auto"` with `pytest-xdist` installed (§M9).

---

End of brief.
