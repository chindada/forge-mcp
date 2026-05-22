from pathlib import Path

import pytest

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


def test_lineage_top_k_default_4(monkeypatch) -> None:
    """§L8.6 — default is 4.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.delenv("FORGE_LINEAGE_TOP_K", raising=False)
    assert RunConfig.from_env().lineage_top_k == 4


def test_lineage_top_k_env_override(monkeypatch) -> None:
    """§L8.6 — env override is parsed as an integer.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.setenv("FORGE_LINEAGE_TOP_K", "7")
    assert RunConfig.from_env().lineage_top_k == 7


def test_lineage_top_k_zero_allowed(monkeypatch) -> None:
    """§L3.2 — K=0 is the kill switch, allowed.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    monkeypatch.setenv("FORGE_LINEAGE_TOP_K", "0")
    assert RunConfig.from_env().lineage_top_k == 0


def test_lineage_top_k_invalid_rejected(monkeypatch) -> None:
    """§L13.5 — invalid values raise ValueError fail-fast.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.config import RunConfig

    for raw in ("-1", "11", "abc"):
        monkeypatch.setenv("FORGE_LINEAGE_TOP_K", raw)
        with pytest.raises(ValueError):
            RunConfig.from_env()
