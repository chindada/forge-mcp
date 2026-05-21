#!/usr/bin/env bash
set -euo pipefail
UV_CACHE_DIR=.harness/uv-cache uv run ruff check
UV_CACHE_DIR=.harness/uv-cache uv run ruff format --check
UV_CACHE_DIR=.harness/uv-cache uv run pyright
UV_CACHE_DIR=.harness/uv-cache uv run python scripts/check_docstrings.py src tests scripts
UV_CACHE_DIR=.harness/uv-cache uv run pytest -m "not slow"
