"""C10 — fast SDK-shape contract pins (PR CI; no auth, no subprocess).

These assert the exact SDK shapes forge depends on, so an SDK bump that
renames or moves any of them fails CI immediately instead of only in the
slow e2e. Every test is importorskip-guarded.
"""

from __future__ import annotations

import inspect

import pytest


def test_claude_client_has_receive_response() -> None:
    """Pin: ClaudeSDKClient exposes receive_response (A1 idiom).

    Design: A1 consumes one turn via receive_response(); a rename breaks the
        Claude seam, so CI must catch it.
    Implementation: importorskip the SDK and assert the attribute exists.
    Example: pytest tests/test_sdk_contract.py -k receive_response -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import ClaudeSDKClient

    assert hasattr(ClaudeSDKClient, "receive_response")


def test_build_options_wraps_bare_output_format_as_envelope() -> None:
    """Pin: build_options envelopes a bare schema as json_schema (A3).

    Design: the subprocess transport emits --json-schema only for the
        envelope shape; a bare schema is silently dropped.
    Implementation: pass a bare object schema and read back .output_format.
    Example: pytest tests/test_sdk_contract.py -k envelope -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import build_options

    bare = {"type": "object", "properties": {"x": {"type": "string"}}}
    opts = build_options(output_format=bare)
    assert opts.output_format == {"type": "json_schema", "schema": bare}


def test_build_options_passes_enveloped_output_format_through() -> None:
    """Pin: build_options is idempotent for an already-enveloped schema (A3).

    Design: callers may pass either shape; double-wrapping would corrupt it.
    Implementation: pass an enveloped value and assert identity.
    Example: pytest tests/test_sdk_contract.py -k passes_enveloped -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import build_options

    enveloped = {"type": "json_schema", "schema": {"type": "object"}}
    opts = build_options(output_format=enveloped)
    assert opts.output_format == enveloped


def test_claude_agent_options_accepts_stderr_field() -> None:
    """Pin: ClaudeAgentOptions has a stderr field (B8 tee).

    Design: B8 wires run.log teeing through ClaudeAgentOptions(stderr=...).
    Implementation: assert 'stderr' is a declared dataclass field.
    Example: pytest tests/test_sdk_contract.py -k stderr_field -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from dataclasses import fields

    from claude_agent_sdk import ClaudeAgentOptions

    assert "stderr" in {f.name for f in fields(ClaudeAgentOptions)}


def test_result_message_has_structured_output_field() -> None:
    """Pin: ResultMessage declares structured_output (A3 drain target).

    Design: structured output lands on ResultMessage.structured_output.
    Implementation: assert the field name is declared on the dataclass.
    Example: pytest tests/test_sdk_contract.py -k structured_output -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from dataclasses import fields

    from claude_agent_sdk import ResultMessage

    assert "structured_output" in {f.name for f in fields(ResultMessage)}


def test_text_block_constructs_and_tool_use_block_shape() -> None:
    """Pin: TextBlock(text=...) constructs; ToolUseBlock has id/name/input (A3).

    Design: drain_text reads block.text; recovery reads ToolUseBlock fields.
    Implementation: construct a TextBlock and inspect ToolUseBlock fields.
    Example: pytest tests/test_sdk_contract.py -k text_block -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from dataclasses import fields

    from claude_agent_sdk import TextBlock, ToolUseBlock

    assert TextBlock(text="hi").text == "hi"
    assert {"id", "name", "input"} <= {f.name for f in fields(ToolUseBlock)}


def test_claude_transport_symbols_importable() -> None:
    """Pin: CLIConnectionError and HookMatcher import (A1/H10 deps).

    Design: is_transient_error and git_deny_hooks lazily import these.
    Implementation: a bare import that raises ImportError on drift.
    Example: pytest tests/test_sdk_contract.py -k transport_symbols -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import CLIConnectionError, HookMatcher  # noqa: F401


def test_app_server_config_codex_bin_roundtrips() -> None:
    """Pin: CodexConfig(codex_bin=...) round-trips .codex_bin/.cwd (A5).

    Design: the field is codex_bin, not executable; a rename breaks A5.
        openai-codex 0.132 renamed AppServerConfig → CodexConfig with the same
        codex_bin/cwd kwargs, which the seam helper now builds.
    Implementation: build via the seam helper and read .codex_bin/.cwd back.
    Example: pytest tests/test_sdk_contract.py -k codex_bin -v.
    """
    pytest.importorskip("openai_codex")
    from pathlib import Path

    from forge_mcp.drivers._codex import build_app_server_config

    cfg = build_app_server_config(codex_bin="codex", cwd=Path("/repo"))
    assert cfg.codex_bin == "codex"
    assert cfg.cwd == "/repo"


def test_async_codex_accepts_single_config_param() -> None:
    """Pin: AsyncCodex.__init__ accepts a single 'config' param (A6).

    Design: A6 constructs AsyncCodex(config=server_config).
    Implementation: inspect the signature for a 'config' parameter.
    Example: pytest tests/test_sdk_contract.py -k single_config -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import AsyncCodex

    assert "config" in inspect.signature(AsyncCodex.__init__).parameters


def test_async_thread_turn_signature_has_sandbox_approval_cwd() -> None:
    """Pin: AsyncThread.turn signature includes sandbox/approval_mode/cwd (A6).

    Design: openai-codex 0.132 retired the per-turn sandbox_policy union; the
        seam now calls thread.turn(input, cwd=..., approval_mode=...) with the
        coarse sandbox preset, so turn must still expose sandbox/approval_mode/cwd.
    Implementation: importorskip AsyncThread and inspect turn's parameters.
    Example: pytest tests/test_sdk_contract.py -k turn_signature -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import AsyncThread

    params = inspect.signature(AsyncThread.turn).parameters
    assert {"sandbox", "approval_mode", "cwd"} <= set(params)


def test_text_input_is_constructable() -> None:
    """Pin: TextInput(text=...) constructs as a RunInput (A6).

    Design: A6 wraps instructions as TextInput(instructions).
    Implementation: construct and read .text.
    Example: pytest tests/test_sdk_contract.py -k text_input -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import TextInput

    assert TextInput(text="go").text == "go"


def test_thread_start_carries_sandbox_workspace_write_overrides() -> None:
    """Pin: thread_start exposes sandbox+config and the §H7 override keys exist (A6).

    Design: openai-codex 0.132 moved writable_roots/network_access out of the
        per-turn SandboxPolicy union into thread_start(config=...) overrides;
        sandbox_config_for builds that dict. A drift in the param names or the
        sandbox_workspace_write field names would silently drop the §H7 boundary,
        so pin all three: the Sandbox preset, thread_start's sandbox/config
        params, and SandboxWorkspaceWrite's writable_roots/network_access fields.
    Implementation: inspect AsyncCodex.thread_start params and the
        SandboxWorkspaceWrite model fields the override dict targets.
    Example: pytest tests/test_sdk_contract.py -k sandbox_workspace_write -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import AsyncCodex, Sandbox
    from openai_codex.generated.v2_all import SandboxWorkspaceWrite

    assert Sandbox.workspace_write is not None
    params = set(inspect.signature(AsyncCodex.thread_start).parameters)
    assert {"sandbox", "config"} <= params
    fields = set(SandboxWorkspaceWrite.model_fields)
    assert {"writable_roots", "network_access"} <= fields


def test_approval_mode_deny_all_exists() -> None:
    """Pin: ApprovalMode.deny_all exists (A6 never_approval_mode).

    Design: the generator runs non-interactively under deny_all.
    Implementation: attribute access raising AttributeError on drift.
    Example: pytest tests/test_sdk_contract.py -k deny_all -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import ApprovalMode

    assert ApprovalMode.deny_all is not None


def test_codex_transport_symbols_importable() -> None:
    """Pin: TransportClosedError and is_retryable_error import (is_transient_error dep).

    Design: the Codex seam's is_transient_error lazily imports these.
    Implementation: a bare import that raises ImportError on drift.
    Example: pytest tests/test_sdk_contract.py -k codex_transport -v.
    """
    pytest.importorskip("openai_codex")
    from openai_codex import TransportClosedError, is_retryable_error  # noqa: F401
