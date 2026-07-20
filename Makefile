SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help setup-tools setup update fmt lint test build ci

# help — print every target and its one-line description (the default goal).
#
# Design: a self-documenting Makefile so plain `make` lists what is runnable,
#   keeping discovery in the Makefile itself rather than a separate doc (§4.2).
# Implementation: awk scans MAKEFILE_LIST for lines matching a target followed by
#   a `## ` comment (regex `^[a-zA-Z][a-zA-Z0-9_-]*:.*## ` — the `_-` in the class
#   is required so `setup-tools:` matches) and prints "name  description". The
#   comment-block `#` lines start with `# ` so they never match.
# Example: `make` or `make help` prints the aligned target table.
help: ## Show this help (targets + one-line descriptions)
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z][a-zA-Z0-9_-]*:.*## / {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# setup-tools — bootstrap the only host tool the Makefile manages: uv.
#
# Design: §2 scopes host bootstrap to uv alone; the codex binary and claude CLI
#   are documented manual prerequisites, so this never touches global npm.
# Implementation: idempotent — `command -v uv` short-circuits when uv is already
#   on PATH; otherwise install via the official astral.sh script. The `|| …`
#   form is safe under `set -e` because the guard is the left side of `||`.
# Example: `make setup-tools` is a no-op on a machine that already has uv.
setup-tools: ## Install uv if missing (no-op when present)
	command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

# setup — install the project + dev tools from the pinned lockfile.
#
# Design: reproducible onboarding — §6 mandates `--locked` so a stale lock fails
#   fast instead of silently re-resolving; changing dependencies is `update`'s job.
# Implementation: `uv sync --locked --all-extras` installs exactly uv.lock and
#   pulls the `dev` extra (§3.8 — dev tools live in optional-dependencies, an
#   extra, so `--all-extras` is correct and `--dev`, a PEP-735 group flag, is not).
# Example: `make setup` after `make setup-tools` yields a ready dev environment.
setup: ## Install deps from the locked file (dev extra included)
	uv sync --locked --all-extras

# update — re-resolve every dependency to the newest allowed, then install.
#
# Design: §6 — `uv lock --upgrade` is the documented way to move every package to
#   the latest version permitted by pyproject constraints; host tools are
#   refreshed separately by re-running `setup-tools`.
# Implementation: `uv lock --upgrade` rewrites uv.lock, then `uv sync --all-extras`
#   installs it — no `--locked` here because the lock was just rewritten.
# Example: `make update`, then commit the changed uv.lock.
update: ## Re-lock to latest allowed versions, then install
	uv lock --upgrade
	uv sync --all-extras

# fmt — apply ruff autofixes and format sources in place.
#
# Design: separate the mutating "fix it" action from the read-only `lint` gate so
#   verification never silently rewrites files; mirrors §3.8's fmt/lint split.
# Implementation: `ruff check --fix` first (lint/import autofixes), then
#   `ruff format`, both over `src tests scripts` — the same roots ci.sh and lint use.
# Example: `make fmt` rewrites imports and reformats; inspect with `git diff`.
fmt: ## Apply ruff autofixes + format (mutates files)
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

# lint — run the four read-only static checks (ci.sh:3-6) without mutating files.
#
# Design: §4.4 keeps these byte-for-byte identical to ci.sh's first four steps so
#   `make ci` and scripts/ci.sh can never drift; lint is non-mutating so a check
#   run never rewrites the tree it inspects.
# Implementation: ruff check, ruff format --check, pyright, then the Rule-21
#   docstring checker — each over `src tests scripts`, exactly as ci.sh:3-6.
# Example: `make lint` exits non-zero on the first failing check.
lint: ## Run ruff + pyright + docstring checks (read-only)
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run pyright
	uv run python scripts/check_docstrings.py src tests scripts

# test — run the pytest suite (slow tests excluded by default).
#
# Design: §4.4 — identical to ci.sh:7; slow/real-CLI tests are excluded by the
#   pyproject `addopts = "-m 'not slow'"` default (§3.8), so the gate stays fast.
# Implementation: `uv run pytest` honors pyproject's testpaths and addopts; run
#   slow tests on demand with `uv run pytest -m slow`.
# Example: `make test` runs the default (fast) suite; FAILs surface as non-zero.
test: ## Run the pytest suite (fast; slow excluded)
	uv run pytest

# build — produce the wheel and sdist with the hatchling backend.
#
# Design: §3.8 — the package builds with hatchling; `uv build` is the standard
#   front-end producing both a wheel and an sdist under dist/.
# Implementation: `uv build` reads pyproject's build-system and writes the
#   forge_mcp-<version> wheel and the .tar.gz sdist to dist/.
# Example: `make build` then `ls dist/` shows the .whl and .tar.gz.
build: ## Build wheel + sdist into dist/
	uv build

# ci — the aggregate gate: lint then test (the same five commands as ci.sh).
#
# Design: §4.4 — `ci` is the local mirror of scripts/ci.sh; expanding to `lint`
#   then `test` runs the identical five commands in the identical order, so the
#   two entry points cannot behave differently.
# Implementation: a prerequisite-only target (`ci: lint test`) — make runs lint's
#   four checks, then test's pytest; it has no recipe body of its own.
# Example: `make ci` is the one command to run before pushing.
ci: lint test ## Run the full local gate (= scripts/ci.sh)
