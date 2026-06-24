# tests/test_phases.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.artifacts import RunLayout
from forge_mcp.drivers._codex import CodexEvent
from forge_mcp.gitguard import capture_state
from forge_mcp.models import Plan
from forge_mcp.orchestrator.phases import run_plan_loop
from tests.fakes import FakeClaudeRunner, FakeCodexRunner, structured


def _git(repo: Path, *args: str) -> None:
    """Run a git command in *repo*, raising on non-zero exit.

    Design: test helper to set up and mutate a temp repo for the §9 backstop test
        without repeating boilerplate.
    Implementation: subprocess.run with check=True; capture_output suppresses
        noise in test output.
    Example: _git(repo, 'init', '-q') initialises a new repo.
    """
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _plan(**kw):
    """Build a Plan fixture with sensible defaults, overridable via kwargs.

    Design: test helper to reduce boilerplate when constructing plans for the
        per-plan loop tests.
    Implementation: start from a default backend plan dict, apply overrides, and
        construct a Plan.
    Example: _plan(verification_command='false') yields a plan with that command.
    """
    base = dict(
        id="p1",
        depends_on=[],
        surface="backend",
        file_scope=["**"],
        verification_command=None,
        body="do X",
    )
    base.update(kw)
    return Plan(**base)  # type: ignore[arg-type]


@pytest.mark.driver
async def test_clean_iteration_completes(tmp_path: Path):
    """Design: §6.5 no gaps + no verify cmd + no git violation -> completed.
    Implementation: evaluator returns no_gaps; loop completes in one iteration.
    Example: terminal_state 'done'.
    """
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    claude = FakeClaudeRunner([structured({"no_gaps": True, "summary": "ok", "gaps": []})])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=RunLayout.for_run(tmp_path),
        plan=_plan(),
        sandbox=sandbox,
        spec_text="s",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=3,
        base_git_state=None,
    )
    assert res.terminal_state == "done"


@pytest.mark.driver
async def test_verify_failure_synthesizes_blocking_gap(tmp_path: Path):
    """Design: §6.5 a failing verification synthesizes a non-demotable gap, blocks completion.
    Implementation: verification_command 'false' with no eval gaps -> not done.
    Example: terminal_state is 'incomplete' (cap/non-progress) with a verify gap.
    """
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    claude = FakeClaudeRunner([structured({"no_gaps": True, "summary": "ok", "gaps": []})] * 3)
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})] * 3)
    res = await run_plan_loop(
        layout=RunLayout.for_run(tmp_path),
        plan=_plan(verification_command="false"),
        sandbox=sandbox,
        spec_text="s",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
        base_git_state=None,
    )
    assert res.terminal_state != "done"
    assert any(
        "verif" in g.title.lower() or g.design_doc_section == "§6.5" for g in res.synthesized
    )


@pytest.mark.driver
async def test_git_mutation_synthesizes_blocking_gap(tmp_path: Path):
    """Design: §9 a git-state mutation on the real surface synthesizes a §9 gap, blocks done.
    Implementation: base captured after one commit; a second commit moves the surface
        so base != end; run with git_surface=repo and a clean evaluator -> not done.
    Example: terminal_state != 'done' and a synthesized gap has design_doc_section '§9'.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "one")
    base = capture_state(repo)
    # A second commit simulates a stage mutating the real git surface.
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-qam", "two")

    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    claude = FakeClaudeRunner([structured({"no_gaps": True, "summary": "ok", "gaps": []})])
    codex = FakeCodexRunner([CodexEvent(kind="turn.completed", payload={})])
    res = await run_plan_loop(
        layout=RunLayout.for_run(tmp_path / "run"),
        plan=_plan(),
        sandbox=sandbox,
        spec_text="s",
        claude_runner=claude,
        codex_runner=codex,
        schemas={"eval": {"type": "object"}, "triage": {"type": "object"}},
        max_iterations=1,
        base_git_state=base,
        git_surface=repo,
    )
    assert res.terminal_state != "done"
    assert any(g.design_doc_section == "§9" for g in res.synthesized)
