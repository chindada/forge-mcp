from __future__ import annotations

from pathlib import Path

from forge_mcp.verifier import run_verification


def test_none_command_passes(tmp_path: Path):
    """Design: §6.5 verify_passed is True when no command is declared.
    Implementation: command=None.
    Example: passed True, timed_out False.
    """
    o = run_verification(None, tmp_path)
    assert o.passed and not o.timed_out and o.exit_code is None


def test_exit_zero_passes(tmp_path: Path):
    """Design: §6.5 passed = (exit_code == 0).
    Implementation: 'true'.
    Example: passed True.
    """
    assert run_verification("true", tmp_path).passed


def test_nonzero_exit_is_not_passed_no_exception(tmp_path: Path):
    """Design: §6.5 check=False -> a failing command is passed=False, not a raise.
    Implementation: 'false'.
    Example: passed False, exit_code != 0.
    """
    o = run_verification("false", tmp_path)
    assert not o.passed and o.exit_code != 0 and not o.timed_out


def test_timeout_sets_timed_out(tmp_path: Path):
    """Design: §6.5 timeout -> passed=False, timed_out=True (TimeoutExpired caught).
    Implementation: 'sleep 5' with timeout 0.2.
    Example: timed_out True.
    """
    o = run_verification("sleep 5", tmp_path, timeout=0.2)
    assert not o.passed and o.timed_out


def test_output_is_bounded(tmp_path: Path):
    """Design: §6.5 output is captured and bounded.
    Implementation: large stdout, small output_limit.
    Example: len(output) <= limit.
    """
    o = run_verification("for i in $(seq 1 1000); do echo line$i; done", tmp_path, output_limit=200)
    assert len(o.output) <= 200
