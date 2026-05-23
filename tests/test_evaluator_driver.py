"""§10.3 evaluator driver schema parsing and retry pins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forge_mcp.drivers._claude import SCHEMA_RETRY_SUFFIX, StructuredResult
from forge_mcp.drivers.evaluator import EvaluatorDriver
from forge_mcp.errors import OutputSchemaError
from forge_mcp.models import EvalResult
from forge_mcp.orchestrator.retry import with_schema_retry
from forge_mcp.runcontext import RunContext


class _Runner:
    """Claude runner fake returning queued structured results.

    Design: evaluator driver tests need to observe prompts without SDK calls.
    Implementation: pop StructuredResult objects and record prompt strings.
    Example: runner.prompts[-1] contains SCHEMA_RETRY_SUFFIX on retry.
    """

    def __init__(self, results: list[StructuredResult]) -> None:
        """Store queued results and initialize prompt capture.

        Design: each call consumes one canned Claude response.
        Implementation: keep mutable lists for results and prompts.
        Example: _Runner([StructuredResult({}, '')]).prompts == [].
        """
        self.results = results
        self.prompts: list[str] = []
        self.last_session_id: str | None = None

    async def run(self, *, prompt: str, options: Any, system: str) -> StructuredResult:
        """Return the next canned structured result.

        Design: this is the only EvaluatorDriver seam needed for schema tests.
        Implementation: append prompt and pop the first queued result.
        Example: await runner.run(prompt='p', options=None, system='s').
        """
        self.prompts.append(prompt)
        return self.results.pop(0)

    async def run_with_messages(self, *, prompt, options, system, stop=None):
        """Satisfy the ClaudeRunner protocol for unused methods.

        Design: Pyright checks the complete protocol even when tests call only run().
        Implementation: raise AssertionError so accidental use fails loudly.
        Example: evaluator schema tests never call run_with_messages.
        """
        raise AssertionError("run_with_messages is not used in these tests")

    async def interrupt(self) -> None:
        """Satisfy the ClaudeRunner protocol for unused lifecycle methods.

        Design: evaluator schema tests do not exercise cancellation cleanup.
        Implementation: no-op keeps the fake protocol-compatible.
        Example: await runner.interrupt() returns None.
        """

    async def aclose(self) -> None:
        """Satisfy the ClaudeRunner protocol for unused lifecycle methods.

        Design: evaluator schema tests do not exercise runner shutdown.
        Implementation: no-op keeps the fake protocol-compatible.
        Example: await runner.aclose() returns None.
        """

    def terminate(self) -> None:
        """Satisfy the ClaudeRunner protocol for unused lifecycle methods.

        Design: evaluator schema tests do not exercise hard termination.
        Implementation: no-op keeps the fake protocol-compatible.
        Example: runner.terminate() returns None.
        """


def _ctx(tmp_path: Path) -> RunContext:
    """Build a RunContext suitable for evaluator driver calls.

    Design: evaluator requires run_dir, target_dir, and iteration_n.
    Implementation: create the iteration directory and return a minimal context.
    Example: ctx = _ctx(tmp_path); ctx.iteration_n == 1.
    """
    run_dir = tmp_path / "run"
    target_dir = tmp_path / "target"
    (run_dir / "iteration-1").mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(exist_ok=True)
    return RunContext(run_dir=run_dir, target_dir=target_dir, iteration_n=1)


@pytest.mark.asyncio
async def test_evaluate_schema_validation_error_triggers_retry(tmp_path: Path) -> None:
    """Evaluator schema validation failures participate in schema retry.

    Design: §A generalizes retry from JSON syntax failures to Pydantic failures.
    Implementation: first response lacks required fields; retry returns valid EvalResult.
    Example: runner.prompts[1] includes the verbatim schema retry suffix.
    """
    valid = EvalResult(no_gaps=True, gaps=[], summary="ok").model_dump(mode="json")
    runner = _Runner([StructuredResult({"no_gaps": True}, ""), StructuredResult(valid, "")])
    driver = EvaluatorDriver(runner)

    result = await with_schema_retry(lambda retry: driver.evaluate(_ctx(tmp_path), retry=retry))

    assert result.summary == "ok"
    assert len(runner.prompts) == 2
    assert SCHEMA_RETRY_SUFFIX in runner.prompts[1]


@pytest.mark.asyncio
async def test_triage_schema_validation_error_triggers_retry(tmp_path: Path) -> None:
    """Triage schema validation failures participate in schema retry.

    Design: §A applies the same OutputSchemaError boundary to TriageResult.
    Implementation: first response has the wrong shape; retry returns a valid triage.
    Example: the second triage prompt carries SCHEMA_RETRY_SUFFIX verbatim.
    """
    runner = _Runner(
        [
            StructuredResult({"triages": "not-list", "summary": "bad"}, ""),
            StructuredResult({"triages": [], "summary": "ok"}, ""),
        ]
    )
    driver = EvaluatorDriver(runner)

    result = await with_schema_retry(
        lambda retry: driver.triage_design_flaws(
            _ctx(tmp_path), eval_result=EvalResult(no_gaps=True, gaps=[], summary="ok"), retry=retry
        )
    )

    assert result.summary == "ok"
    assert len(runner.prompts) == 2
    assert SCHEMA_RETRY_SUFFIX in runner.prompts[1]


@pytest.mark.asyncio
async def test_evaluate_validation_failure_raises_output_schema_error(tmp_path: Path) -> None:
    """Direct evaluator calls raise OutputSchemaError on schema deviations.

    Design: with_schema_retry owns retry; driver owns converting parse failures.
    Implementation: call evaluate once with an invalid structured object.
    Example: missing summary raises OutputSchemaError instead of ValidationError.
    """
    driver = EvaluatorDriver(_Runner([StructuredResult({"no_gaps": True}, "")]))

    with pytest.raises(OutputSchemaError):
        await driver.evaluate(_ctx(tmp_path))
