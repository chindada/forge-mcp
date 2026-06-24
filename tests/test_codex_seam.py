from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from forge_mcp.drivers import _codex

codex_present = importlib.util.find_spec("openai_codex") is not None


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_build_codex_config(tmp_path: Path):
    """Design: §8.2 the launch config class is CodexConfig (NOT AppServerConfig).
    Implementation: build and assert cwd threaded.
    Example: build_codex_config(codex_bin='codex', cwd=tmp).cwd == str(tmp).
    """
    cfg = _codex.build_codex_config(codex_bin="codex", cwd=tmp_path)
    assert str(tmp_path) in str(cfg.cwd)


def test_codex_event_payload_fallback():
    """Design: §8.2 payload uses a model_dump -> dict -> {} fallback.
    Implementation: _dump on an object lacking model_dump returns {} or dict.
    Example: _dump(object()) == {}.
    """
    assert _codex._dump(object()) == {}
    assert _codex._dump({"a": 1}) == {"a": 1}


def test_is_transient_classification():
    """Design: §8.2 transient errors are retryable; timeout/cancel never are.
    Implementation: builtin cases classify without the SDK installed.
    Example: is_transient(ConnectionError()) is True; TimeoutError() is False.
    """
    assert _codex.is_transient(ConnectionError()) is True
    assert _codex.is_transient(BrokenPipeError()) is True
    assert _codex.is_transient(TimeoutError()) is False
    assert _codex.is_transient(asyncio.CancelledError()) is False
    assert _codex.is_transient(OSError()) is False
