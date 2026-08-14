from __future__ import annotations

import asyncio

from forge_mcp.drivers import _claude
from forge_mcp.schemas import envelope


def test_envelope_is_idempotent():
    """Design: §8.1 output_format must be {type:json_schema, schema:<bare>}, wrapped once.
    Implementation: wrapping an already-enveloped schema is a no-op.
    Example: envelope(envelope(s)) == envelope(s).
    """
    bare = {"type": "object", "properties": {}}
    once = envelope(bare)
    assert once == {"type": "json_schema", "schema": bare}
    assert envelope(once) == once


def test_build_options_disables_mcp_inheritance_without_hiding_skills():
    """MCP isolation must remain independent from settings-backed skill discovery.

    Design: every forge-created Claude session must reject inherited MCP servers
        while retaining the user, project, and local sources that advertise skills.
    Implementation: construct options only; assert the empty strict MCP boundary
        together with the unchanged skills and setting source values.
    Example: a forge skill-probe session can see `superpowers:writing-plans` but
        cannot load a `forge` MCP declaration from any settings source.
    """
    options = _claude.build_options(system="list skills only")

    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True
    assert options.skills == "all"
    assert options.setting_sources == ["user", "project", "local"]


def test_git_deny_hook_blocks_mutation_passes_safe():
    """Design: §8.1/§9 the PreToolUse hook denies git-mutating Bash, passes the rest.
    Implementation: call the deny callback with a git commit and a safe command.
    Example: commit -> deny envelope; status -> {}.
    """
    hooks = _claude.git_deny_hooks()
    cb = hooks["PreToolUse"][0].hooks[0]
    deny = asyncio.run(
        cb({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, "id", None)
    )
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = asyncio.run(cb({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}, "id", None))
    assert ok == {}
