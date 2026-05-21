"""§6.4 preflight ordering and canonicalization pins."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError

from forge_mcp.config import RunConfig
from forge_mcp.models import RunForgeInput
from forge_mcp.preflight import _canonicalize_target_dir, prepare_run


async def test_target_dir_missing_raises_invalid_params(tmp_path: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    inputs = RunForgeInput(target_dir=str(tmp_path / "missing"), design_doc_content="x")
    with pytest.raises(McpError) as exc:
        await prepare_run(inputs, RunConfig.from_env())
    assert exc.value.error.code == -32602


async def test_lock_busy_raises_server_error(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from forge_mcp.lockfile import TargetLock

    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    held = TargetLock(target_dir / ".harness" / "run.lock")
    held.acquire()
    try:
        inputs = RunForgeInput(target_dir=str(target_dir), design_doc_content="x")
        with pytest.raises(McpError) as exc:
            await prepare_run(inputs, RunConfig.from_env())
        assert exc.value.error.code == -32000
    finally:
        held.release()


async def test_abspath_not_realpath_pins_symlinked_alias(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    alias = target_dir.parent / (target_dir.name + "_alias")
    os.symlink(target_dir, alias)
    assert _canonicalize_target_dir(str(target_dir)) != _canonicalize_target_dir(str(alias))


async def test_prepare_run_does_not_write_canonical_design(target_dir: Path, monkeypatch) -> None:
    """Pin preflight validation-only design handling.

    Design: §8.1 canonicalize_design, not prepare_run, writes inputs/design.md.
    Implementation: monkeypatch slow environment probes and inspect run dir after preflight.
    Example: prepared.harness_dir / run_id / 'inputs/design.md' is absent.
    """
    from forge_mcp import preflight as preflight_mod

    monkeypatch.setattr(preflight_mod, "check_claude_cli", lambda _config: ("claude", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "check_codex", AsyncMock(return_value=("codex", "OK", "x")))
    monkeypatch.setattr(preflight_mod, "check_claude_auth", lambda: ("auth", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "probe_required_skills", AsyncMock(return_value=None))
    prepared = await prepare_run(
        RunForgeInput(target_dir=str(target_dir), design_doc_content="design"), RunConfig()
    )
    try:
        assert not (prepared.harness_dir / prepared.run_id / "inputs" / "design.md").exists()
    finally:
        prepared.lock.release()


async def test_resume_without_candidate_raises_server_error(target_dir: Path, monkeypatch) -> None:
    """Pin §H2 resume with no resumable run raises SERVER_ERROR.

    Design: an explicit resume request that finds no non-terminal run must fail
        fast and loud (Rule 8) rather than silently starting a fresh run.
    Implementation: monkeypatch find_resumable_run to None and assert McpError
        with code -32000 before any slow probe runs.
    Example: pytest.raises(McpError) around prepare_run(resume=True).
    """
    from forge_mcp import preflight as preflight_mod

    monkeypatch.setattr(preflight_mod, "find_resumable_run", lambda _harness: None)
    inputs = RunForgeInput(target_dir=str(target_dir), design_doc_content="x", resume=True)
    with pytest.raises(McpError) as exc:
        await prepare_run(inputs, RunConfig.from_env())
    assert exc.value.error.code == -32000


async def test_resume_adopts_candidate_run_id(target_dir: Path, monkeypatch) -> None:
    """Pin §H2 resume threads the candidate run id into lock acquisition.

    Design: §H19 note 4 — resume continuity requires lock.acquire to adopt the
        located run's id so all artifacts share the resumed identity.
    Implementation: stub find_resumable_run to a known ResumePoint, replace
        TargetLock with a recording double, neutralize slow probes, and assert
        acquire received adopt_run_id equal to the candidate's run id.
    Example: prepared = await prepare_run(inputs_with_resume, config).
    """
    from forge_mcp import preflight as preflight_mod
    from forge_mcp.orchestrator.resume import ResumePoint

    point = ResumePoint(
        run_id="cccccccc",
        run_dir=target_dir / ".harness" / "cccccccc",
        last_completed_iteration=4,
        start_iteration=5,
    )
    monkeypatch.setattr(preflight_mod, "find_resumable_run", lambda _harness: point)

    captured: dict[str, object] = {}

    class _RecordingLock:
        """Lock double recording adopt_run_id and exposing run_id.

        Design: preflight transfers a lock to the orchestrator; the test only
            needs to observe the adopt_run_id passed to acquire.
        Implementation: store the kwarg and expose run_id from the adopted id.
        Example: lock.acquire(adopt_run_id='cccccccc').
        """

        def __init__(self, _path) -> None:
            """Record the lock path; ignore it otherwise.

            Design: TargetLock is constructed with a run.lock path.
            Implementation: no state needed beyond run_id default.
            Example: _RecordingLock(path).
            """
            self.run_id = "cccccccc"

        def acquire(self, adopt_run_id=None) -> None:
            """Record the adopt_run_id passed by preflight.

            Design: pins that resume adopts the candidate id.
            Implementation: store the value for assertion.
            Example: lock.acquire(adopt_run_id='cccccccc').
            """
            captured["adopt_run_id"] = adopt_run_id

        def release(self) -> None:
            """No-op release for the test double.

            Design: failure paths release the lock; the test does not exercise them.
            Implementation: do nothing.
            Example: lock.release().
            """

    monkeypatch.setattr(preflight_mod, "TargetLock", _RecordingLock)
    monkeypatch.setattr(preflight_mod, "check_claude_cli", lambda _config: ("claude", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "check_codex", AsyncMock(return_value=("codex", "OK", "x")))
    monkeypatch.setattr(preflight_mod, "check_claude_auth", lambda: ("auth", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "probe_required_skills", AsyncMock(return_value=None))

    inputs = RunForgeInput(target_dir=str(target_dir), design_doc_content="x", resume=True)
    prepared = await prepare_run(inputs, RunConfig())
    assert captured["adopt_run_id"] == "cccccccc"
    assert prepared.resume_point is point
