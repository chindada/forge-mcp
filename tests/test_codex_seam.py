from __future__ import annotations

import asyncio
import importlib.util
import types
from collections import deque
from pathlib import Path

import pytest

from forge_mcp.drivers import _codex

codex_present = importlib.util.find_spec("openai_codex") is not None


def _fake_codex_with_deque(maxlen: int = 400) -> types.SimpleNamespace:
    """Build a fake Codex object exposing _client._sync._stderr_lines as a deque.

    Design: §8.2 the stderr-tee tests need the SDK's private 3-object attribute
        chain without importing the real SDK; SimpleNamespace mirrors it.
    Implementation: nest SimpleNamespaces holding a bounded deque at the leaf.
    Example: _fake_codex_with_deque()._client._sync._stderr_lines.maxlen == 400.
    """
    sync = types.SimpleNamespace(_stderr_lines=deque(maxlen=maxlen))
    return types.SimpleNamespace(_client=types.SimpleNamespace(_sync=sync))


def test_stderr_tee_installed_and_tees_to_run_log(tmp_path: Path):
    """Design: §8.2 the tee swaps the SDK's bounded deque for a teeing deque that
        mirrors each appended line into run.log while preserving maxlen.
    Implementation: install over a fake deque(maxlen=400), append a line, assert it
        reached run.log and stayed in the buffer.
    Example: appending 'boom' writes 'boom' to run.log and keeps it buffered.
    """
    fake = _fake_codex_with_deque(maxlen=400)
    driver = _codex.CodexDriver()
    log = tmp_path / "run.log"
    driver._try_install_stderr_tee(fake, log)

    swapped = fake._client._sync._stderr_lines
    assert isinstance(swapped, _codex._StderrTeeDeque)
    assert swapped.maxlen == 400  # SDK's bounded behaviour preserved
    swapped.append("boom")
    assert driver._stderr_tee is not None
    driver._stderr_tee.close()
    assert "boom" in log.read_text()
    assert "boom" in swapped  # still buffered in memory


def test_stderr_tee_failsoft_when_chain_missing(tmp_path: Path):
    """Design: §8.2 a missing/wrong attribute chain degrades to no-tee, never raises.
    Implementation: install over a bare object lacking the deque chain; expect a
        warning and no installed tee.
    Example: _try_install_stderr_tee(object(), run.log) warns and installs nothing.
    """
    driver = _codex.CodexDriver()
    with pytest.warns(UserWarning):
        driver._try_install_stderr_tee(object(), tmp_path / "run.log")
    assert driver._stderr_tee is None


def test_stderr_tee_noop_without_run_log():
    """Design: §8.2 with no run_log_path there is no sink, so the deque is untouched.
    Implementation: install with run_log_path=None and assert the original deque
        object is left in place and no tee is recorded.
    Example: _try_install_stderr_tee(fake, None) leaves the SDK deque as-is.
    """
    fake = _fake_codex_with_deque()
    original = fake._client._sync._stderr_lines
    driver = _codex.CodexDriver()
    driver._try_install_stderr_tee(fake, None)
    assert fake._client._sync._stderr_lines is original
    assert driver._stderr_tee is None


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
