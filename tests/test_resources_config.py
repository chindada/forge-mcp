"""§R3.3 / §R4.5 / §R9.6 — FORGE_HARNESS_ROOTS parsing + validation."""

from __future__ import annotations

import os

import pytest

from forge_mcp.config import RunConfig


def test_unset_yields_empty(monkeypatch, tmp_path):
    """Unset FORGE_HARNESS_ROOTS yields empty discovery roots.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.delenv("FORGE_HARNESS_ROOTS", raising=False)
    cfg = RunConfig.from_env()
    assert cfg.harness_roots == ()
    assert cfg.harness_root_tokens == {}


def test_one_valid_root(monkeypatch, tmp_path):
    """One absolute readable root is accepted.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    root = tmp_path / "harness-root"
    root.mkdir()
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(root))
    cfg = RunConfig.from_env()
    assert cfg.harness_roots == (root,)
    assert len(cfg.harness_root_tokens) == 1
    token, p = next(iter(cfg.harness_root_tokens.items()))
    assert p == root
    assert len(token) == 12


def test_two_valid_roots_comma_separated(monkeypatch, tmp_path):
    """Comma-separated absolute roots are accepted.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    r1 = tmp_path / "h1"
    r2 = tmp_path / "h2"
    r1.mkdir()
    r2.mkdir()
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", f"{r1},{r2}")
    cfg = RunConfig.from_env()
    assert set(cfg.harness_roots) == {r1, r2}
    assert len(cfg.harness_root_tokens) == 2


def test_non_existent_raises(monkeypatch, tmp_path):
    """Missing roots fail fast.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    bogus = tmp_path / "does-not-exist"
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(bogus))
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        RunConfig.from_env()


def test_file_not_dir_raises(monkeypatch, tmp_path):
    """Files are rejected as roots.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    p = tmp_path / "notadir"
    p.write_text("")
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(p))
    with pytest.raises((ValueError, NotADirectoryError, OSError)):
        RunConfig.from_env()


def test_relative_path_raises(monkeypatch, tmp_path):
    """Relative roots are rejected.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", "relative/path")
    with pytest.raises((ValueError, OSError)):
        RunConfig.from_env()


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses mode bits")
def test_unreadable_dir_raises(monkeypatch, tmp_path):
    """Unreadable roots fail fast.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    p = tmp_path / "noread"
    p.mkdir()
    p.chmod(0o000)
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", str(p))
    try:
        with pytest.raises((PermissionError, OSError, ValueError)):
            RunConfig.from_env()
    finally:
        p.chmod(0o700)


def test_empty_env_yields_empty(monkeypatch):
    """Empty FORGE_HARNESS_ROOTS is treated as unset.

    Design: §R tests pin the resource-surface behavior required by the plan.
    Implementation: The test constructs focused fixtures and asserts direct outputs.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setenv("FORGE_HARNESS_ROOTS", "")
    cfg = RunConfig.from_env()
    assert cfg.harness_roots == ()
    assert cfg.harness_root_tokens == {}
