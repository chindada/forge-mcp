from __future__ import annotations

import importlib.util

import pytest

claude_present = importlib.util.find_spec("claude_agent_sdk") is not None
codex_present = importlib.util.find_spec("openai_codex") is not None


@pytest.mark.skipif(not claude_present, reason="claude-agent-sdk not installed")
def test_claude_symbols_exist():
    """Design: §8.4 the seam's symbols must exist in the installed SDK.
    Implementation: import and getattr the load-bearing names.
    Example: ClaudeAgentOptions, ClaudeSDKClient, HookMatcher present.
    """
    import inspect

    import claude_agent_sdk as c

    assert hasattr(c, "ClaudeAgentOptions")
    assert hasattr(c, "ClaudeSDKClient")
    assert hasattr(c, "HookMatcher")
    sig = inspect.signature(c.ClaudeSDKClient.__init__)
    assert "options" in sig.parameters


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_codex_symbols_exist():
    """Design: §8.4 re-introspect the installed Codex SDK symbols.
    Implementation: assert the 0.1.0b2-line shapes (or installed equivalents).
    Example: Sandbox.full_access, ApprovalMode.deny_all present.
    """
    import inspect

    import openai_codex as o
    from openai_codex import ApprovalMode, Sandbox

    assert hasattr(Sandbox, "workspace_write")
    assert hasattr(Sandbox, "full_access")  # still a member; driver no longer uses it
    assert hasattr(ApprovalMode, "deny_all")
    params = inspect.signature(o.AsyncCodex.thread_start).parameters
    assert "sandbox" in params and "approval_mode" in params
