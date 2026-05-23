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
from forge_mcp.skills import SkillMissingError, SkillProbeTimeout


@pytest.fixture
def preflight_happy_inputs(target_dir: Path, monkeypatch):
    """Return inputs/config with all unrelated preflight checks stubbed OK.

    Design: finding 4 tests must isolate exception translation around the skill
        probe path rather than exercising real CLIs or auth.
    Implementation: monkeypatch doctor checks and TargetLock to no-op doubles.
    Example: inputs, config = preflight_happy_inputs.
    """
    from forge_mcp import preflight as preflight_mod

    class _Lock:
        """No-op lock double exposing a stable run id.

        Design: prepare_run transfers lock ownership but tests only need shape.
        Implementation: acquire/release are no-ops and run_id is fixed.
        Example: lock = _Lock(path); lock.acquire().
        """

        run_id = "abcd1234"

        def __init__(self, _path) -> None:
            """Accept the production constructor argument.

            Design: TargetLock is constructed from the harness run.lock path.
            Implementation: ignore the path in this test double.
            Example: _Lock(Path('run.lock')).
            """

        def acquire(self, adopt_run_id=None) -> None:
            """No-op acquire matching TargetLock.

            Design: the test focuses on probe errors after acquisition.
            Implementation: intentionally do nothing.
            Example: lock.acquire(adopt_run_id=None).
            """

        def release(self) -> None:
            """No-op release matching TargetLock.

            Design: prepare_run releases on post-lock failures.
            Implementation: intentionally do nothing.
            Example: lock.release().
            """

    monkeypatch.setattr(preflight_mod, "TargetLock", _Lock)
    monkeypatch.setattr(preflight_mod, "check_claude_cli", lambda _config: ("claude", "OK", "x"))
    monkeypatch.setattr(preflight_mod, "check_codex", AsyncMock(return_value=("codex", "OK", "x")))
    monkeypatch.setattr(preflight_mod, "check_claude_auth", lambda: ("auth", "OK", "x"))
    return RunForgeInput(target_dir=str(target_dir), design_doc_content="x"), RunConfig()


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


async def test_prepare_run_tags_missing_skill(monkeypatch, preflight_happy_inputs) -> None:
    """§W2 / finding 4 — a missing skill surfaces a tagged McpError.

    Design: prepare_run must translate domain skill errors to a tagged
        infra_failure McpError so nothing crosses the wire untagged.
    Implementation: force probe_required_skills to raise SkillMissingError and
        assert prepare_run raises McpError whose message carries the kind tag.
    Example: pytest asserts '[FORGE_ERR_' appears in the message.
    """
    from forge_mcp import preflight as preflight_mod

    inputs, config = preflight_happy_inputs

    async def boom(**kwargs) -> None:
        """Raise the missing-skill domain exception.

        Design: the test controls the exact post-lock failure.
        Implementation: ignore kwargs and raise SkillMissingError.
        Example: await boom() raises SkillMissingError.
        """
        raise SkillMissingError(["superpowers:writing-plans"])

    monkeypatch.setattr(preflight_mod, "probe_required_skills", boom)
    with pytest.raises(McpError) as ei:
        await prepare_run(inputs, config)
    assert "[FORGE_ERR_" in ei.value.error.message


async def test_prepare_run_tags_skill_probe_timeout(monkeypatch, preflight_happy_inputs) -> None:
    """§W2 / finding 4 — a skill-probe timeout surfaces a tagged McpError.

    Design: SkillProbeTimeout is a known domain error and must be tagged.
    Implementation: force probe_required_skills to raise SkillProbeTimeout and
        assert the tagged McpError.
    Example: pytest asserts McpError is raised with a kind tag.
    """
    from forge_mcp import preflight as preflight_mod

    inputs, config = preflight_happy_inputs

    async def boom(**kwargs) -> None:
        """Raise the timeout domain exception.

        Design: the test controls the exact post-lock failure.
        Implementation: ignore kwargs and raise SkillProbeTimeout.
        Example: await boom() raises SkillProbeTimeout.
        """
        raise SkillProbeTimeout()

    monkeypatch.setattr(preflight_mod, "probe_required_skills", boom)
    with pytest.raises(McpError) as ei:
        await prepare_run(inputs, config)
    assert "[FORGE_ERR_" in ei.value.error.message


async def test_prepare_run_catch_all_tags_unexpected(monkeypatch, preflight_happy_inputs) -> None:
    """§W2 / finding 4 — an unexpected exception is tagged by the catch-all.

    Design: defense in depth — any non-McpError from a preflight step becomes a
        tagged infra_failure rather than crossing the boundary raw.
    Implementation: force a step to raise a raw OSError and assert McpError.
    Example: pytest asserts the catch-all converts OSError to McpError.
    """
    from forge_mcp import preflight as preflight_mod

    inputs, config = preflight_happy_inputs

    async def boom(**kwargs) -> None:
        """Raise an unexpected raw exception from a post-lock step.

        Design: the catch-all should translate this non-domain exception.
        Implementation: ignore kwargs and raise OSError.
        Example: await boom() raises OSError.
        """
        raise OSError("unreadable design doc")

    monkeypatch.setattr(preflight_mod, "probe_required_skills", boom)
    with pytest.raises(McpError) as ei:
        await prepare_run(inputs, config)
    assert "[FORGE_ERR_" in ei.value.error.message


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
