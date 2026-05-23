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


@pytest.mark.parametrize("raw", ["0", "10", "1000"])
def test_keep_runs_bounded_int_accepts_range(monkeypatch, raw: str) -> None:
    """FORGE_KEEP_RUNS accepts documented bounded integer values.

    Design: §E requires FORGE_KEEP_RUNS validation parity with lineage top-k.
    Implementation: set only FORGE_KEEP_RUNS and inspect the parsed config value.
    Example: FORGE_KEEP_RUNS='1000' parses to integer 1000.
    """
    monkeypatch.setenv("FORGE_KEEP_RUNS", raw)
    assert RunConfig.from_env().keep_runs == int(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("abc", "FORGE_KEEP_RUNS must be an integer in [0, 1000], got 'abc'"),
        ("-1", "FORGE_KEEP_RUNS must be an integer in [0, 1000], got '-1'"),
    ],
)
def test_keep_runs_bounded_int_rejects_invalid(monkeypatch, raw: str, message: str) -> None:
    """FORGE_KEEP_RUNS rejects non-integer and out-of-range values.

    Design: §E makes invalid retention config fail fast instead of pruning oddly.
    Implementation: assert the exact ValueError text for both parse and range paths.
    Example: FORGE_KEEP_RUNS='abc' raises a bounded-int ValueError.
    """
    monkeypatch.setenv("FORGE_KEEP_RUNS", raw)
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        RunConfig.from_env()


@pytest.mark.parametrize(
    ("name", "valid", "invalid", "low", "high"),
    [
        ("FORGE_KEEP_RUNS", "1000", "1001", 0, 1000),
        ("FORGE_LINEAGE_TOP_K", "10", "11", 0, 10),
    ],
)
def test_bounded_int_envs_share_range_validation(
    monkeypatch, name: str, valid: str, invalid: str, low: int, high: int
) -> None:
    """Bounded integer env vars share inclusive range semantics.

    Design: §E demands symmetric validation for retention and lineage knobs.
    Implementation: verify each env accepts its upper bound and rejects above it.
    Example: FORGE_LINEAGE_TOP_K='11' raises the shared bounded-int message.
    """
    monkeypatch.setenv(name, valid)
    cfg = RunConfig.from_env()
    assert getattr(cfg, "keep_runs" if name == "FORGE_KEEP_RUNS" else "lineage_top_k") == int(valid)

    monkeypatch.setenv(name, invalid)
    with pytest.raises(ValueError) as exc_info:
        RunConfig.from_env()
    assert str(exc_info.value) == f"{name} must be an integer in [{low}, {high}], got {invalid!r}"
