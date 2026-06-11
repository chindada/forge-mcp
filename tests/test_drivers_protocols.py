"""§5.2 concrete drivers must not import forbidden modules."""

from __future__ import annotations

import importlib
import inspect
from typing import Any, cast
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

    Design: openai-codex 0.132 calls await thread.turn(input, cwd=, approval_mode=);
        the sandbox preset/config are applied at thread_start, not on the turn.
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

    async def turn(self, input: Any, *, cwd=None, approval_mode=None, sandbox=None, **k):
        """Record turn args and return the configured handle.

        Design: mirrors openai-codex 0.132 AsyncThread.turn keyword-only args
            (sandbox preset/config now live on thread_start).
        Implementation: update recorder with observed arguments.
        Example: await thread.turn(TextInput(text='go'), cwd='/repo').
        """
        self._recorder.update(input=input, cwd=cwd, approval_mode=approval_mode, sandbox=sandbox)
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
        approval_mode=object(),
        env=None,
        run_log_path=run_log,
    )
    assert runner._codex is not None
    runner._codex._client._sync._stderr_lines.append("boom")
    _ = [ev async for ev in session]
    assert "[codex-stderr] boom" in run_log.read_text()


async def test_codex_thread_start_full_access_and_no_config(monkeypatch) -> None:
    """Pin F-Inv 1 + F-Inv 2 — thread_start gets the typed full-access preset.

    Design: §F5 item 4: the single thread_start call site passes
        sandbox=Sandbox.full_access via the typed parameter, no config=
        kwarg at all (the "full-access" vs "danger-full-access" namespace
        trap, F-Inv 2), and deny_all approvals (F-Inv 3).
    Implementation: monkeypatch AsyncCodex with a kwargs-capturing fake,
        run one turn with approval_mode=never_approval_mode(), drain the
        stream, and assert the captured kwargs identities.
    Example: pytest tests/test_drivers_protocols.py -k full_access -v.
    """
    import pytest

    pytest.importorskip("openai_codex")
    import openai_codex
    from openai_codex import ApprovalMode, Sandbox

    from forge_mcp.drivers._codex import CodexRunnerImpl, never_approval_mode

    captured: dict[str, Any] = {}
    closes = [0]
    handle = _FakeTurnHandle([_Ev("turn/completed", {})])
    thread = _FakeThread("thr_full", handle, {})

    class _CapturingAsyncCodex(_FakeAsyncCodex):
        """Fake AsyncCodex recording the kwargs thread_start receives.

        Design: §F5 item 4 needs the exact kwargs the seam passed; the base
            fake discards them.
        Implementation: stash kwargs into the shared dict, then delegate.
        Example: await _CapturingAsyncCodex(...).thread_start(sandbox=s).
        """

        async def thread_start(self, **kwargs: Any) -> Any:
            """Record kwargs and return the configured fake thread.

            Design: capture must not alter fake thread_start behavior.
            Implementation: update the captured dict and call super().
            Example: thread = await codex.thread_start(sandbox=s).
            """
            captured.update(kwargs)
            return await super().thread_start(**kwargs)

    def factory(*, config, **kwargs):
        """Return the capturing fake AsyncCodex.

        Design: production constructs AsyncCodex(config=server_config).
        Implementation: ignore extra kwargs and build the capturing fake.
        Example: factory(config=object()).
        """
        return _CapturingAsyncCodex(config=config, thread=thread, closes=closes)

    monkeypatch.setattr(openai_codex, "AsyncCodex", factory)

    runner = CodexRunnerImpl()
    session = await runner.turn(
        instructions="go",
        server_config=object(),
        approval_mode=never_approval_mode(),
        env=None,
    )
    async for _ in session:
        pass
    assert captured["sandbox"] is Sandbox.full_access
    assert "config" not in captured
    assert captured["approval_mode"] is ApprovalMode.deny_all


def test_src_carries_no_sandbox_or_network_knob_strings() -> None:
    """Pin F-Inv 1 structurally — forbidden sandbox strings absent from src.

    Design: §F5 item 5 grep guard in the repo's §15-guard tradition: no file
        under src/forge_mcp/ may contain workspace-write, workspace_write,
        sandbox_config_for, or network_access; full access is the only
        generator posture and the knob must not quietly return.
    Implementation: walk the installed package root (src layout) and scan
        every .py/.md file's text for each forbidden substring.
    Example: pytest tests/test_drivers_protocols.py -k knob_strings -v.
    """
    from pathlib import Path

    import forge_mcp

    root = Path(forge_mcp.__file__).parent
    forbidden = ("workspace-write", "workspace_write", "sandbox_config_for", "network_access")
    offenders = [
        f"{path.relative_to(root)}: {needle}"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".md"}
        for needle in forbidden
        if needle in path.read_text()
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# §G4.1 — prune_offcwd_write_copies helper unit pins (artifact containment).
# ---------------------------------------------------------------------------

_PRUNE_CONTENT = "# recovered artifact body\nline two\n"


class _PruneBlock:
    """Stand-in Write tool_use block exposing .name and .input (§G4.1).

    Design: §G2 iterates blocks exactly like collect_writes_to_basename, so
        tests need only .name and .input attributes.
    Implementation: plain attributes set in __init__.
    Example: _PruneBlock('/tmp/plan.md', 'body').name == 'Write'.
    """

    def __init__(self, file_path, content=_PRUNE_CONTENT, name="Write"):
        """Store the scripted block fields.

        Design: §G4.1 needs malformed inputs too, so file_path may be None.
        Implementation: build the input dict, omitting file_path when None.
        Example: _PruneBlock(None).input == {'content': _PRUNE_CONTENT}.
        """
        self.name = name
        self.input: dict[str, Any] = {"content": content}
        if file_path is not None:
            self.input["file_path"] = file_path


class _PruneMsg:
    """Stand-in message exposing a .content block list (§G4.1).

    Design: §G2 reads getattr(msg, 'content', None) or [] like the sibling.
    Implementation: assign the provided block list to .content.
    Example: _PruneMsg([_PruneBlock('p')]).content[0].name == 'Write'.
    """

    def __init__(self, blocks):
        """Store the scripted block list.

        Design: each test hand-builds exactly the blocks it needs.
        Implementation: assign verbatim.
        Example: _PruneMsg([]).content == [].
        """
        self.content = blocks


def _prune_setup(tmp_path):
    """Build a target_dir + canonical plan.md layout for prune tests (§G4.1).

    Design: tests mirror §13: canonical artifact under
        <target>/.harness/<run-id>/plan/plan.md.
    Implementation: mkdir the plan dir, write the canonical file with
        _PRUNE_CONTENT, return (target, canonical).
    Example: target, canonical = _prune_setup(tmp_path).
    """

    target = tmp_path / "target"
    plan_dir = target / ".harness" / "rid00001" / "plan"
    plan_dir.mkdir(parents=True)
    canonical = plan_dir / "plan.md"
    canonical.write_text(_PRUNE_CONTENT)
    return target, canonical


def test_prune_deletes_offcwd_copy_inside_target_dir(tmp_path) -> None:
    """Pin §G2 — gated leak inside target_dir is pruned; canonical survives.

    Design: G-Inv 1 — after recovery no authored file remains under
        target_dir outside .harness/.
    Implementation: physical copy at <target>/plan.md with matching content;
        assert unlink, return value, and canonical untouched.
    Example: pytest tests/test_drivers_protocols.py -k deletes_offcwd -v.
    """
    import os

    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    leak = target / "plan.md"
    leak.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock(str(leak))])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert removed == [os.path.abspath(leak)]
    assert not leak.exists()
    assert canonical.read_text() == _PRUNE_CONTENT


def test_prune_resolves_relative_path_against_canonical_parent(tmp_path) -> None:
    """Pin §G2 — relative file_path joins onto canonical_path.parent.

    Design: §G2 forbids resolving against os.getcwd(); '../../../plan.md'
        from <target>/.harness/<rid>/plan/ lands at <target>/plan.md.
    Implementation: relative Write block; assert the target-root copy is gone.
    Example: pytest tests/test_drivers_protocols.py -k relative_path -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    leak = target / "plan.md"
    leak.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock("../../../plan.md")])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert len(removed) == 1
    assert not leak.exists()


def test_prune_spares_different_content(tmp_path) -> None:
    """Pin G-Inv 2 — a same-basename file with different text survives.

    Design: content-match is the single guard against deleting caller-owned
        files; path-trust alone never deletes (G-Decision 2).
    Implementation: leak file holds other text; assert untouched and [] back.
    Example: pytest tests/test_drivers_protocols.py -k different_content -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    leak = target / "plan.md"
    leak.write_text("a real, unrelated project file\n")
    msgs = [_PruneMsg([_PruneBlock(str(leak))])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert removed == []
    assert leak.read_text() == "a real, unrelated project file\n"


def test_prune_spares_paths_outside_target_dir(tmp_path) -> None:
    """Pin G-Decision 3 — strays outside target_dir are not the harness's.

    Design: only copies inside the caller's workspace are the §13 leak;
        external writes are left alone.
    Implementation: matching-content copy in a sibling dir outside target;
        assert untouched.
    Example: pytest tests/test_drivers_protocols.py -k outside_target -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    stray = outside / "plan.md"
    stray.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock(str(stray))])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert removed == []
    assert stray.exists()


def test_prune_never_deletes_canonical_path(tmp_path) -> None:
    """Pin §G2 gate 1 — the just-rebuilt canonical file is never deleted.

    Design: a Write block naming the canonical path itself must be skipped
        even though basename and content both match.
    Implementation: block file_path == canonical; assert survival and [].
    Example: pytest tests/test_drivers_protocols.py -k never_deletes_canonical -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    msgs = [_PruneMsg([_PruneBlock(str(canonical))])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert removed == []
    assert canonical.read_text() == _PRUNE_CONTENT


def test_prune_skips_malformed_write_block(tmp_path) -> None:
    """Pin §G2 — missing/None file_path is skipped, never KeyError/TypeError.

    Design: mirrors collect_writes_to_basename's defensive accessors; the
        gate-swallow does not cover these, so the accessors must.
    Implementation: one block without file_path, one with file_path=None;
        assert no exception and no deletion.
    Example: pytest tests/test_drivers_protocols.py -k malformed -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    none_block = _PruneBlock(None)
    none_block.input["file_path"] = None
    msgs = [_PruneMsg([_PruneBlock(None), none_block])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert removed == []


def test_prune_returns_empty_when_target_dir_none(tmp_path) -> None:
    """Pin §G2 — target_dir=None returns [] with no filesystem access.

    Design: the guard is the first statement; nothing is resolved or stat'd.
    Implementation: a would-match leak exists on disk; with target_dir=None
        it survives and [] is returned.
    Example: pytest tests/test_drivers_protocols.py -k target_dir_none -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    leak = target / "plan.md"
    leak.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock(str(leak))])]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=None
    )

    assert removed == []
    assert leak.exists()


def test_prune_dedupes_repeated_blocks_and_prunes_all_copies(tmp_path) -> None:
    """Pin §G2 + G7.4 — duplicates de-duplicate; distinct copies all pruned.

    Design: removed paths are collected in encounter order, de-duplicated by
        resolved path; multiple distinct gated copies are all removed.
    Implementation: two blocks naming the same leak plus one naming a second
        leak in a subdir; assert both files gone and exactly two entries.
    Example: pytest tests/test_drivers_protocols.py -k dedupes -v.
    """
    from forge_mcp.drivers._claude import prune_offcwd_write_copies

    target, canonical = _prune_setup(tmp_path)
    leak_a = target / "plan.md"
    leak_a.write_text(_PRUNE_CONTENT)
    sub = target / "docs"
    sub.mkdir()
    leak_b = sub / "plan.md"
    leak_b.write_text(_PRUNE_CONTENT)
    msgs = [
        _PruneMsg([_PruneBlock(str(leak_a)), _PruneBlock(str(leak_a))]),
        _PruneMsg([_PruneBlock(str(leak_b))]),
    ]

    removed = prune_offcwd_write_copies(
        msgs, "plan.md", canonical_path=canonical, content=_PRUNE_CONTENT, target_dir=target
    )

    assert len(removed) == 2
    assert not leak_a.exists() and not leak_b.exists()


# ---------------------------------------------------------------------------
# §G4.2 / §G4.3 — driver-level recovery + prune pins (planner & evaluator).
# ---------------------------------------------------------------------------


class _OffcwdRunner:
    """ClaudeRunner double whose turn carries scripted Write blocks (§G4.2).

    Design: recovery fires when the canonical file is absent after the turn;
        the double never touches disk, so recovery + prune drive everything.
    Implementation: run_with_messages returns a ClaudeTurn wrapping the
        scripted message list; lifecycle methods are no-ops.
    Example: PlannerDriver(_OffcwdRunner(msgs)).
    """

    def __init__(self, messages):
        """Store the scripted turn messages.

        Design: one fake == one Claude turn.
        Implementation: assign list verbatim; last_session_id stays None.
        Example: _OffcwdRunner([_PruneMsg([...])]).
        """
        self._messages = messages
        self.last_session_id: str | None = None

    async def run_with_messages(self, *, prompt, options, system, stop=None):
        """Return the scripted ClaudeTurn.

        Design: drivers reach the SDK only through this seam (§5.2).
        Implementation: wrap stored messages in ClaudeTurn with empty text.
        Example: turn = await runner.run_with_messages(prompt='p', options=o, system='s').
        """
        from forge_mcp.drivers._claude import ClaudeTurn, StructuredResult

        _ = (prompt, options, system, stop)
        return ClaudeTurn(
            result=StructuredResult(structured=None, text=""), messages=self._messages
        )

    async def run(self, *, prompt, options, system):
        """Satisfy the ClaudeRunner protocol; unused here.

        Design: these tests exercise run_with_messages only.
        Implementation: raise loudly on accidental use.
        Example: never called by write_plan/write_remediation.
        """
        raise AssertionError("run() is not used in these tests")

    async def interrupt(self) -> None:
        """No-op interrupt for protocol compatibility.

        Design: lifecycle is out of scope for prune pins.
        Implementation: return None.
        Example: await runner.interrupt().
        """

    async def aclose(self) -> None:
        """No-op close for protocol compatibility.

        Design: lifecycle is out of scope for prune pins.
        Implementation: return None.
        Example: await runner.aclose().
        """

    def terminate(self) -> None:
        """No-op terminate for protocol compatibility.

        Design: lifecycle is out of scope for prune pins.
        Implementation: return None.
        Example: runner.terminate().
        """


async def test_write_plan_recovery_prunes_offcwd_copy(tmp_path) -> None:
    """Pin §G4.2 — planner recovery rebuilds plan.md and prunes the orphan.

    Design: G-Decision 6 — the planner ctx has no target_dir; the containment
        root is run_dir.parent.parent, which §13 makes exactly target_dir.
    Implementation: physical leak at the temp target root; fake turn carries
        the matching absolute Write; assert canonical present, leak gone, and
        the descriptor extends the recovered prefix with the pruned suffix.
    Example: pytest tests/test_drivers_protocols.py -k write_plan_recovery -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers.planner import PlannerDriver
    from forge_mcp.runcontext import RunContext

    target = tmp_path
    run_dir = target / ".harness" / "rid00001"
    leak = target / "plan.md"
    leak.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock(str(leak))])]
    driver = PlannerDriver(cast(Any, _OffcwdRunner(msgs)))

    descriptor = await driver.write_plan(RunContext(run_dir=run_dir))

    assert (run_dir / "plan" / "plan.md").read_text() == _PRUNE_CONTENT
    assert not leak.exists()
    assert descriptor == ("recovered planner Write tool content for plan.md; pruned 1 off-cwd copy")


async def test_write_plan_recovery_without_orphan_keeps_descriptor_text(tmp_path) -> None:
    """Pin §G4.3 — recovery with no off-cwd copy keeps the original descriptor.

    Design: the descriptor is extended, not replaced; the suffix appears only
        when a copy was actually removed.
    Implementation: the Write block names a path that does not exist on disk;
        assert the canonical file is rebuilt and the descriptor is unchanged.
    Example: pytest tests/test_drivers_protocols.py -k without_orphan -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers.planner import PlannerDriver
    from forge_mcp.runcontext import RunContext

    target = tmp_path
    run_dir = target / ".harness" / "rid00001"
    msgs = [_PruneMsg([_PruneBlock(str(target / "plan.md"))])]
    driver = PlannerDriver(cast(Any, _OffcwdRunner(msgs)))

    descriptor = await driver.write_plan(RunContext(run_dir=run_dir))

    assert (run_dir / "plan" / "plan.md").read_text() == _PRUNE_CONTENT
    assert descriptor == "recovered planner Write tool content for plan.md"


async def test_write_remediation_recovery_prunes_offcwd_copy(tmp_path) -> None:
    """Pin §G4.2 + §G6.3 — remediation recovery prunes the target-root orphan.

    Design: reproduces run 88c16115 under control: an absolute Write to the
        target_dir root, byte-identical to the recovered contract.md; after
        write_remediation, target_dir is clean apart from .harness/.
    Implementation: leak at <tmp>/contract.md; ctx.target_dir set directly;
        assert canonical iteration-2/contract.md, leak gone, suffixed
        descriptor, and target_dir containing only .harness afterwards.
    Example: pytest tests/test_drivers_protocols.py -k write_remediation_recovery -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers.evaluator import EvaluatorDriver
    from forge_mcp.models import EvalResult
    from forge_mcp.runcontext import RunContext

    target = tmp_path
    run_dir = target / ".harness" / "rid00001"
    run_dir.mkdir(parents=True)
    leak = target / "contract.md"
    leak.write_text(_PRUNE_CONTENT)
    msgs = [_PruneMsg([_PruneBlock(str(leak))])]
    driver = EvaluatorDriver(cast(Any, _OffcwdRunner(msgs)))
    ctx = RunContext(run_dir=run_dir, target_dir=target, iteration_n=1)
    eval_result = EvalResult(no_gaps=True, gaps=[], summary="ok")

    descriptor = await driver.write_remediation(ctx, next_iteration_n=2, eval_result=eval_result)

    assert (run_dir / "iteration-2" / "contract.md").read_text() == _PRUNE_CONTENT
    assert not leak.exists()
    assert descriptor == (
        "recovered remediation Write tool content for contract.md; pruned 1 off-cwd copy"
    )
    assert [p.name for p in target.iterdir()] == [".harness"]


async def test_write_remediation_recovery_without_orphan_keeps_descriptor_text(
    tmp_path,
) -> None:
    """Pin §G4.3 — remediation descriptor unchanged when nothing was pruned.

    Design: the suffix appears only when a copy was actually removed.
    Implementation: Write block names a non-existent path; assert canonical
        rebuilt and the original descriptor text returned verbatim.
    Example: pytest tests/test_drivers_protocols.py -k remediation_recovery_without -v.
    """
    import pytest

    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.drivers.evaluator import EvaluatorDriver
    from forge_mcp.models import EvalResult
    from forge_mcp.runcontext import RunContext

    target = tmp_path
    run_dir = target / ".harness" / "rid00001"
    run_dir.mkdir(parents=True)
    msgs = [_PruneMsg([_PruneBlock(str(target / "contract.md"))])]
    driver = EvaluatorDriver(cast(Any, _OffcwdRunner(msgs)))
    ctx = RunContext(run_dir=run_dir, target_dir=target, iteration_n=1)
    eval_result = EvalResult(no_gaps=True, gaps=[], summary="ok")

    descriptor = await driver.write_remediation(ctx, next_iteration_n=2, eval_result=eval_result)

    assert (run_dir / "iteration-2" / "contract.md").read_text() == _PRUNE_CONTENT
    assert descriptor == "recovered remediation Write tool content for contract.md"
