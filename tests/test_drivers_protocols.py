"""§5.2 concrete drivers must not import forbidden modules."""

from __future__ import annotations

import importlib
import inspect
from typing import Any
from unittest.mock import MagicMock


def _make_result_message(structured: dict | None):
    """Build a real ResultMessage with structured_output for tests.

    Design: C9 fakes use installed SDK message types so constructor drift fails
        in fast tests instead of slow e2e.
    Implementation: fill required ResultMessage fields with benign values.
    Example: _make_result_message({'ok': True}).structured_output.
    """
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        structured_output=structured,
    )


def _claude_turn_script(
    *,
    text: str | None,
    structured: dict | None,
    init_session_id: str | None = None,
    include_init: bool = True,
) -> list[Any]:
    """Build a real-SDK-typed message list for one receive_response() turn.

    Design: C9 fakes must use real SDK message/block types; A1 includes init
        SystemMessage in the response stream and ends with ResultMessage.
    Implementation: assemble optional SystemMessage, AssistantMessage/TextBlock,
        and ResultMessage carrying structured_output.
    Example: _claude_turn_script(text='hi', structured={'available': []}).
    """
    from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock

    messages: list[Any] = []
    if include_init:
        messages.append(
            SystemMessage(subtype="init", data={"session_id": init_session_id, "skills": []})
        )
    if text is not None:
        messages.append(AssistantMessage(content=[TextBlock(text=text)], model="test"))
    messages.append(_make_result_message(structured))
    return messages


def _patch_claude_sdk_receive_response(monkeypatch, scripts: list[list[Any]]) -> list[int]:
    """Patch ClaudeSDKClient with fakes driving receive_response().

    Design: A1 iterates client.receive_response(); fakes must expose it.
    Implementation: install a factory returning a fake whose response generator
        yields the next scripted message list.
    Example: _patch_claude_sdk_receive_response(mp, [script_a, script_b]).
    """
    import claude_agent_sdk

    counter: list[int] = [0]

    class _Fake:
        """ClaudeSDKClient double exposing the real streaming methods.

        Design: tests need no subprocess but must match the SDK seam shape.
        Implementation: async context manager, query no-op, response generators.
        Example: async with _Fake(messages) as client: ...
        """

        def __init__(self, messages: list[Any]) -> None:
            """Store the scripted response messages.

            Design: each fake represents one Claude turn.
            Implementation: assign the message list verbatim.
            Example: _Fake([SystemMessage(...)]).
            """
            self._messages = messages

        async def __aenter__(self):
            """Enter the fake SDK context.

            Design: production uses ClaudeSDKClient as an async context manager.
            Implementation: return self with no setup.
            Example: async with fake as client: ...
            """
            return self

        async def __aexit__(self, *args: Any) -> None:
            """Exit the fake SDK context.

            Design: no teardown is needed for the fake.
            Implementation: return None.
            Example: context manager cleanup calls this.
            """
            return None

        async def query(self, *, prompt: str) -> None:
            """Pretend to issue a query.

            Design: ClaudeRunnerImpl calls query before receiving messages.
            Implementation: no-op after accepting the prompt kwarg.
            Example: await client.query(prompt='hi').
            """
            return None

        async def receive_response(self):
            """Yield the finite scripted response stream.

            Design: A1 consumes receive_response, not receive_messages.
            Implementation: async-yield each stored message.
            Example: async for msg in client.receive_response(): ...
            """
            for msg in self._messages:
                yield msg

        async def receive_messages(self):
            """Yield the same script for compatibility-only tests.

            Design: the fake remains close to SDK but production should not use this.
            Implementation: async-yield each stored message.
            Example: async for msg in client.receive_messages(): ...
            """
            for msg in self._messages:
                yield msg

        async def interrupt(self) -> None:
            """No-op interrupt to satisfy lifecycle paths.

            Design: aclose escalation may call interrupt best-effort.
            Implementation: return None.
            Example: await client.interrupt().
            """
            return None

    def factory(*args: Any, **kwargs: Any) -> _Fake:
        """Return the next scripted fake client.

        Design: each run_with_messages call instantiates ClaudeSDKClient once.
        Implementation: increment a list-backed counter for closure mutability.
        Example: factory(options=...).
        """
        idx = counter[0]
        counter[0] = idx + 1
        return _Fake(scripts[idx])

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", factory)
    return counter


class _FakeTurnHandle:
    """AsyncTurnHandle double whose .stream() yields method/payload events.

    Design: A6 adapts handle.stream() events into CodexEvent(kind=.method,
        payload=<dict>); the fake terminates after its configured events.
    Implementation: async-yield namespace-like event objects.
    Example: async for ev in handle.stream(): ...
    """

    def __init__(self, events: list[Any]) -> None:
        """Store the stream events.

        Design: each handle owns one finite turn stream.
        Implementation: assign the list verbatim.
        Example: _FakeTurnHandle([_Ev('turn/completed', {})]).
        """
        self._events = events

    async def stream(self):
        """Yield configured events.

        Design: matches AsyncTurnHandle.stream().
        Implementation: async-yield each event in order.
        Example: async for ev in handle.stream(): ...
        """
        for event in self._events:
            yield event


class _Ev:
    """One streamed Codex event exposing .method and .payload.

    Design: real SDK events expose .method (str) and .payload (typed/dict).
    Implementation: store both attributes verbatim.
    Example: _Ev('turn/started', {}).
    """

    def __init__(self, method: str, payload: Any) -> None:
        """Store method and payload.

        Design: CodexRunnerImpl maps method to event kind.
        Implementation: assign public attributes.
        Example: _Ev('turn/completed', {}).method.
        """
        self.method = method
        self.payload = payload


class _FakeThread:
    """AsyncThread double exposing .id and an awaitable .turn(...).

    Design: A6 calls await thread.turn(input, cwd=, sandbox_policy=, approval_mode=).
    Implementation: store id; turn records kwargs and returns a handle.
    Example: thread = _FakeThread('thr_1', handle, recorder).
    """

    def __init__(self, id_: str | None, handle: Any, recorder: dict[str, Any]) -> None:
        """Bind id, handle, and recorder.

        Design: tests assert lifecycle ids and call kwargs.
        Implementation: assign values for later turn().
        Example: _FakeThread('thr_1', handle, {}).
        """
        self.id = id_
        self._handle = handle
        self._recorder = recorder

    async def turn(self, input: Any, *, cwd=None, sandbox_policy=None, approval_mode=None, **k):
        """Record turn args and return the configured handle.

        Design: mirrors AsyncThread.turn's keyword-only policy args.
        Implementation: update recorder with observed arguments.
        Example: await thread.turn(TextInput(text='go'), cwd='/repo').
        """
        self._recorder.update(
            input=input, cwd=cwd, sandbox_policy=sandbox_policy, approval_mode=approval_mode
        )
        return self._handle


class _FakeAsyncCodex:
    """AsyncCodex double accepting a single config kwarg with close tracking.

    Design: A6 constructs AsyncCodex(config=server_config) and opens it as an
        async context manager; A6-lifecycle requires idempotent close.
    Implementation: record config; expose __aenter__/__aexit__/close and a
        thread_start() returning the configured thread.
    Example: _FakeAsyncCodex(config=cfg, thread=thread, closes=[0]).
    """

    def __init__(self, *, config: Any, thread: Any, closes: list[int]) -> None:
        """Bind config, thread, and close counter.

        Design: tests inspect constructor shape and close count.
        Implementation: store attributes verbatim.
        Example: _FakeAsyncCodex(config=object(), thread=t, closes=[0]).
        """
        self.config = config
        self._thread = thread
        self._closes = closes

    async def __aenter__(self):
        """Enter the fake Codex context.

        Design: production manually calls __aenter__ before setup.
        Implementation: return self.
        Example: await codex.__aenter__().
        """
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit the fake Codex context.

        Design: included for async context parity.
        Implementation: return None.
        Example: await codex.__aexit__(None, None, None).
        """
        return None

    async def thread_start(self, **kwargs: Any) -> Any:
        """Return the configured fake thread.

        Design: matches AsyncCodex.thread_start().
        Implementation: ignore kwargs and return self._thread.
        Example: thread = await codex.thread_start().
        """
        return self._thread

    async def close(self) -> None:
        """Increment the close counter.

        Design: lifecycle tests assert exactly-once close semantics.
        Implementation: mutate the shared single-element list.
        Example: await codex.close().
        """
        self._closes[0] += 1


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
    """Pin §H7 network_access flips the Codex SandboxPolicy RootModel flag.

    Design: §H7 makes network access a caller knob; A6 reads the real SDK flag
        at policy.root.network_access.
    Implementation: build policies with network_access False and True and read
        policy.root.network_access precisely.
    Example: sandbox_policy_for(..., network_access=False) disables network.
    """
    import pytest

    pytest.importorskip("openai_codex")
    from forge_mcp.drivers._codex import sandbox_policy_for

    def _net(policy) -> bool:
        """Read the network-access flag off the SandboxPolicy RootModel.

        Design: A6 builds SandboxPolicy via model_validate; the flag reads at
            policy.root.network_access.
        Implementation: read policy.root.network_access.
        Example: _net(policy) is False for a network-disabled policy.
        """
        return bool(policy.root.network_access)

    off = sandbox_policy_for(
        target_dir=tmp_path, iteration_dir=tmp_path / "iter", network_access=False
    )
    on = sandbox_policy_for(
        target_dir=tmp_path, iteration_dir=tmp_path / "iter", network_access=True
    )
    assert _net(off) is False
    assert _net(on) is True


async def test_claude_runner_last_session_id_none_before_first_call(monkeypatch) -> None:
    """Pin §C2.2 — ClaudeRunnerImpl.last_session_id is None until first run.

    Design: §C2.2 capture-only invariant says the attribute is None pre-call.
    Implementation: instantiate the runner and read the attribute only.
    Example: pytest tests/test_drivers_protocols.py -k none_before_first_call -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    runner = ClaudeRunnerImpl()
    assert runner.last_session_id is None


async def test_claude_runner_drains_structured_output_via_receive_response(monkeypatch) -> None:
    """Pin A1/A3 — drain reads ResultMessage.structured_output via receive_response.

    Design: A1 consumes one turn with receive_response(); A3 reads structured
        output from ResultMessage.structured_output.
    Implementation: script real SDK message/block types and assert drained data.
    Example: pytest tests/test_drivers_protocols.py -k drains_structured_output -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk_receive_response(
        monkeypatch,
        [_claude_turn_script(text="hello", structured={"available": ["x"]})],
    )
    runner = ClaudeRunnerImpl()
    result = await runner.run(prompt="hi", options=MagicMock(), system="sys")
    assert result.text == "hello"
    assert result.structured == {"available": ["x"]}


async def test_run_with_messages_stops_early_on_predicate(monkeypatch) -> None:
    """Pin A2 — run_with_messages breaks the stream when stop(msg) is truthy.

    Design: §A2 requires breaking out of receive_response() as soon as the init
        message is read; the async-with teardown aborts the throwaway turn.
    Implementation: drive a fake whose receive_response yields an init message
        then trailing messages, recording each pull; assert the trailing
        messages are never consumed once stop fires on the init message.
    Example: pytest tests/test_drivers_protocols.py -k stops_early -v.
    """
    import claude_agent_sdk
    from claude_agent_sdk import SystemMessage

    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    consumed: list[Any] = []

    class _RecordingFake:
        """ClaudeSDKClient double recording which messages get pulled.

        Design: A2 early-exit must stop pulling after the init message.
        Implementation: async context manager whose receive_response appends
            each yielded message to a shared list before yielding it.
        Example: async with _RecordingFake() as client: ...
        """

        async def __aenter__(self):
            """Enter the fake context returning self.

            Design: production uses ClaudeSDKClient as an async context manager.
            Implementation: return self with no setup.
            Example: async with _RecordingFake() as client: ...
            """
            return self

        async def __aexit__(self, *args: Any) -> None:
            """Exit the fake context with no teardown.

            Design: exiting the context is what aborts the in-flight turn.
            Implementation: return None.
            Example: context-manager cleanup calls this.
            """
            return None

        async def query(self, *, prompt: str) -> None:
            """Accept the probe query without doing work.

            Design: run_with_messages issues query before receiving.
            Implementation: no-op after accepting prompt.
            Example: await client.query(prompt='hi').
            """
            return None

        async def receive_response(self):
            """Yield init then trailing messages, recording each pull.

            Design: trailing messages model the throwaway turn that A2 must
                not wait out.
            Implementation: append each message to `consumed` before yielding.
            Example: async for msg in client.receive_response(): ...
            """
            init = SystemMessage(subtype="init", data={"skills": ["x"]})
            trailing = SystemMessage(subtype="other", data={})
            for msg in (init, trailing):
                consumed.append(msg)
                yield msg

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", lambda *a, **k: _RecordingFake())

    runner = ClaudeRunnerImpl()

    def _stop(msg: Any) -> bool:
        """Stop once an init SystemMessage with a skills field is seen.

        Design: mirrors the probe's real predicate.
        Implementation: check subtype init and a skills key in data.
        Example: _stop(SystemMessage(subtype='init', data={'skills': []})).
        """
        subtype = getattr(msg, "subtype", None)
        data = getattr(msg, "data", None)
        return subtype == "init" and isinstance(data, dict) and "skills" in data

    turn = await runner.run_with_messages(prompt="probe", options=object(), system="", stop=_stop)

    assert len(turn.messages) == 1
    assert getattr(turn.messages[0], "subtype", None) == "init"
    # §A2 — the trailing message after init must never be consumed.
    assert len(consumed) == 1


async def test_claude_runner_last_session_id_populated_then_preserved_after_aclose(
    monkeypatch,
) -> None:
    """Pin §C2.2 lifecycle — populated by init SystemMessage, preserved after aclose.

    Design: §C2.2 says the attribute is set when init arrives and survives
        aclose() so orchestrator can read it after the call.
    Implementation: script a real init SystemMessage with data['session_id'].
    Example: pytest tests/test_drivers_protocols.py -k preserved_after_aclose -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk_receive_response(
        monkeypatch,
        [_claude_turn_script(text=None, structured=None, init_session_id="sess_A")],
    )
    runner = ClaudeRunnerImpl()
    await runner.run(prompt="hi", options=MagicMock(), system="sys")
    assert runner.last_session_id == "sess_A"
    await runner.aclose()
    assert runner.last_session_id == "sess_A"


async def test_claude_runner_last_session_id_resets_at_start_of_next_call(monkeypatch) -> None:
    """Pin §C2.2 reset — later calls overwrite the captured session id.

    Design: each call creates a fresh SDK session; stale ids must not leak.
    Implementation: script two calls with distinct real init messages.
    Example: pytest tests/test_drivers_protocols.py -k resets_at_start -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk_receive_response(
        monkeypatch,
        [
            _claude_turn_script(text=None, structured=None, init_session_id="sess_A"),
            _claude_turn_script(text=None, structured=None, init_session_id="sess_B"),
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
    """Pin §C11 risk 4 — missing session_id leaves last_session_id None.

    Design: malformed init data must not fail the stream or terminate the run.
    Implementation: script an init message with no session_id and assert no id.
    Example: pytest tests/test_drivers_protocols.py -k fail_soft_when_init_missing_field -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import SystemMessage

    from forge_mcp.drivers._claude import ClaudeRunnerImpl

    _patch_claude_sdk_receive_response(
        monkeypatch,
        [[SystemMessage(subtype="init", data={"skills": []}), _make_result_message(None)]],
    )
    runner = ClaudeRunnerImpl()
    await runner.run(prompt="hi", options=MagicMock(), system="sys")
    assert runner.last_session_id is None


def test_build_options_wires_stderr_callback_to_run_log(tmp_path) -> None:
    """Pin B8 — build_options(run_log_path=...) tees Claude stderr.

    Design: B8 wires ClaudeAgentOptions(stderr=callback) to run.log.
    Implementation: invoke the callback and assert the prefixed line is written.
    Example: pytest tests/test_drivers_protocols.py -k stderr_callback -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers._claude import build_options

    run_log = tmp_path / "run.log"
    run_log.write_text("")
    opts = build_options(run_log_path=run_log)
    assert callable(opts.stderr)
    opts.stderr("kaboom")
    assert "[claude-stderr] kaboom" in run_log.read_text()


async def test_codex_turn_streams_method_to_kind_and_closes_on_exhaustion(monkeypatch) -> None:
    """Pin A6/A6-lifecycle — method maps to kind and close runs on exhaustion.

    Design: A6 maps ev.method to CodexEvent.kind; success cleanup happens in
        the stream generator's finally.
    Implementation: collect kinds from a fake stream and assert one close.
    Example: pytest tests/test_drivers_protocols.py -k closes_on_exhaustion -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    closes = [0]
    recorder: dict[str, Any] = {}
    handle = _FakeTurnHandle([_Ev("turn/started", {}), _Ev("turn/completed", {})])
    thread = _FakeThread("thr_1", handle, recorder)

    def factory(*, config, **kwargs):
        """Return a configured fake AsyncCodex.

        Design: production constructs AsyncCodex(config=...).
        Implementation: ignore extra kwargs and return the shared fake.
        Example: factory(config=object()).
        """
        return _FakeAsyncCodex(config=config, thread=thread, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    session = await runner.turn(
        instructions="go",
        server_config=object(),
        sandbox_policy=object(),
        approval_mode=object(),
        env=None,
    )
    kinds = [ev.kind async for ev in session]
    assert kinds == ["turn/started", "turn/completed"]
    assert closes[0] == 1
    assert runner.last_thread_id == "thr_1"


async def test_codex_turn_closes_codex_when_setup_raises(monkeypatch) -> None:
    """Pin A6-lifecycle — codex closes if setup raises after open.

    Design: thread_start/turn failures after __aenter__ must not leak codex.
    Implementation: make thread_start raise and assert close ran once.
    Example: pytest tests/test_drivers_protocols.py -k setup_raises -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    closes = [0]

    class _Boom(_FakeAsyncCodex):
        """Fake AsyncCodex whose thread_start raises.

        Design: drives setup-failure cleanup path.
        Implementation: raise RuntimeError from thread_start.
        Example: await _Boom(...).thread_start() raises.
        """

        async def thread_start(self, **kwargs: Any) -> Any:
            """Raise during setup.

            Design: setup errors after open must close codex.
            Implementation: raise RuntimeError unconditionally.
            Example: await codex.thread_start().
            """
            raise RuntimeError("setup failed")

    def factory(*, config, **kwargs):
        """Return the failing fake AsyncCodex.

        Design: production constructs AsyncCodex(config=...).
        Implementation: instantiate _Boom with no thread.
        Example: factory(config=object()).
        """
        return _Boom(config=config, thread=None, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    with pytest.raises(RuntimeError):
        await runner.turn(
            instructions="go",
            server_config=object(),
            sandbox_policy=object(),
            approval_mode=object(),
            env=None,
        )
    assert closes[0] == 1


async def test_codex_turn_double_close_is_idempotent(monkeypatch) -> None:
    """Pin A6-lifecycle — aclose after stream exhaust does not double-close.

    Design: all close paths share a one-shot guard.
    Implementation: exhaust stream, call aclose, and assert close count remains 1.
    Example: pytest tests/test_drivers_protocols.py -k double_close -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    closes = [0]
    recorder: dict[str, Any] = {}
    handle = _FakeTurnHandle([_Ev("turn/completed", {})])
    thread = _FakeThread("thr_1", handle, recorder)

    def factory(*, config, **kwargs):
        """Return a configured fake AsyncCodex.

        Design: production constructs AsyncCodex(config=...).
        Implementation: ignore extra kwargs and return the shared fake.
        Example: factory(config=object()).
        """
        return _FakeAsyncCodex(config=config, thread=thread, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    session = await runner.turn(
        instructions="go",
        server_config=object(),
        sandbox_policy=object(),
        approval_mode=object(),
        env=None,
    )
    _ = [ev async for ev in session]
    assert closes[0] == 1
    await runner.aclose()
    assert closes[0] == 1


async def test_codex_runner_last_thread_id_fail_soft_when_id_missing(monkeypatch) -> None:
    """Pin §C11 risk 5 — last_thread_id is None when AsyncThread omits .id.

    Design: SDK shape drift may omit a string id; runner must read None.
    Implementation: return a fake thread with id None and exhaust its stream.
    Example: pytest tests/test_drivers_protocols.py -k fail_soft_when_id_missing -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    closes = [0]
    recorder: dict[str, Any] = {}
    thread = _FakeThread(None, _FakeTurnHandle([_Ev("turn/completed", {})]), recorder)

    def factory(*, config, **kwargs):
        """Return a fake AsyncCodex with an id-less thread.

        Design: drives fail-soft last_thread_id behavior.
        Implementation: return configured _FakeAsyncCodex.
        Example: factory(config=object()).
        """
        return _FakeAsyncCodex(config=config, thread=thread, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    session = await runner.turn(
        instructions="go",
        server_config=object(),
        sandbox_policy=object(),
        approval_mode=object(),
        env=None,
    )
    _ = [ev async for ev in session]
    assert runner.last_thread_id is None


async def test_codex_stderr_tees_to_run_log(monkeypatch, tmp_path) -> None:
    """Pin B7 — Codex subprocess stderr lines are teed to run.log.

    Design: B7 replaces _stderr_lines with a deque subclass before __aenter__.
    Implementation: append a fake stderr line and assert run.log received it.
    Example: pytest tests/test_drivers_protocols.py -k stderr_tees -v.
    """
    import collections

    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex

    from forge_mcp.drivers._codex import CodexRunnerImpl

    run_log = tmp_path / "run.log"
    run_log.write_text("")
    closes = [0]
    recorder: dict[str, Any] = {}
    thread = _FakeThread("thr_1", _FakeTurnHandle([_Ev("turn/completed", {})]), recorder)

    class _Sync:
        """Private sync object exposing _stderr_lines.

        Design: mirrors the SDK private path used by B7.
        Implementation: hold a deque(maxlen=400).
        Example: _Sync()._stderr_lines.append('x').
        """

        def __init__(self) -> None:
            """Create the stderr deque.

            Design: B7 preserves maxlen and buffered lines.
            Implementation: initialize collections.deque(maxlen=400).
            Example: _Sync()._stderr_lines.maxlen == 400.
            """
            self._stderr_lines = collections.deque(maxlen=400)

    class _Client:
        """Private client object exposing _sync.

        Design: mirrors codex._client._sync.
        Implementation: create one _Sync instance.
        Example: _Client()._sync.
        """

        def __init__(self) -> None:
            """Create sync holder.

            Design: private SDK path starts at _client._sync.
            Implementation: assign _Sync().
            Example: _Client()._sync._stderr_lines.
            """
            self._sync = _Sync()

    class _CodexWithStderr(_FakeAsyncCodex):
        """Fake AsyncCodex exposing the private stderr deque path.

        Design: B7 installs before __aenter__ by replacing _stderr_lines.
        Implementation: add _client to the base fake.
        Example: _CodexWithStderr(...)._client._sync._stderr_lines.
        """

        def __init__(self, *, config: Any, thread: Any, closes: list[int]) -> None:
            """Initialize base fake and private client path.

            Design: tests need both normal lifecycle and stderr private path.
            Implementation: call super then assign _Client().
            Example: _CodexWithStderr(config=c, thread=t, closes=[0]).
            """
            super().__init__(config=config, thread=thread, closes=closes)
            self._client = _Client()

    def factory(*, config, **kwargs):
        """Return a fake Codex exposing stderr internals.

        Design: production constructs AsyncCodex(config=...).
        Implementation: instantiate _CodexWithStderr.
        Example: factory(config=object()).
        """
        return _CodexWithStderr(config=config, thread=thread, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    session = await runner.turn(
        instructions="go",
        server_config=object(),
        sandbox_policy=object(),
        approval_mode=object(),
        env=None,
        run_log_path=run_log,
    )
    assert runner._codex is not None
    runner._codex._client._sync._stderr_lines.append("boom")
    _ = [ev async for ev in session]
    assert "[codex-stderr] boom" in run_log.read_text()
