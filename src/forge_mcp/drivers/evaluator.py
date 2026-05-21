"""§10.3 EvaluatorDriver — evaluate, triage, and remediate."""

from __future__ import annotations

import json
from importlib.resources import files

from ..artifacts import atomic_write_json, atomic_write_text, render_eval_md
from ..errors import OutputSchemaError
from ..models import EvalResult, TriageResult
from ..runcontext import RunContext
from ..schemas.eval_result import EVAL_RESULT_SCHEMA
from ..schemas.triage_result import TRIAGE_RESULT_SCHEMA
from ._claude import (
    CLAUDE_SETTING_SOURCES,
    ClaudeRunner,
    build_options,
    collect_writes_to_basename,
    maybe_append_retry_suffix,
    truncate_for_warning,
)


class EvaluatorDriver:
    """Claude-backed evaluator phase driver.

    Design: §9.2 uses Claude for schema-bearing evaluation/triage and a
        non-schema remediation contract, each in fresh sessions.
    Implementation: all Claude options flow through `_claude.build_options`,
        parse errors raise OutputSchemaError for orchestrator retry.
    Example: result = await EvaluatorDriver(runner).evaluate(ctx).
    """

    def __init__(self, runner: ClaudeRunner) -> None:
        """Store the Claude runner seam.

        Design: evaluator tests substitute fakes at this protocol boundary.
        Implementation: assign runner for later method calls.
        Example: EvaluatorDriver(fake_runner).
        """
        self._runner = runner

    async def evaluate(self, ctx: RunContext, *, retry: bool = False) -> EvalResult:
        """Evaluate the current iteration and write eval artifacts.

        Design: §10.3 returns structured EvalResult with one orchestrator-owned
            schema retry on parse failure.
        Implementation: run Claude with EVAL_RESULT_SCHEMA, parse once, validate
            via Pydantic, then write eval.json and eval.md.
        Example: er = await driver.evaluate(ctx, retry=False).
        """
        if ctx.iteration_n is None:
            raise RuntimeError("EvaluatorDriver requires ctx.iteration_n")
        if ctx.target_dir is None:
            raise RuntimeError("EvaluatorDriver.evaluate requires ctx.target_dir")
        iteration_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
        system = (files("forge_mcp.prompts") / "evaluator_system.md").read_text()
        prompt = maybe_append_retry_suffix(
            "Evaluate target_dir against inputs/design.md and return EvalResult JSON.", retry
        )
        result = await self._runner.run(
            prompt=prompt,
            options=build_options(
                setting_sources=CLAUDE_SETTING_SOURCES,
                output_format=EVAL_RESULT_SCHEMA,
                add_dirs=[ctx.target_dir, ctx.run_dir / "inputs"],
                disallowed_tools=("Edit",),
                cwd=iteration_dir,
                cli_path=ctx.claude_cli_path,
            ),
            system=system,
        )
        er = EvalResult.model_validate(_run_and_parse_json(result.structured, result.text))
        atomic_write_json(iteration_dir / "eval.json", er.model_dump(mode="json"))
        atomic_write_text(
            iteration_dir / "eval.md", render_eval_md(er, iteration_n=ctx.iteration_n)
        )
        return er

    async def triage_design_flaws(
        self, ctx: RunContext, *, eval_result: EvalResult, retry: bool = False
    ) -> TriageResult:
        """Classify evaluator gaps as design flaws or code bugs.

        Design: §11.1 strict citation validation happens in pure triage policy,
            but this driver obtains the structured TriageResult artifact.
        Implementation: run Claude with TRIAGE_RESULT_SCHEMA and write
            triage.json after a single parse/validation attempt.
        Example: tr = await driver.triage_design_flaws(ctx, eval_result=er).
        """
        if ctx.iteration_n is None:
            raise RuntimeError("EvaluatorDriver requires ctx.iteration_n")
        iteration_dir = ctx.run_dir / f"iteration-{ctx.iteration_n}"
        system = (files("forge_mcp.prompts") / "evaluator_triage.md").read_text()
        prompt = maybe_append_retry_suffix(
            "Triage these evaluator gaps against inputs/design.md:\n"
            + eval_result.model_dump_json(indent=2),
            retry,
        )
        result = await self._runner.run(
            prompt=prompt,
            options=build_options(
                setting_sources=CLAUDE_SETTING_SOURCES,
                output_format=TRIAGE_RESULT_SCHEMA,
                add_dirs=[ctx.run_dir / "inputs"],
                disallowed_tools=("Edit", "Write"),
                cwd=iteration_dir,
                cli_path=ctx.claude_cli_path,
            ),
            system=system,
        )
        triage = TriageResult.model_validate(_run_and_parse_json(result.structured, result.text))
        atomic_write_json(iteration_dir / "triage.json", triage.model_dump(mode="json"))
        return triage

    async def write_remediation(
        self, ctx: RunContext, *, next_iteration_n: int, eval_result: EvalResult
    ) -> str | None:
        """Write the next iteration contract.md via Claude.

        Design: §9.2 transitions to iter_remediating before this call so a
            failure is attributed to remediation, not evaluation.
        Implementation: run Claude in the next iteration directory and recover
            off-cwd Write tool content for contract.md when needed.
        Example: await driver.write_remediation(ctx, next_iteration_n=2, eval_result=er).
        """
        next_dir = ctx.run_dir / f"iteration-{next_iteration_n}"
        next_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        system = (files("forge_mcp.prompts") / "evaluator_remediation.md").read_text()
        prompt = "Write contract.md for the next generator iteration from this EvalResult:\n"
        prompt += eval_result.model_dump_json(indent=2)
        turn = await self._runner.run_with_messages(
            prompt=prompt,
            options=build_options(
                setting_sources=CLAUDE_SETTING_SOURCES,
                add_dirs=[ctx.run_dir / "inputs"],
                disallowed_tools=("Edit",),
                cwd=next_dir,
                cli_path=ctx.claude_cli_path,
            ),
            system=system,
        )
        contract_path = next_dir / "contract.md"
        if contract_path.exists():
            return None
        recovered = collect_writes_to_basename(turn.messages, "contract.md")
        if recovered is None:
            return None
        atomic_write_text(contract_path, recovered)
        return truncate_for_warning("recovered remediation Write tool content for contract.md")


def _run_and_parse_json(structured: dict | None, raw_text: str) -> dict:
    """Single parse attempt; raise OutputSchemaError on any failure.

    Design: §10.3 makes the driver attempt exactly one parse while the
        orchestrator owns retry through with_schema_retry.
    Implementation: prefer structured dict, otherwise json.loads raw text and
        wrap JSON failures in OutputSchemaError.
    Example: payload = _run_and_parse_json({'ok': True}, '').
    """
    if structured is not None:
        return structured
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OutputSchemaError(raw=raw_text, reason=str(exc)) from exc
    if not isinstance(parsed, dict):
        raise OutputSchemaError(raw=raw_text, reason="top-level JSON value is not an object")
    return parsed
