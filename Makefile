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
