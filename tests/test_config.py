# tests/test_config.py
from __future__ import annotations

import time

from forge_mcp import config
from forge_mcp.ids import is_run_id


def test_env_overrides(monkeypatch, tmp_path):
    """Design: §10.4 env vars take precedence over PATH/default fallbacks.
    Implementation: set FORGE_CODEX_BIN and CLAUDE_CONFIG_DIR.
    Example: codex_bin() == the env path.
    """
    monkeypatch.setenv("FORGE_CODEX_BIN", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert config.codex_bin() == tmp_path / "codex"
    assert config.claude_config_dir() == tmp_path / "cc"


def test_claude_bin_makes_relative_override_independent_of_child_cwd(monkeypatch, tmp_path):
    """Resolve one Claude executable before SDK sessions change child cwd.

    Design: preflight and every Claude stage must address the identical binary,
        even when FORGE_CLAUDE_BIN is relative and a stage uses another cwd.
    Implementation: resolve from a server directory, then compare the returned
        path after moving the test process to a distinct target directory.
    Example: ``bin/claude`` becomes ``/server/bin/claude`` for every stage.
    """
    server_dir = tmp_path / "server"
    target_dir = tmp_path / "target"
    claude_path = server_dir / "bin" / "claude"
    claude_path.parent.mkdir(parents=True)
    claude_path.touch()
    target_dir.mkdir()
    monkeypatch.chdir(server_dir)
    monkeypatch.setenv("FORGE_CLAUDE_BIN", "bin/claude")

    resolved = config.claude_bin()
    monkeypatch.chdir(target_dir)

    assert resolved.is_absolute()
    assert resolved == claude_path.resolve()


def test_create_run_dir_makes_gitignore_and_timestamp_dir(tmp_path):
    """Design: §11/§12 .harness is self-ignored; run dir is a valid run-id.
    Implementation: create_run_dir then inspect.
    Example: .harness/.gitignore == '*'; run dir name is a run-id.
    """
    when = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    rd = config.create_run_dir(tmp_path, when)
    assert (tmp_path / ".harness" / ".gitignore").read_text() == "*"
    assert is_run_id(rd.name) and rd.is_dir()


def test_same_second_rerun_uniquifies(tmp_path):
    """Design: §12 same-second re-run appends -NN.
    Implementation: two create_run_dir with identical struct_time.
    Example: second dir name ends with -01.
    """
    when = time.struct_time((2026, 6, 23, 18, 31, 2, 0, 0, 0))
    a = config.create_run_dir(tmp_path, when)
    b = config.create_run_dir(tmp_path, when)
    assert a.name != b.name and b.name.endswith("-01")


def test_concurrency_cap_removed():
    """Design: §11 the single-plan harness has no concurrency, so CONCURRENCY_CAP is gone.
    Implementation: the module no longer defines the constant.
    Example: hasattr(config, 'CONCURRENCY_CAP') is False.
    """
    assert not hasattr(config, "CONCURRENCY_CAP")
