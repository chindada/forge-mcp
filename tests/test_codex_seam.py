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


class _RecordingThread:
    """Fake Codex thread recording the turn() call and yielding no notifications.

    Design: §5 the seam test must observe the sandbox/approval/config args the
        driver passes to thread_start and turn() without the real SDK; this
        double records them and returns an empty stream so _generate_impl runs
        to completion.
    Implementation: turn() stores its kwargs and returns a handle whose stream()
        is an empty async generator; id is a constant.
    Example: thread = _RecordingThread(); await thread.turn(...) sets turn_kwargs.
    """

    def __init__(self) -> None:
        """Initialise with a stable id and empty recorded-kwargs slots.

        Design: §5 the recorder must expose .id (read into last_thread_id) and
            start with no recorded turn kwargs.
        Implementation: set id to a constant and turn_kwargs to None.
        Example: _RecordingThread().id == 'fake-thread'.
        """
        self.id = "fake-thread"
        self.turn_kwargs: dict | None = None

    async def turn(self, _text: object, **kwargs: object) -> object:
        """Record the turn kwargs and return a handle over an empty stream.

        Design: §5 the test asserts approval_mode on the turn; record it.
        Implementation: store kwargs; return a handle whose stream() yields nothing.
        Example: await thread.turn(TextInput(...), approval_mode=x) records x.
        """
        self.turn_kwargs = kwargs

        async def _empty():
            """Yield no notifications.

            Design: §5 an empty turn stream lets _generate_impl finish cleanly.
            Implementation: an async generator with a guarded yield never reached.
            Example: ``async for _ in _empty(): ...`` iterates zero times.
            """
            if False:
                yield None

        return types.SimpleNamespace(stream=_empty)


class _RecordingCodex:
    """Fake AsyncCodex recording thread_start kwargs (§5 seam probe).

    Design: §5 the test must capture the sandbox/approval/cwd/config the driver
        passes to thread_start; this async-context double records them.
    Implementation: __aenter__ returns self; thread_start stores kwargs and
        returns a _RecordingThread; close is a no-op.
    Example: codex = _RecordingCodex(); the driver records start_kwargs on it.
    """

    def __init__(self, *, config: object) -> None:
        """Store the launch config and init empty recorded slots.

        Design: §5 AsyncCodex is constructed with config=cfg; mirror that arg.
        Implementation: keep config; set start_kwargs/thread to None.
        Example: _RecordingCodex(config=cfg).config is cfg.
        """
        self.config = config
        self.start_kwargs: dict | None = None
        self.thread = _RecordingThread()

    async def __aenter__(self) -> _RecordingCodex:
        """Enter the async context returning self.

        Design: §5 the driver does ``async with AsyncCodex(...) as ctx``.
        Implementation: return self.
        Example: ``async with _RecordingCodex(config=c) as ctx: ...``.
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Exit the async context as a no-op.

        Design: §5 nothing to release in the double.
        Implementation: return None.
        Example: context exit is a no-op.
        """
        return None

    async def thread_start(self, **kwargs: object) -> _RecordingThread:
        """Record thread_start kwargs and return the recording thread.

        Design: §5 the test asserts sandbox/approval_mode/config/cwd here.
        Implementation: store kwargs; return the pre-built _RecordingThread.
        Example: await codex.thread_start(sandbox=s) records s.
        """
        self.start_kwargs = kwargs
        return self.thread

    async def close(self) -> None:
        """Close as a no-op.

        Design: §5 aclose() delegates here; the double has nothing to free.
        Implementation: return None.
        Example: await codex.close() completes immediately.
        """
        return None


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


@pytest.mark.skipif(not codex_present, reason="openai-codex not installed")
def test_thread_start_uses_workspace_write_and_network_config(monkeypatch, tmp_path: Path):
    """Design: §5 the driver must start the Codex thread under workspace_write (NOT
        full_access) with approval_mode=deny_all, the cwd None-guard, and the
        inline network config enabling internet for workspace-write.
    Implementation: monkeypatch AsyncCodex with a recording double, drive
        _generate_impl to exhaustion, then assert the recorded thread_start and
        turn kwargs carry Sandbox.workspace_write, ApprovalMode.deny_all,
        config={'sandbox_workspace_write': {'network_access': True}}, and cwd=str(cwd).
    Example: after the empty turn, start_kwargs['sandbox'] is Sandbox.workspace_write.
    """
    import openai_codex
    from openai_codex import ApprovalMode, Sandbox

    cfg = _codex.build_codex_config(codex_bin="codex", cwd=tmp_path)
    captured: dict = {}

    def _factory(*, config):
        """Build and record the recording codex double.

        Design: §5 capture the constructed double so the test can read its
            recorded start/turn kwargs after the driver runs.
        Implementation: construct _RecordingCodex(config=config), store it.
        Example: _factory(config=cfg) returns a _RecordingCodex.
        """
        codex = _RecordingCodex(config=config)
        captured["codex"] = codex
        return codex

    monkeypatch.setattr(openai_codex, "AsyncCodex", _factory)

    driver = _codex.CodexDriver()

    async def _drive() -> None:
        """Consume the generator to exhaustion.

        Design: §5 running the turn populates the recorded kwargs.
        Implementation: async-for over _generate_impl, discarding events.
        Example: ``await _drive()`` leaves captured['codex'] populated.
        """
        async for _ in driver._generate_impl(instructions="hi", config=cfg):
            pass

    asyncio.run(_drive())

    codex = captured["codex"]
    start = codex.start_kwargs
    assert start is not None
    assert start["sandbox"] is Sandbox.workspace_write
    assert start["sandbox"] is not Sandbox.full_access
    assert start["approval_mode"] is ApprovalMode.deny_all
    assert start["config"] == {"sandbox_workspace_write": {"network_access": True}}
    assert start["cwd"] == str(tmp_path)
    assert codex.thread.turn_kwargs is not None
    assert codex.thread.turn_kwargs["approval_mode"] is ApprovalMode.deny_all
    assert codex.thread.turn_kwargs["cwd"] == str(tmp_path)
