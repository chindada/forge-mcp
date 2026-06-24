# tests/test_skills.py
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.skills import probe_claude_skills, probe_codex_skills
from tests.fakes import FakeClaudeRunner, structured


def test_codex_probe_fails_when_skill_absent(tmp_path: Path):
    """Design: §10.3 the Codex probe FAILs when a required skill is not discoverable.
    Implementation: an empty ~/.codex yields FAIL rows.
    Example: status FAIL for plan-execution.
    """
    (tmp_path / "skills").mkdir()
    probes = probe_codex_skills(codex_home=tmp_path)
    assert any(p.status == "FAIL" for p in probes)


def test_codex_probe_ok_when_present(tmp_path: Path):
    """Design: §10.3 a present skill dir yields OK.
    Implementation: create the expected skill folders.
    Example: status OK when both ids discoverable.
    """
    for sid in ("executing-plans", "frontend-design"):
        (tmp_path / "skills" / sid).mkdir(parents=True)
    probes = probe_codex_skills(codex_home=tmp_path)
    assert all(p.status != "FAIL" for p in probes)


@pytest.mark.driver
async def test_claude_probe_ok_when_init_skills_present():
    """Design: §10.3 the Claude probe is OK when the init skills cover required ids.
    Implementation: a fake whose result carries init_skills with both ids yields no FAIL.
    Example: init_skills=[writing-plans, code-review] -> all non-FAIL.
    """
    runner = FakeClaudeRunner(
        [structured({}, init_skills=["writing-plans", "code-review", "extra"])]
    )
    probes = await probe_claude_skills(
        runner=runner, required=("writing-plans", "code-review"), deadline=5.0
    )
    assert all(p.status != "FAIL" for p in probes)


@pytest.mark.driver
async def test_claude_probe_fails_when_required_skill_absent():
    """Design: §10.3 the Claude probe FAILs when a required skill is not advertised.
    Implementation: a fake whose init_skills omits code-review yields a FAIL row for it.
    Example: init_skills=[writing-plans] -> FAIL for code-review.
    """
    runner = FakeClaudeRunner([structured({}, init_skills=["writing-plans"])])
    probes = await probe_claude_skills(
        runner=runner, required=("writing-plans", "code-review"), deadline=5.0
    )
    failed = [p.label for p in probes if p.status == "FAIL"]
    assert failed == ["code-review"]
