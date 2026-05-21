"""Shared fixtures for forge-mcp tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def target_dir(tmp_path: Path) -> Path:
    """Return a pristine temporary directory standing in for `target_dir`.

    Design: §13 roots all run artifacts under `<target_dir>/.harness/`, so
        tests need a clean directory the production code can canonicalize and
        mkdir into without colliding across cases.
    Implementation: create a `target` child under pytest's tmp_path; the
        harness creates `.harness/` itself when needed.
    Example: def test_x(target_dir): (target_dir / ".harness").mkdir().
    """
    d = tmp_path / "target"
    d.mkdir()
    return d


@pytest.fixture()
def harness_dir(target_dir: Path) -> Path:
    """Return a private `.harness` directory under `target_dir`.

    Design: §13 requires `.harness/` directories to be 0700 so tests that use
        artifact helpers start from production-shaped permissions.
    Implementation: create `target_dir / ".harness"` with mode 0o700 and
        return that path unchanged.
    Example: def test_x(harness_dir): assert harness_dir.name == ".harness".
    """
    d = target_dir / ".harness"
    d.mkdir(mode=0o700)
    return d
