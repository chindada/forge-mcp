"""TriageResult JSON schema for Claude structured output (§7)."""

from __future__ import annotations

from typing import Any

TRIAGE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["triages", "summary"],
    "properties": {
        "triages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "gap_title",
                    "design_fault",
                    "fault_kind",
                    "cited_sections",
                    "explanation",
                ],
                "properties": {
                    "gap_title": {"type": "string", "minLength": 1},
                    "design_fault": {"type": "boolean"},
                    "fault_kind": {
                        "type": ["string", "null"],
                        "enum": [
                            "contradiction",
                            "infeasibility",
                            "deprecated_dependency",
                            "ambiguity",
                            "other",
                            None,
                        ],
                    },
                    "cited_sections": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}
