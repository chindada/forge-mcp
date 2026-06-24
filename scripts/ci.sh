#!/usr/bin/env bash
set -euo pipefail
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright
uv run python scripts/check_docstrings.py src tests scripts
uv run pytest
