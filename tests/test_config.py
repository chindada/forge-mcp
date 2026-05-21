from pathlib import Path

from forge_mcp.config import RunConfig


def test_env_defaults(monkeypatch):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.delenv("FORGE_CODEX_BIN", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FORGE_CLAUDE_CLI_PATH", raising=False)
    cfg = RunConfig.from_env()
    assert cfg.codex_bin == "codex"
    assert cfg.claude_config_dir is None
    assert cfg.claude_cli_path is None


def test_env_overrides(monkeypatch):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setenv("FORGE_CODEX_BIN", "codex-dev")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/claude")
    monkeypatch.setenv("FORGE_CLAUDE_CLI_PATH", "/bin/claude")
    cfg = RunConfig.from_env()
    assert cfg.codex_bin == "codex-dev"
    assert cfg.claude_config_dir == Path("/tmp/claude")
    assert cfg.claude_cli_path == Path("/bin/claude")
