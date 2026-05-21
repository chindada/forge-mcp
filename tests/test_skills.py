"""§6.4 step 7 required-skills live probe via Claude SDK seam."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from forge_mcp import skills
from forge_mcp.skills import REQUIRED_SKILLS, SkillMissingError


class _Options:
    """Fake ClaudeAgentOptions accepting arbitrary kwargs.

    Design: skills tests must not require an installed Claude SDK.
    Implementation: store kwargs for possible inspection.
    Example: _Options(output_format={}).
    """

    def __init__(self, **kwargs) -> None:
        """Store provided option kwargs.

        Design: build_options imports this fake class during tests.
        Implementation: assign kwargs to self.kwargs.
        Example: _Options(a=1).kwargs['a'] == 1.
        """
        self.kwargs = kwargs


class _FakeRunner:
    """Stand-in ClaudeRunner returning canned structured response."""

    def __init__(self, payload: dict) -> None:
        """Store the payload returned by run.

        Design: probe tests vary available skills without SDK calls.
        Implementation: assign payload to an instance attribute.
        Example: _FakeRunner({'available': []}).payload.
        """
        self.payload = payload

    async def run(self, *args, **kwargs):
        """Return a StructuredResult containing the canned payload.

        Design: probe_required_skills reads result.structured.
        Implementation: import production StructuredResult and instantiate it.
        Example: await runner.run().structured.
        """
        from forge_mcp.drivers._claude import StructuredResult

        return StructuredResult(structured=self.payload, text="")


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake claude_agent_sdk module into sys.modules.

    Design: build_options imports ClaudeAgentOptions lazily.
    Implementation: set a SimpleNamespace-like module with _Options class.
    Example: _install_fake_sdk(monkeypatch).
    """
    module: Any = types.ModuleType("claude_agent_sdk")
    module.ClaudeAgentOptions = _Options
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)


async def test_missing_skill_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    _install_fake_sdk(monkeypatch)
    with pytest.raises(SkillMissingError):
        await skills.probe_required_skills(
            runner=_FakeRunner({"available": []}), claude_cli_path=None
        )


async def test_all_present_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    _install_fake_sdk(monkeypatch)
    await skills.probe_required_skills(
        runner=_FakeRunner({"available": list(REQUIRED_SKILLS)}), claude_cli_path=None
    )
