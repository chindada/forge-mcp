"""Per-engine skill discovery probes (§10.3).

All SDK imports are lazy (inside functions) so that
``import forge_mcp.skills`` succeeds without the Claude SDK installed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from forge_mcp.drivers._claude import ClaudeRunner

# Required Codex skill ids (§10.3 candidates).
_REQUIRED_CODEX_SKILLS: tuple[str, ...] = ("executing-plans", "frontend-design")

# Required Claude skill ids (§10.3 candidates).
_REQUIRED_CLAUDE_SKILLS: tuple[str, ...] = ("writing-plans", "code-review")


@dataclass(frozen=True)
class SkillProbe:
    """Result of a single skill discovery probe (§10.3).

    Design: §10.3 callers need a uniform result type for both Claude and Codex
        probes so doctor/health-check code can iterate without branching on
        engine type.
    Implementation: frozen dataclass with three fields; status is restricted to
        the literal union "OK" | "WARN" | "FAIL" so mypy catches typos.
    Example: ``SkillProbe(label="executing-plans", status="FAIL", detail="not found")``.
    """

    label: str
    status: Literal["OK", "WARN", "FAIL"]
    detail: str


def _find_codex_skill_dir(codex_home: Path, skill_id: str) -> Path | None:
    """Locate a discoverable Codex skill directory for *skill_id* (§10.3).

    Design: §10.3 says to inspect both ``~/.codex/skills/`` and the ``~/.codex``
        plugin cache; plugin-provided skills (e.g. superpowers' ``executing-plans``)
        live only under the cache, so checking ``skills/`` alone yields a false
        negative for a skill that is genuinely installed.
    Implementation: prefer the direct ``codex_home/skills/<id>`` path; otherwise
        glob the plugin cache layout
        ``plugins/cache/<marketplace>/<plugin>/<hash>/skills/<id>`` and return the
        first directory match, else None.
    Example: with the superpowers plugin installed,
        ``_find_codex_skill_dir(home, "executing-plans")`` returns the cache path.
    """
    direct = codex_home / "skills" / skill_id
    if direct.is_dir():
        return direct
    for candidate in codex_home.glob(f"plugins/cache/*/*/*/skills/{skill_id}"):
        if candidate.is_dir():
            return candidate
    return None


def probe_codex_skills(*, codex_home: Path) -> list[SkillProbe]:
    """Inspect ``codex_home`` for each required Codex skill id (§10.3).

    Design: §10.3 this probe is pure filesystem — no live binary is needed;
        it legitimately FAILs until the operator installs the required skills,
        and that FAIL surfaces correctly to the doctor/health-check output. It
        inspects both ``~/.codex/skills/`` and the plugin cache so a
        plugin-provided skill is not reported as missing.
    Implementation: for each id in ``_REQUIRED_CODEX_SKILLS`` resolve a
        discoverable directory via ``_find_codex_skill_dir``; return OK with the
        resolved path if found, FAIL otherwise.
    Example: an empty ``codex_home`` (no skills/ entry, no plugin cache) yields
        two FAIL probes.
    """
    results: list[SkillProbe] = []
    for skill_id in _REQUIRED_CODEX_SKILLS:
        found = _find_codex_skill_dir(codex_home, skill_id)
        if found is not None:
            results.append(SkillProbe(label=skill_id, status="OK", detail=str(found)))
        else:
            results.append(
                SkillProbe(
                    label=skill_id,
                    status="FAIL",
                    detail=(
                        f"skill not found under {codex_home / 'skills'} or "
                        f"{codex_home / 'plugins' / 'cache'}"
                    ),
                )
            )
    return results


async def probe_claude_skills(
    *,
    runner: ClaudeRunner,
    required: tuple[str, ...],
    deadline: float,
) -> list[SkillProbe]:
    """Open a Claude SDK session and check that required skills are advertised (§10.3).

    Design: §10.3 the Claude side probe reads the ``SystemMessage.data["skills"]``
        list emitted at session init; if a required skill is absent the probe
        FAILs so operators know to install or enable it.
    Implementation: lazy-import the SDK inside this async function; open a
        session with the runner and read ``StructuredResult.init_skills`` (the
        seam-forwarded init ``SystemMessage.data["skills"]`` list); wrap the
        call in ``asyncio.timeout(deadline)``; a None init_skills is treated as
        "no skills discoverable" so every required id FAILs (degraded). Return
        one SkillProbe per id in *required*.
    Example: ``await probe_claude_skills(runner=fake, required=("writing-plans",),
        deadline=30.0)`` returns [SkillProbe(label="writing-plans", status=...)]
    """
    from forge_mcp.config import claude_bin  # lazy import (§10.4)
    from forge_mcp.drivers._claude import build_options  # lazy import

    options = build_options(system="list skills only", cli_path=str(claude_bin()))

    try:
        async with asyncio.timeout(deadline):
            result = await runner.run(prompt="list your skills", options=options)
    except TimeoutError:
        return [SkillProbe(label=sid, status="FAIL", detail="probe timed out") for sid in required]

    # The init SystemMessage.data["skills"] is forwarded by the Claude seam onto
    # StructuredResult.init_skills (§10.3). A None value means the session emitted
    # no skills registry, so we treat every required id as undiscoverable.
    available: list[str] = result.init_skills or []

    results: list[SkillProbe] = []
    for skill_id in required:
        # Plugin skills are advertised namespaced (e.g. "superpowers:writing-plans"),
        # so a required bare id matches either the bare form or any "<ns>:<id>" form.
        if any(s == skill_id or s.endswith(f":{skill_id}") for s in available):
            results.append(SkillProbe(label=skill_id, status="OK", detail="skill available"))
        else:
            results.append(
                SkillProbe(
                    label=skill_id,
                    status="FAIL",
                    detail=f"skill '{skill_id}' not found in session init",
                )
            )
    return results
