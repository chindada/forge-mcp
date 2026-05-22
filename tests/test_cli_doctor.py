"""§14 CLI doctor and serve command smoke tests."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from forge_mcp import cli


def test_doctor_prints_check_lines(monkeypatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fake_check_codex(config):
        """Return a successful fake Codex check.

        Design: CLI tests should not require a real Codex install.
        Implementation: ignore config and return the shared check tuple shape.
        Example: await fake_check_codex(config) returns ('codex', 'OK', 'fake').
        """
        return ("codex", "OK", "fake")

    monkeypatch.setattr(cli.doc, "check_codex", fake_check_codex)
    monkeypatch.setattr(cli.doc, "check_claude_cli", lambda config: ("claude", "OK", "fake"))
    monkeypatch.setattr(cli.doc, "check_claude_auth", lambda: ("claude_auth", "OK", "fake"))
    monkeypatch.setattr(cli, "probe_required_skills", lambda **_kwargs: _ok_coro(None))
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "[OK] codex" in result.output


def test_serve_help_works() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    result = CliRunner().invoke(cli.app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "Boot the stdio" in result.output


def _ok_coro(value):
    """Wrap a tuple in a coroutine for monkeypatching async checks.

    Design: tests stub async helpers without an extra dependency.
    Implementation: define a local async function that returns the value.
    Example: monkeypatch.setattr(..., lambda c: _ok_coro(('codex','OK','x'))).
    """

    async def _inner(*_a, **_kw):
        """Inner coroutine yielding the captured value.

        Design: shape matches `async def f(config) -> tuple[...]`.
        Implementation: trivial async return.
        Example: await _inner(config).
        """
        return value

    return _inner()


def test_doctor_includes_skills_and_harness(monkeypatch, tmp_path) -> None:
    """Pin a forge-mcp behavior.

    Design: §14 — doctor MUST run the skill probe and a .harness-writable
        check alongside the existing claude/codex/auth/disk probes (single
        source of truth with preflight).
    Implementation: stub probe_required_skills and check_harness_writable to
        succeed, invoke `forge doctor`, and assert their output lines appear.
    Example: pytest runs this test in the non-slow suite.
    """

    async def fake_probe(*, runner, claude_cli_path):
        """Return without raising — simulates all required skills present.

        Design: doctor tests must not require a live Claude session.
        Implementation: no-op coroutine matching probe_required_skills.
        Example: await fake_probe(runner=None, claude_cli_path=None).
        """
        return None

    monkeypatch.setattr(cli, "probe_required_skills", fake_probe, raising=False)
    monkeypatch.setattr(cli.doc, "check_codex", lambda config: _ok_coro(("codex", "OK", "fake")))
    monkeypatch.setattr(cli.doc, "check_claude_cli", lambda config: ("claude", "OK", "fake"))
    monkeypatch.setattr(cli.doc, "check_claude_auth", lambda: ("claude_auth", "OK", "fake"))
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "[OK] skills" in result.output
    assert "[OK] .harness" in result.output


def test_doctor_fails_when_skill_missing(monkeypatch, tmp_path) -> None:
    """Pin a forge-mcp behavior.

    Design: §14 / Rule 8 — a missing required skill must surface as FAIL
        and a non-zero exit code from `forge doctor`.
    Implementation: stub probe_required_skills to raise SkillMissingError,
        assert exit_code == 1 and the FAIL line names the missing skill.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.skills import SkillMissingError

    async def fake_probe(*, runner, claude_cli_path):
        """Raise as if the required skill list were not available.

        Design: simulate the SDK returning a structured 'available' list
            missing the required ids.
        Implementation: raise SkillMissingError with a known id.
        Example: await fake_probe(runner=None, claude_cli_path=None) raises.
        """
        raise SkillMissingError(["superpowers:writing-plans"])

    monkeypatch.setattr(cli, "probe_required_skills", fake_probe, raising=False)
    monkeypatch.setattr(cli.doc, "check_codex", lambda config: _ok_coro(("codex", "OK", "fake")))
    monkeypatch.setattr(cli.doc, "check_claude_cli", lambda config: ("claude", "OK", "fake"))
    monkeypatch.setattr(cli.doc, "check_claude_auth", lambda: ("claude_auth", "OK", "fake"))
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] skills" in result.output
    assert "superpowers:writing-plans" in result.output


def test_version_prints_package_version() -> None:
    """Pin a forge-mcp behavior.

    Design: §5.1 — `forge version` is the third documented Typer command
        and must print the installed package version.
    Implementation: invoke `forge version` and assert the output contains
        the importlib.metadata version for 'forge-mcp'.
    Example: pytest runs this test in the non-slow suite.
    """
    import importlib.metadata

    expected = importlib.metadata.version("forge-mcp")
    result = CliRunner().invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert expected in result.output


def test_doctor_bad_lineage_top_k_emits_fail_line(monkeypatch, capsys) -> None:
    """§L14 step 5 — bad FORGE_LINEAGE_TOP_K emits a FAIL env_config line.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    monkeypatch.setenv("FORGE_LINEAGE_TOP_K", "abc")
    from forge_mcp.cli import doctor as doctor_cmd

    with pytest.raises(SystemExit) as exc_info:
        doctor_cmd()
    combined = capsys.readouterr().out + capsys.readouterr().err
    assert exc_info.value.code == 1
    assert "FAIL" in combined and "env_config" in combined


def test_server_import_bad_lineage_top_k_emits_stderr_diagnostic(monkeypatch, capsys) -> None:
    """§L14 step 5 — server.py import with bad env writes stderr hint.

    Design: cross-run learning and adjacent orchestration behavior is
        load-bearing, so tests pin the user-visible contract.
    Implementation: call focused production code or fixtures and assert the
        observable artifact, model, prompt, or configuration result.
    Example: pytest runs this test in the non-slow suite.
    """
    import importlib
    import sys

    monkeypatch.setenv("FORGE_LINEAGE_TOP_K", "abc")
    sys.modules.pop("forge_mcp.server", None)
    with pytest.raises(ValueError):
        importlib.import_module("forge_mcp.server")
    captured = capsys.readouterr()
    assert "Invalid forge-mcp config" in captured.err
    assert "forge doctor" in captured.err
