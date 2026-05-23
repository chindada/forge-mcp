"""A2 — skill probe reads the init SystemMessage data['skills'] list."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class _ProbeRunner:
    """Minimal ClaudeRunner double exposing run_with_messages.

    Design: A2 opens a live session and reads init SystemMessage data['skills'];
        the double returns a scripted init message.
    Implementation: run_with_messages returns a ClaudeTurn-like object whose
        messages include one init SystemMessage carrying data['skills'].
    Example: _ProbeRunner(skills=['superpowers:writing-plans']).
    """

    def __init__(
        self, *, skills: list[str], has_skills_field: bool = True, delay: float = 0.0
    ) -> None:
        """Store the scripted probe behavior.

        Design: tests vary skills presence, field absence, and latency.
        Implementation: assign constructor values to private attributes.
        Example: _ProbeRunner(skills=[], has_skills_field=False).
        """
        self._skills = skills
        self._has = has_skills_field
        self._delay = delay
        self.last_session_id = None
        self.consumed = 0

    async def run_with_messages(
        self, *, prompt: str, options: Any, system: str, stop: Any = None
    ) -> Any:
        """Return a ClaudeTurn-like result, honoring an optional stop predicate.

        Design: A2 passes a stop predicate so the probe breaks on the init
            message; the double models init followed by a trailing message and
            stops pulling once stop fires.
        Implementation: optionally sleep, then iterate a scripted stream,
            counting consumed messages and breaking when stop(msg) is truthy.
        Example: await runner.run_with_messages(prompt='x', options=o, system='', stop=p).
        """
        from claude_agent_sdk import SystemMessage

        from forge_mcp.drivers._claude import ClaudeTurn, StructuredResult

        _ = (prompt, options, system)
        if self._delay:
            await asyncio.sleep(self._delay)
        data = {"skills": list(self._skills)} if self._has else {}
        init = SystemMessage(subtype="init", data=data)
        trailing = SystemMessage(subtype="result", data={})
        collected: list[Any] = []
        for msg in (init, trailing):
            self.consumed += 1
            collected.append(msg)
            if stop is not None and stop(msg):
                break
        return ClaudeTurn(result=StructuredResult(structured=None, text=""), messages=collected)

    async def run(self, *, prompt: str, options: Any, system: str) -> Any:
        """Delegate to run_with_messages and return its result.

        Design: keeps this double compatible with the ClaudeRunner protocol.
        Implementation: await run_with_messages and return .result.
        Example: await runner.run(prompt='x', options=o, system='').
        """
        return (await self.run_with_messages(prompt=prompt, options=options, system=system)).result

    async def aclose(self) -> None:
        """No-op close for protocol compatibility.

        Design: lifecycle may close runners after probes.
        Implementation: return None.
        Example: await runner.aclose().
        """
        return None


async def test_probe_passes_when_required_skills_present() -> None:
    """Pin A2 — no raise when REQUIRED_SKILLS subset of init skills.

    Design: REQUIRED_SKILLS must be a subset of data['skills'].
    Implementation: pass a runner whose init lists the required skill.
    Example: pytest tests/test_skills.py -k present -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.skills import REQUIRED_SKILLS, probe_required_skills

    runner = _ProbeRunner(skills=list(REQUIRED_SKILLS) + ["other:thing"])
    await probe_required_skills(runner=runner, claude_cli_path=None)


async def test_probe_raises_skill_missing_when_absent() -> None:
    """Pin A2 — SkillMissingError when a required skill is absent.

    Design: a missing required skill must hard-fail preflight.
    Implementation: pass a runner whose init skills omit the required id.
    Example: pytest tests/test_skills.py -k missing -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.skills import SkillMissingError, probe_required_skills

    runner = _ProbeRunner(skills=["unrelated:skill"])
    with pytest.raises(SkillMissingError):
        await probe_required_skills(runner=runner, claude_cli_path=None)


async def test_probe_raises_when_init_lacks_skills_field() -> None:
    """Pin A2 — cannot-verify hard fail when init omits 'skills'.

    Design: if init lacks skills the probe cannot verify and must raise.
    Implementation: pass a runner whose init data dict omits skills.
    Example: pytest tests/test_skills.py -k lacks_skills -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.skills import SkillMissingError, probe_required_skills

    runner = _ProbeRunner(skills=[], has_skills_field=False)
    with pytest.raises(SkillMissingError):
        await probe_required_skills(runner=runner, claude_cli_path=None)


async def test_probe_times_out(monkeypatch) -> None:
    """Pin A2 — SkillProbeTimeout when the probe exceeds its budget.

    Design: preflight holds the target lock, so the probe is bounded by wait_for.
    Implementation: shrink timeout and use a slow runner.
    Example: pytest tests/test_skills.py -k times_out -v.
    """
    pytest.importorskip("claude_agent_sdk")
    import forge_mcp.skills as skills_mod
    from forge_mcp.skills import SkillProbeTimeout, probe_required_skills

    monkeypatch.setattr(skills_mod, "SKILL_PROBE_TIMEOUT_SECONDS", 0.01)
    runner = _ProbeRunner(skills=list(skills_mod.REQUIRED_SKILLS), delay=0.5)
    with pytest.raises(SkillProbeTimeout):
        await probe_required_skills(runner=runner, claude_cli_path=None)


async def test_probe_stops_after_init_message() -> None:
    """Pin A2 — probe breaks out of the stream once the init skills are read.

    Design: §A2 requires not waiting out the throwaway turn; the probe must
        stop after the init SystemMessage carrying data['skills'].
    Implementation: a runner that yields init then a trailing message records
        consumption; after the probe runs only the init was consumed.
    Example: pytest tests/test_skills.py -k stops_after_init -v.
    """
    pytest.importorskip("claude_agent_sdk")
    from forge_mcp.skills import REQUIRED_SKILLS, probe_required_skills

    runner = _ProbeRunner(skills=list(REQUIRED_SKILLS))
    await probe_required_skills(runner=runner, claude_cli_path=None)
    assert runner.consumed == 1  # §A2 — trailing message never consumed.
