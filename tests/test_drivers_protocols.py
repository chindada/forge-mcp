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


def test_sandbox_policy_network_access_flip(tmp_path) -> None:
    """Pin §H7 network_access flips the Codex SandboxPolicy network toggle.

    Design: §H7 makes network access a caller knob; False must disable network
        in the produced policy while the default remains enabled.
    Implementation: build policies with network_access False and True and read
        the policy's network attribute (network_access or networkAccess).
    Example: sandbox_policy_for(..., network_access=False) disables network.
    """
    import pytest as _pytest

    codex_mod = _pytest.importorskip("openai_codex")
    if not hasattr(codex_mod, "SandboxPolicy"):
        _pytest.skip("openai_codex.SandboxPolicy is unavailable")
    from forge_mcp.drivers._codex import sandbox_policy_for

    def _net(policy) -> bool:
        """Read the network-access flag under either SDK attribute name.

        Design: SDK may expose snake_case or camelCase; the pin tolerates both.
        Implementation: prefer network_access, fall back to networkAccess.
        Example: _net(policy) is False for a network-disabled policy.
        """
        return bool(getattr(policy, "network_access", getattr(policy, "networkAccess", None)))

    off = sandbox_policy_for(
        target_dir=tmp_path, iteration_dir=tmp_path / "iter", network_access=False
    )
    on = sandbox_policy_for(
        target_dir=tmp_path, iteration_dir=tmp_path / "iter", network_access=True
    )
    assert _net(off) is False
    assert _net(on) is True
