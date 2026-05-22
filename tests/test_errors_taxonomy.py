"""§W tests for error taxonomy and RunResult schema."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.shared.exceptions import McpError

from forge_mcp import errors
from forge_mcp.config import RunConfig
from forge_mcp.errors import FailureKind
from forge_mcp.lockfile import TargetLock
from forge_mcp.models import ArtifactIndex, RunForgeInput, RunResult
from forge_mcp.orchestrator.ledger import RunLedger
from forge_mcp.orchestrator.lifecycle import handle_cancellation
from forge_mcp.orchestrator.result import build_result
from forge_mcp.orchestrator.statemachine import RunStateMachine
from forge_mcp.preflight import prepare_run
from forge_mcp.server import run_forge_handler
from forge_mcp.state import RunState


def test_failure_kind_taxonomy_is_exactly_six() -> None:
    """§W-Inv 1 pins the closed six-kind taxonomy.

    Design: hosts may branch on these categories, so accidental additions or
        removals are API changes.
    Implementation: inspect the single-source prefix table.
    Example: pytest fails if a seventh key appears.
    """
    assert set(errors._PREFIX) == {
        "invalid_params",
        "infra_failure",
        "auth",
        "lock_held",
        "timeout",
        "cancelled",
    }


def test_failure_kind_prefix_verbatim() -> None:
    """§W2 pins the wire-visible prefix text.

    Design: callers match on these stable bracketed strings because JSON-RPC
        numeric codes are not reliably visible in FastMCP text results.
    Implementation: call errors.tag for each closed kind with a shared body.
    Example: errors.tag('timeout', 'body') has the timeout prefix.
    """
    expected = {
        "invalid_params": "[FORGE_ERR_INVALID_PARAMS] body",
        "infra_failure": "[FORGE_ERR_INFRA_FAILURE] body",
        "auth": "[FORGE_ERR_AUTH] body",
        "lock_held": "[FORGE_ERR_LOCK_HELD] body",
        "timeout": "[FORGE_ERR_TIMEOUT] body",
        "cancelled": "[FORGE_ERR_CANCELLED] body",
    }
    for kind, text in expected.items():
        typed_kind: FailureKind = kind  # type: ignore[assignment]
        assert errors.tag(typed_kind, "body") == text


def test_run_result_failure_kind_schema_and_default() -> None:
    """§W3 adds an optional failure_kind field to RunResult.

    Design: structured failure discrimination must travel with terminal normal
        returns while completed results keep the field null.
    Implementation: assert schema contents and construct a minimal completed
        result without passing failure_kind.
    Example: RunResult(...).failure_kind is None for completed.
    """
    schema = RunResult.model_json_schema()
    assert "failure_kind" in schema["properties"]
    text = repr(schema["properties"]["failure_kind"]) + repr(schema.get("$defs", {}))
    for kind in errors._PREFIX:
        assert kind in text
    result = RunResult(
        status="completed",
        run_id="abcd1234",
        run_dir="/r/.harness/abcd1234",
        iterations_used=1,
        runtime_seconds=1,
        artifacts=ArtifactIndex(
            plan_path="/r/plan/plan.md",
            status_log_path="/r/status.log",
            state_json_path="/r/state.json",
        ),
        message="ok",
    )
    assert result.failure_kind is None


def test_run_result_failure_kind_field_present() -> None:
    """§W3: RunResult JSON schema lists failure_kind as nullable six-kind field.

    Design: §-Tests enumerates a schema-shape pin so the structured failure
        discriminator stays wire-visible; a sibling of
        test_run_result_failure_kind_schema_and_default focused only on shape.
    Implementation: read RunResult.model_json_schema(), assert failure_kind is a
        property whose rendered type spans the six errors._PREFIX kinds and a
        null arm (string | null), tolerating Pydantic's anyOf/$defs encoding.
    Example: "failure_kind" in schema["properties"] and all six kinds appear.
    """
    schema = RunResult.model_json_schema()
    assert "failure_kind" in schema["properties"]
    prop = schema["properties"]["failure_kind"]
    rendered = repr(prop) + repr(schema.get("$defs", {}))
    for kind in errors._PREFIX:
        assert kind in rendered
    assert "null" in rendered


def test_prefix_construction_single_source() -> None:
    """§W-Decision 3: the FORGE_ERR_ prefix literal lives only in errors.py.

    Design: a single construction site keeps the wire-visible taxonomy from
        drifting across modules; every other module must route through tag().
    Implementation: scan src for the literal "[FORGE_ERR_" and assert the only
        matching file is errors.py.
    Example: result.py builds prefixed messages via tag(), not literals.
    """
    root = Path(errors.__file__).resolve().parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "errors.py":
            continue
        if re.search(r"\[FORGE_ERR_", path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"FORGE_ERR_ literal outside errors.py: {offenders}"


async def test_invalid_params_raise_has_prefix() -> None:
    """§W: invalid params raise carries the [FORGE_ERR_INVALID_PARAMS] prefix.

    Design: pydantic validation failures must surface the stable wire prefix so
        hosts can branch on the failure category despite the lost numeric code.
    Implementation: call run_forge_handler with arguments missing target_dir and
        assert the raised McpError message starts with the invalid_params prefix.
    Example: McpError(...).error.message startswith "[FORGE_ERR_INVALID_PARAMS] ".
    """
    with pytest.raises(McpError) as excinfo:
        await run_forge_handler({})
    message = excinfo.value.error.message
    assert message.startswith(errors._PREFIX["invalid_params"] + " ")


async def test_lock_held_raise_has_prefix(tmp_path: Path) -> None:
    """§W: a busy target lock raises the [FORGE_ERR_LOCK_HELD] prefix.

    Design: lock contention is a distinct, host-branchable failure category.
    Implementation: reproduce the foreign-lock scenario used by the preflight
        lock tests, invoke the prepare path, and assert the McpError prefix.
    Example: McpError(...).error.message startswith "[FORGE_ERR_LOCK_HELD] ".
    """
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / ".harness").mkdir(mode=0o700)
    held = TargetLock(target_dir / ".harness" / "run.lock")
    held.acquire()
    try:
        inputs = RunForgeInput(target_dir=str(target_dir), design_doc_content="x")
        with pytest.raises(McpError) as excinfo:
            await prepare_run(inputs, RunConfig.from_env())
        assert excinfo.value.error.message.startswith(errors._PREFIX["lock_held"] + " ")
    finally:
        held.release()


def test_timeout_result_has_timeout_kind(tmp_path: Path) -> None:
    """§W: a runtime cap-hit result carries failure_kind=timeout + prefix.

    Design: the runtime cap path is real work cut short and returns a normal
        incomplete RunResult tagged so hosts can distinguish it from disconnect.
    Implementation: build the cap-hit RunResult through the final result builder
        used by the finalizing path and assert kind + prefix.
    Example: result.failure_kind == "timeout" and message has the timeout prefix.
    """
    run_dir = tmp_path / "abcd1234"
    (run_dir / "plan").mkdir(parents=True)
    (run_dir / "plan" / "plan.md").write_text("plan")
    sm = RunStateMachine(
        run_dir / "state.json",
        RunState(
            state="incomplete",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=1,
            started_at=datetime.now(UTC),
        ),
    )
    ledger = RunLedger(decided_at=datetime.now(UTC), stop_reason="runtime cap")
    result = build_result(
        run_id="abcd1234",
        run_dir=run_dir,
        status="incomplete",
        inputs=RunForgeInput(target_dir=str(tmp_path), design_doc_content="x"),
        sm=sm,
        ledger=ledger,
        started_at=datetime.now(UTC),
    )
    assert result.failure_kind == "timeout"
    assert result.message.startswith(errors._PREFIX["timeout"] + " ")


def _cancel_deps(tmp_path: Path) -> MagicMock:
    """Build cancellation deps with closeable fake runners.

    Design: the forensic cancellation test exercises the lifecycle helper
        without depending on real SDK drivers.
    Implementation: return a MagicMock with planner/generator/evaluator runners
        exposing aclose and terminate.
    Example: deps = _cancel_deps(tmp_path).
    """
    planner = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    generator = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    evaluator = MagicMock(_runner=MagicMock(aclose=AsyncMock(), terminate=MagicMock()))
    return MagicMock(
        drivers=MagicMock(planner=planner, generator=generator, evaluator=evaluator),
        run_dir=tmp_path,
        status=MagicMock(update=AsyncMock()),
        logger=MagicMock(),
    )


async def test_cancelled_path_is_forensic_only(tmp_path: Path) -> None:
    """§8.5: client-disconnect is forensic-only — failed+cancelled, no RunResult.

    Design: cancellation and runtime-timeout must never be conflated; the
        disconnect path re-raises after writing the failed/cancelled forensic
        state and returns no result to the caller.
    Implementation: drive the cancellation lifecycle path, assert the final
        state record is failed with cancelled=True, and assert CancelledError.
    Example: state.json has state="failed", cancelled=True; CancelledError raised.
    """
    sm = RunStateMachine(
        tmp_path / "state.json",
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=0,
            started_at=datetime.now(UTC),
            last_updated_at=datetime.now(UTC),
        ),
    )
    sm.transition("iter_generating", iteration=1)
    ledger = RunLedger()
    lock = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await handle_cancellation(sm, ledger, _cancel_deps(tmp_path), lock)
    assert sm.current.state == "failed"
    assert sm.current.cancelled is True
