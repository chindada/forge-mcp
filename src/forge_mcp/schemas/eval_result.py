"""EvalResult JSON schema for Claude structured output (§7)."""

from __future__ import annotations

from typing import Any

EVAL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["no_gaps", "gaps", "summary"],
    "properties": {
        "no_gaps": {"type": "boolean"},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "severity",
                    "design_doc_section",
                    "current_state",
                    "expected_state",
                    "suggested_fix",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "severity": {"type": "string"},
                    "design_doc_section": {"type": "string"},
                    "current_state": {"type": "string"},
                    "expected_state": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}
