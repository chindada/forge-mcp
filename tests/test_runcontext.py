from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from forge_mcp.runcontext import RunContext


def test_field_order_load_bearing():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert [f.name for f in fields(RunContext)] == [
        "run_dir",
        "target_dir",
        "claude_config_dir",
        "claude_cli_path",
        "iteration_n",
    ]


def test_frozen_and_defaults():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    ctx = RunContext(run_dir=Path("/r"), target_dir=Path("/t"))
    assert ctx.claude_config_dir is None
    assert ctx.claude_cli_path is None
    assert ctx.iteration_n is None
    with pytest.raises(FrozenInstanceError):
        attr = "iteration_n"
        setattr(ctx, attr, 1)


def test_runcontext_target_dir_is_optional() -> None:
    """Pin §9.1 planner context without a target_dir.

    Design: the planner runs before the Generator loop and must not receive a
        target directory in its RunContext.
    Implementation: construct the dataclass with target_dir=None explicitly.
    Example: RunContext(run_dir=Path('/tmp/run'), target_dir=None).
    """
    ctx = RunContext(
        run_dir=Path("/tmp/run"),
        target_dir=None,
        claude_config_dir=None,
        claude_cli_path=None,
        iteration_n=None,
    )
    assert ctx.target_dir is None
