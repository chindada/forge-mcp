"""§6.4 step 7 — live skill availability probe via Claude SDK init data."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

REQUIRED_SKILLS: tuple[str, ...] = ("superpowers:writing-plans",)
SKILL_PROBE_TIMEOUT_SECONDS = 120.0


class SkillMissingError(Exception):
    """Raised when a required superpowers skill is unavailable to Claude.

    Design: Rule 8 fails fast rather than letting later phases degrade when a
        required planning skill is absent.
    Implementation: carry the missing skill ids and join them in the message.
    Example: raise SkillMissingError(['superpowers:writing-plans']).
    """

    def __init__(self, missing: list[str]) -> None:
        """Format the missing-skill list onto the exception message.

        Design: doctor and preflight surface this message directly to users.
        Implementation: store the list and comma-join ids for Exception text.
        Example: SkillMissingError(['superpowers:writing-plans']).missing.
        """
        super().__init__("required skills missing: " + ", ".join(missing))
        self.missing = missing


class SkillProbeTimeout(Exception):
    """Raised when the skill probe exceeds SKILL_PROBE_TIMEOUT_SECONDS.

    Design: Rule 8 — preflight holds the target lock, so a hung Claude session
        must fail fast and loud rather than block indefinitely.
    Implementation: thin Exception carrying a fixed message.
    Example: raise SkillProbeTimeout().
    """

    def __init__(self) -> None:
        """Format the timeout message.

        Design: operators need to know the probe, not the run, timed out.
        Implementation: pass a fixed string to Exception.
        Example: SkillProbeTimeout().
        """
        super().__init__(
            f"skill probe exceeded {SKILL_PROBE_TIMEOUT_SECONDS}s without an init message"
        )


def _extract_skills(messages: list[Any]) -> list[str] | None:
    """Return the init SystemMessage's data['skills'] list, or None if absent.

    Design: A2 reads ground-truth loaded skills from the CLI's structured init
        data; a missing skills field means the probe cannot verify.
    Implementation: scan for subtype=='init', read data['skills'], and return
        None when no init carries it.
    Example: _extract_skills(messages) == ['superpowers:writing-plans'].
    """
    for msg in messages:
        subtype = msg.get("subtype") if isinstance(msg, dict) else getattr(msg, "subtype", None)
        if subtype != "init":
            continue
        data = msg.get("data") if isinstance(msg, dict) else getattr(msg, "data", None)
        if isinstance(data, dict) and "skills" in data:
            skills = data["skills"]
            if isinstance(skills, list):
                return [str(skill) for skill in skills]
    return None


def _is_init_with_skills(msg: Any) -> bool:
    """Return True for an init SystemMessage that exposes a 'skills' field (A2).

    Design: §A2 breaks out of the stream the moment the init message carrying
        loaded skills is read, rather than waiting out the throwaway turn.
    Implementation: detect subtype=='init' and a 'skills' key in data, handling
        both dict-shaped and attribute-shaped messages.
    Example: _is_init_with_skills(SystemMessage(subtype='init', data={'skills': []})).
    """
    subtype = msg.get("subtype") if isinstance(msg, dict) else getattr(msg, "subtype", None)
    if subtype != "init":
        return False
    data = msg.get("data") if isinstance(msg, dict) else getattr(msg, "data", None)
    return isinstance(data, dict) and "skills" in data


async def probe_required_skills(*, runner: Any, claude_cli_path: Path | None) -> None:
    """Verify REQUIRED_SKILLS are loaded by reading init SystemMessage data (A2).

    Design: §6.4 step 7 exercises the same setting_sources path later Claude
        phases use; A2 replaces model self-report with deterministic init data.
    Implementation: open a session with build_options, issue a minimal query
        under wait_for, break on the init message via a stop predicate, extract
        data['skills'], and raise on unverifiable/missing.
    Example: await probe_required_skills(runner=r, claude_cli_path=None).
    """
    from .drivers._claude import CLAUDE_SETTING_SOURCES, build_options

    options = build_options(
        setting_sources=CLAUDE_SETTING_SOURCES,
        disallowed_tools=("Edit", "Write"),
        cli_path=claude_cli_path,
    )
    try:
        turn = await asyncio.wait_for(
            runner.run_with_messages(
                prompt="Respond with 'ok'.",
                options=options,
                system="",
                stop=_is_init_with_skills,  # §A2 — break as soon as init skills are read.
            ),
            timeout=SKILL_PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise SkillProbeTimeout() from exc
    skills = _extract_skills(turn.messages)
    if skills is None:
        raise SkillMissingError(list(REQUIRED_SKILLS))
    missing = [skill for skill in REQUIRED_SKILLS if skill not in set(skills)]
    if missing:
        raise SkillMissingError(missing)
