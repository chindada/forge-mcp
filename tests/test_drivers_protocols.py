"""§5.2 concrete drivers must not import forbidden modules."""

from __future__ import annotations

import importlib
import inspect
from typing import Any
from unittest.mock import MagicMock


class _FakeClaudeSDKClient:
    """ClaudeSDKClient stand-in that emits scripted SystemMessages on receive (§C2.2 tests).

    Design: ClaudeRunnerImpl.run_with_messages consumes a ClaudeSDKClient as an
        async context manager and iterates receive_messages(); a tiny scripted
        client lets the lifecycle tests pin _absorb_system_message and reset
        timing without a live SDK.
    Implementation: __aenter__ returns self; query is a no-op; receive_messages
        is an async generator yielding the messages this fake was constructed
        with.
    Example: with FakeClaudeSDKClient(messages=[{'subtype': 'init', ...}]): ...
    """

    def __init__(self, messages: list[Any]) -> None:
        """Store the canned message script.

        Design: each test instance owns its own message sequence.
        Implementation: assign the list verbatim.
        Example: _FakeClaudeSDKClient([{'subtype': 'init', 'session_id': 's'}]).
        """
        self._messages = messages

    async def __aenter__(self) -> _FakeClaudeSDKClient:
        """Enter the SDK async context.

        Design: ClaudeSDKClient is used as `async with` in production.
        Implementation: return self with no setup.
        Example: async with _FakeClaudeSDKClient([]) as client: ...
        """
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit the SDK async context.

        Design: no teardown is needed for the fake.
        Implementation: return None.
        Example: handled by `async with`.
        """
        return None

    async def query(self, *, prompt: str) -> None:
        """Pretend to issue a query.

        Design: ClaudeRunnerImpl.run_with_messages calls client.query.
        Implementation: no-op.
        Example: await client.query(prompt='hi').
        """
        return None

    async def receive_messages(self):
        """Yield the canned message script.

        Design: ClaudeRunnerImpl iterates this generator.
        Implementation: async-yield each stored message.
        Example: async for m in client.receive_messages(): ...
        """
        for msg in self._messages:
            yield msg

    async def interrupt(self) -> None:
        """No-op interrupt to satisfy ClaudeRunnerImpl.interrupt() path.

        Design: the runner calls interrupt() best-effort on aclose escalation.
        Implementation: nothing to do for the fake.
        Example: await client.interrupt().
        """
        return None


def _patch_claude_sdk(monkeypatch, messages_per_call: list[list[Any]]) -> list[int]:
    """Patch claude_agent_sdk.ClaudeSDKClient with a sequence of fake clients.

    Design: lifecycle tests need different scripts on consecutive runs; the
        helper hands each ClaudeSDKClient(...) call a fresh _FakeClaudeSDKClient
        from the configured sequence and records the call count.
    Implementation: build a closure-mutable counter; install the patch with
        monkeypatch so cleanup is automatic; return the counter list.
    Example: counter = _patch_claude_sdk(mp, [[init1], [init2]]).
    """
    import claude_agent_sdk

    counter: list[int] = [0]

    def factory(*args: Any, **kwargs: Any) -> _FakeClaudeSDKClient:
        """Return the next scripted fake client.

        Design: each .run_with_messages call instantiates ClaudeSDKClient once.
        Implementation: read the next script slice by index and increment.
        Example: factory(options=...).
        """
        idx = counter[0]
        counter[0] = idx + 1
        return _FakeClaudeSDKClient(messages_per_call[idx])

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", factory)
    return counter


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


async def test_claude_runner_last_session_id_none_before_first_call(monkeypatch) -> None:
    """Pin §C2.2 — ClaudeRunnerImpl.last_session_id is None until the first run.

    Design: §C2.2 capture-only invariant says the attribute is None pre-call;
        no SDK interaction has occurred yet.
    Implementation: instantiate the runner and read the attribute without
        touching any SDK seam.
    Example: pytest tests/test_drivers_protocols.py -k none_before_first_call -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    runner = ClaudeRunnerImpl()
    assert runner.last_session_id is None


async def test_claude_runner_last_session_id_populated_then_preserved_after_aclose(
    monkeypatch,
) -> None:
    """Pin §C2.2 lifecycle — populated by init SystemMessage, preserved after aclose.

    Design: §C2.2 says the attribute is set when init arrives and MUST survive
        aclose() so the orchestrator can read it immediately after the call.
    Implementation: script a single SDK message dict with subtype='init' and
        session_id='sess_A'; run one call; assert the attribute; await aclose();
        assert the attribute persists.
    Example: pytest tests/test_drivers_protocols.py -k preserved_after_aclose -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk(monkeypatch, [[{"subtype": "init", "session_id": "sess_A"}]])
    runner = ClaudeRunnerImpl()
    await runner.run(prompt="hi", options=MagicMock(), system="sys")
    assert runner.last_session_id == "sess_A"
    await runner.aclose()
    assert runner.last_session_id == "sess_A"


async def test_claude_runner_last_session_id_resets_at_start_of_next_call(monkeypatch) -> None:
    """Pin §C2.2 reset — the attribute is None at the start of the next call, then populated.

    Design: §C2.2 — each call creates a fresh SDK session; the runner resets
        to None before opening the stream so any stale value cannot leak
        across calls. The second call then populates a new id.
    Implementation: script two calls with distinct init session_ids; after the
        first, assert sess_A; before draining the second's messages the
        attribute would be None mid-stream (not observable from outside), but
        after the second call returns it must be sess_B.
    Example: pytest tests/test_drivers_protocols.py -k resets_at_start -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk(
        monkeypatch,
        [
            [{"subtype": "init", "session_id": "sess_A"}],
            [{"subtype": "init", "session_id": "sess_B"}],
        ],
    )
    runner = ClaudeRunnerImpl()
    await runner.run(prompt="one", options=MagicMock(), system="sys")
    assert runner.last_session_id == "sess_A"
    await runner.run(prompt="two", options=MagicMock(), system="sys")
    assert runner.last_session_id == "sess_B"


async def test_claude_runner_last_session_id_fail_soft_when_init_missing_field(
    monkeypatch,
) -> None:
    """Pin §C11 risk 4 — missing session_id on the init message leaves last_session_id None.

    Design: §C11 risk 4 mandates fail-soft behavior; a malformed init must
        NOT raise during the stream or terminate the run.
    Implementation: script an init message that omits session_id; assert the
        call returns normally and last_session_id is still None afterward.
    Example: pytest tests/test_drivers_protocols.py -k fail_soft_when_init_missing_field -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk(monkeypatch, [[{"subtype": "init"}]])
    runner = ClaudeRunnerImpl()
    await runner.run(prompt="hi", options=MagicMock(), system="sys")
    assert runner.last_session_id is None


async def test_codex_runner_last_thread_id_lifecycle(monkeypatch) -> None:
    """Pin §C2.2 Codex lifecycle — None → populated → preserved across aclose → overwritten.

    Design: §C2.2 makes CodexRunnerImpl retain the AsyncThread on self._thread
        and expose self._thread.id as last_thread_id; aclose() only clears
        _session so the captured id persists for forensic readback; a second
        turn overwrites _thread, so last_thread_id reflects the latest call.
    Implementation: patch openai_codex.AsyncCodex with a fake whose
        threads.create() returns an object whose .id rotates; assert lifecycle
        states across two turns and one aclose.
    Example: pytest tests/test_drivers_protocols.py::test_codex_runner_last_thread_id_lifecycle -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    class _FakeThread:
        """AsyncThread double whose .id is configurable per instance.

        Design: tests need deterministic ids without opening a real Codex thread.
        Implementation: expose .id and a runs facade with create().
        Example: _FakeThread('thr_1').id == 'thr_1'.
        """

        def __init__(self, id_: str | None) -> None:
            """Store the id to expose.

            Design: id may be None to exercise the fail-soft branch.
            Implementation: assign the attribute verbatim.
            Example: _FakeThread('thr_1').id == 'thr_1'.
            """
            self.id = id_

        class _Runs:
            """Stub runs facade exposing create().

            Design: CodexRunnerImpl calls thread.runs.create(...).
            Implementation: return a closeable session double.
            Example: runs = _FakeThread._Runs().
            """

            async def create(self, **kwargs: Any) -> Any:
                """Return a no-op session object.

                Design: CodexRunnerImpl stores the runs.create() result on _session.
                Implementation: return a MagicMock with an async close().
                Example: await thread.runs.create(developer_instructions='x').
                """
                session = MagicMock()

                async def _aclose() -> None:
                    """Awaitable close for session.close().

                    Design: aclose() awaits self._session.close().
                    Implementation: return None.
                    Example: await session.close().
                    """
                    return None

                session.close = _aclose
                return session

        @property
        def runs(self) -> _FakeThread._Runs:
            """Return the per-thread runs facade.

            Design: production code does `thread.runs.create(...)`.
            Implementation: instantiate per access.
            Example: thread.runs.create(...).
            """
            return self._Runs()

    thread_ids: list[str | None] = ["thr_1", "thr_2"]

    class _FakeAsyncCodex:
        """AsyncCodex stand-in returning fresh threads with rotated ids.

        Design: CodexRunnerImpl only needs .threads.create for these tests.
        Implementation: expose a nested threads facade.
        Example: _FakeAsyncCodex().threads.
        """

        def __init__(self, **kwargs: Any) -> None:
            """Accept the same constructor kwargs as the real SDK.

            Design: keep test invocation parity with CodexRunnerImpl.turn().
            Implementation: discard kwargs.
            Example: _FakeAsyncCodex(app_server_config=None, ...).
            """
            self.threads = self._Threads()

        class _Threads:
            """Stub threads facade exposing create().

            Design: CodexRunnerImpl calls codex.threads.create().
            Implementation: pop the next configured id.
            Example: threads = _FakeAsyncCodex._Threads().
            """

            async def create(self) -> _FakeThread:
                """Return the next pre-configured thread.

                Design: CodexRunnerImpl.turn() awaits codex.threads.create().
                Implementation: pop the next id and wrap in _FakeThread.
                Example: thread = await codex.threads.create().
                """
                return _FakeThread(thread_ids.pop(0))

    monkeypatch.setattr(openai_codex, "AsyncCodex", _FakeAsyncCodex)

    runner = CodexRunnerImpl()
    assert runner.last_thread_id is None

    await runner.turn(
        instructions="go",
        server_config=None,
        sandbox_policy=None,
        approval_mode=None,
        env=None,
    )
    assert runner.last_thread_id == "thr_1"

    await runner.aclose()
    assert runner.last_thread_id == "thr_1"

    await runner.turn(
        instructions="go again",
        server_config=None,
        sandbox_policy=None,
        approval_mode=None,
        env=None,
    )
    assert runner.last_thread_id == "thr_2"


async def test_codex_runner_last_thread_id_fail_soft_when_id_missing(monkeypatch) -> None:
    """Pin §C11 risk 5 — last_thread_id is None when AsyncThread omits .id.

    Design: §C11 risk 5 anticipates SDK shape drift where the returned thread
        might not expose a string .id; the runner must read None rather than
        raise.
    Implementation: patch AsyncCodex so threads.create() returns an object
        whose .id is None; assert last_thread_id is None after the turn.
    Example: pytest tests/test_drivers_protocols.py -k fail_soft_when_id_missing -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    class _FakeThreadNoId:
        """Thread whose .id is None to exercise fail-soft.

        Design: SDK shape drift may omit a useful thread id.
        Implementation: expose id=None and a runs facade.
        Example: _FakeThreadNoId().id is None.
        """

        id = None

        class _Runs:
            """Stub runs facade exposing create().

            Design: CodexRunnerImpl calls thread.runs.create(...).
            Implementation: return a closeable session double.
            Example: runs = _FakeThreadNoId._Runs().
            """

            async def create(self, **kwargs: Any) -> Any:
                """Return an awaitable-close session double.

                Design: parity with the lifecycle-test stub.
                Implementation: return a MagicMock with async close.
                Example: await thread.runs.create(developer_instructions='x').
                """
                session = MagicMock()

                async def _aclose() -> None:
                    """Awaitable close for session.close().

                    Design: aclose() awaits self._session.close().
                    Implementation: return None.
                    Example: await session.close().
                    """
                    return None

                session.close = _aclose
                return session

        @property
        def runs(self) -> _FakeThreadNoId._Runs:
            """Return the per-thread runs facade.

            Design: parity with _FakeThread.runs.
            Implementation: instantiate per access.
            Example: thread.runs.create(...).
            """
            return self._Runs()

    class _FakeAsyncCodex:
        """AsyncCodex stand-in whose threads always lack a real id.

        Design: CodexRunnerImpl only needs .threads.create for this test.
        Implementation: expose a nested threads facade.
        Example: _FakeAsyncCodex().threads.
        """

        def __init__(self, **kwargs: Any) -> None:
            """Accept SDK constructor kwargs (ignored).

            Design: parity with the real SDK call site in turn().
            Implementation: assign the threads stub.
            Example: _FakeAsyncCodex().
            """
            self.threads = self._Threads()

        class _Threads:
            """Stub threads facade exposing create().

            Design: CodexRunnerImpl calls codex.threads.create().
            Implementation: return a no-id thread.
            Example: threads = _FakeAsyncCodex._Threads().
            """

            async def create(self) -> _FakeThreadNoId:
                """Return a thread with id=None.

                Design: drives the fail-soft branch.
                Implementation: instantiate _FakeThreadNoId.
                Example: thread = await codex.threads.create().
                """
                return _FakeThreadNoId()

    monkeypatch.setattr(openai_codex, "AsyncCodex", _FakeAsyncCodex)

    runner = CodexRunnerImpl()
    await runner.turn(
        instructions="go",
        server_config=None,
        sandbox_policy=None,
        approval_mode=None,
        env=None,
    )
    assert runner.last_thread_id is None
