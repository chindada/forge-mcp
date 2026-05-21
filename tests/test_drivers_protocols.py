"""§5.2 concrete drivers must not import forbidden modules."""

from __future__ import annotations

import importlib
import inspect


def _module_source(name: str) -> str:
    """Return source for an imported module.

    Design: forbidden-edge tests inspect concrete driver source text.
    Implementation: import the module and call inspect.getsource.
    Example: _module_source('forge_mcp.drivers.planner').
    """
    return inspect.getsource(importlib.import_module(name))


def test_drivers_never_import_sdks_directly() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    for name in (
        "forge_mcp.drivers.planner",
        "forge_mcp.drivers.generator",
        "forge_mcp.drivers.evaluator",
    ):
        src = _module_source(name)
        assert "import claude_agent_sdk" not in src
        assert "from claude_agent_sdk" not in src
        assert "import openai_codex" not in src
        assert "from openai_codex" not in src


def test_drivers_never_import_doctor_or_orchestrator() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    for name in (
        "forge_mcp.drivers.planner",
        "forge_mcp.drivers.generator",
        "forge_mcp.drivers.evaluator",
    ):
        src = _module_source(name)
        assert "forge_mcp.doctor" not in src
        assert "forge_mcp.orchestrator" not in src


def test_evaluator_has_no_probe_removed_need() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.drivers.evaluator import EvaluatorDriver

    assert not hasattr(EvaluatorDriver, "probe_removed_removed_tool_surface_need")


def test_generator_implement_has_no_mcp_servers_param() -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.drivers.generator import GeneratorDriver

    assert "mcp_servers" not in inspect.signature(GeneratorDriver.implement).parameters
