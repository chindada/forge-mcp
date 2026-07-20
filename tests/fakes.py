"""Test doubles (fakes) for ClaudeRunner and CodexRunner Protocols.

Reused by Tasks 17, 18, 19, 26, 27, and 28. Build once, use everywhere.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from forge_mcp.drivers._claude import ClaudeRunner, StructuredResult
from forge_mcp.drivers._codex import CodexEvent, CodexRunner

if TYPE_CHECKING:
    from openai_codex import CodexConfig


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def structured(payload: dict, *, init_skills: list[str] | None = None) -> StructuredResult:
    """Build a StructuredResult with *payload* as structured_output.

    Design: §17 tests need a concise way to build scripted StructuredResult
        values without repeating the full constructor every time; §10.3 skill
        probes additionally need to script the init-message skills list.
    Implementation: returns StructuredResult(structured_output=payload,
        text="", session_id="fake", init_skills=init_skills) — the minimal
        non-None scripted result; init_skills defaults to None so existing
        callers are unaffected.
    Example: ``structured({"x": 1}).structured_output == {"x": 1}``.
    """
    return StructuredResult(
        structured_output=payload, text="", session_id="fake", init_skills=init_skills
    )


# ---------------------------------------------------------------------------
# FakeClaudeRunner
# ---------------------------------------------------------------------------


class FakeClaudeRunner:
    """Scripted fake implementing the ClaudeRunner Protocol (§17).

    Design: §17 tests inject a sequence of pre-built StructuredResult values
        so orchestration code can be exercised without the real SDK.
    Implementation: pop from *scripted* in order; last_session_id tracks the
        session_id of the most-recently-returned result; interrupt/aclose are
        no-ops.
    Example: ``FakeClaudeRunner([structured({"x": 1})]).run(...)`` returns
        that StructuredResult and advances last_session_id.
    """

    def __init__(self, scripted: list[StructuredResult]) -> None:
        """Initialise with a list of pre-built results to return in order.

        Design: §17 the scripted list is consumed FIFO so tests can assert
            ordered interactions without mocking.
        Implementation: copy the list to avoid aliasing; set last_session_id
            to None until the first run().
        Example: ``FakeClaudeRunner([r1, r2])`` will return r1 then r2.
        """
        self._scripted = list(scripted)
        self.last_session_id: str | None = None
        self.prompts: list[str] = []

    async def run(self, *, prompt: str, options: object) -> StructuredResult:
        """Return the next scripted result and update last_session_id.

        Design: §17 consuming results in order mirrors real execution so tests
            can make deterministic assertions about each call.
        Implementation: pop index 0 (FIFO); update last_session_id; raise
            IndexError if the scripted list is exhausted (test bug).
        Example: after one call, last_session_id equals the returned result's
            session_id.
        """
        self.prompts.append(prompt)
        result = self._scripted.pop(0)
        self.last_session_id = result.session_id
        return result

    async def interrupt(self) -> None:
        """No-op interrupt; satisfies the ClaudeRunner Protocol.

        Design: §17 fakes must implement the full Protocol surface; interrupt
            is a best-effort signal that the fake ignores.
        Implementation: returns immediately.
        Example: ``await fake.interrupt()`` completes without side effects.
        """

    async def aclose(self) -> None:
        """No-op close; satisfies the ClaudeRunner Protocol.

        Design: §17 fakes must implement the full Protocol surface; aclose
            releases resources in real drivers but the fake has none.
        Implementation: returns immediately.
        Example: ``await fake.aclose()`` completes without side effects.
        """


assert isinstance(FakeClaudeRunner([]), ClaudeRunner)


# ---------------------------------------------------------------------------
# FakeCodexRunner
# ---------------------------------------------------------------------------


class FakeCodexRunner:
    """Scripted fake implementing the CodexRunner Protocol (§17).

    Design: §17 tests inject a sequence of CodexEvent values so orchestration
        code that consumes the generate() async generator can be exercised
        without the real Codex SDK.
    Implementation: generate() is a regular def returning an async generator;
        if on_generate is set it is called first so tests can inject side
        effects (e.g. sandbox file writes required by Task 28); last_thread_id
        returns a stable fake id; interrupt/aclose are no-ops.
    Example: ``FakeCodexRunner([e1, e2]).generate(...)`` yields e1 then e2.
    """

    def __init__(
        self,
        events: list[CodexEvent],
        on_generate: Callable[[str], object] | None = None,
    ) -> None:
        """Initialise with scripted events and an optional side-effect hook.

        Design: §17 on_generate lets tests inject sandbox file writes or other
            side effects that must happen before the first event is yielded;
            this is required by Task 28.
        Implementation: store events and hook; last_thread_id is a constant
            fake string.
        Example: ``FakeCodexRunner([e], on_generate=lambda cwd: write_file(cwd))``.
        """
        self._events = list(events)
        self._on_generate = on_generate
        self._last_thread_id: str | None = "fake-thread-id"

    @property
    def last_thread_id(self) -> str | None:
        """Return the fake thread id.

        Design: §17 callers that record thread ids for resume must get a
            non-None value from the fake so they don't short-circuit.
        Implementation: returns a stable constant string.
        Example: ``fake.last_thread_id == "fake-thread-id"``.
        """
        return self._last_thread_id

    def generate(
        self,
        *,
        instructions: str,
        config: CodexConfig,
        run_log_path: Path | None = None,
    ) -> AsyncGenerator[CodexEvent, None]:
        """Return an async generator yielding all scripted events.

        Design: §17 the generator contract mirrors the real CodexDriver so
            orchestration code can be tested without the SDK; on_generate is
            called first if set so tests can inject file-system side effects.
        Implementation: defined as a regular def returning an async generator
            expression via a private helper so the Protocol signature matches.
        Example: ``async for evt in fake.generate(...): ...`` yields each
            scripted event in order.
        """
        return self._gen(instructions=instructions, config=config)

    async def _gen(
        self,
        *,
        instructions: str,
        config: CodexConfig,
    ) -> AsyncGenerator[CodexEvent, None]:
        """Inner async generator: call hook then yield events.

        Design: §17 separating the public generate() (regular def) from this
            async generator allows the Protocol's non-async return type to be
            satisfied while still using async yield syntax.
        Implementation: call on_generate(str(config.cwd)) if set; then yield
            each event from the scripted list.
        Example: if on_generate writes a file, it is visible to the caller
            before the first CodexEvent is received.
        """
        if self._on_generate is not None:
            self._on_generate(str(config.cwd))
        for event in self._events:
            yield event

    async def interrupt(self) -> None:
        """No-op interrupt; satisfies the CodexRunner Protocol.

        Design: §17 fakes must implement the full Protocol surface; interrupt
            is a best-effort signal that the fake ignores.
        Implementation: returns immediately.
        Example: ``await fake.interrupt()`` completes without side effects.
        """

    async def aclose(self) -> None:
        """No-op close; satisfies the CodexRunner Protocol.

        Design: §17 fakes must implement the full Protocol surface; aclose
            releases resources in real drivers but the fake has none.
        Implementation: returns immediately.
        Example: ``await fake.aclose()`` completes without side effects.
        """


assert isinstance(FakeCodexRunner([]), CodexRunner)
