"""§6.4 step 7 — live skill availability probe via the Claude SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REQUIRED_SKILLS: tuple[str, ...] = ("superpowers:writing-plans",)

SKILL_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["available"],
    "properties": {"available": {"type": "array", "items": {"type": "string"}}},
}


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


async def probe_required_skills(*, runner: Any, claude_cli_path: Path | None) -> None:
    """Run the schema-bearing skill probe against the Claude SDK.

    Design: §6.4 step 7 exercises the same setting_sources path that later
        Claude phases use, ensuring missing skills fail before a run starts.
    Implementation: request a structured `available` list and compare against
        REQUIRED_SKILLS; raise SkillMissingError for any missing id.
    Example: await probe_required_skills(runner=r, claude_cli_path=None).
    """
    from .drivers._claude import CLAUDE_SETTING_SOURCES, build_options

    prompt = (
        'Return a JSON object {"available": [...]} listing the IDs of every '
        "superpowers skill currently loaded in this session. Do not invoke any skill."
    )
    options = build_options(
        setting_sources=CLAUDE_SETTING_SOURCES,
        output_format=SKILL_PROBE_SCHEMA,
        disallowed_tools=("Edit", "Write"),
        cli_path=claude_cli_path,
    )
    result = await runner.run(prompt=prompt, options=options, system="")
    available = set((result.structured or {}).get("available", []))
    missing = [skill for skill in REQUIRED_SKILLS if skill not in available]
    if missing:
        raise SkillMissingError(missing)
