"""Schema envelope helpers (§8.1): wrap bare JSON Schema in the SDK output_format envelope."""

from __future__ import annotations


def envelope(bare: dict) -> dict:
    """Wrap *bare* in the SDK output_format envelope, idempotent.

    Design: §8.1 ClaudeAgentOptions.output_format must be
        ``{"type": "json_schema", "schema": <bare>}``. Double-wrapping a
        schema that is already enveloped is a no-op so callers don't need
        to track whether they've already wrapped.
    Implementation: check whether *bare* already carries
        ``type == "json_schema"`` AND a ``"schema"`` key; if so return it
        unchanged, otherwise wrap it.
    Example: ``envelope({"type": "object"})`` →
        ``{"type": "json_schema", "schema": {"type": "object"}}``;
        calling again on the result returns the result unchanged.
    """
    if bare.get("type") == "json_schema" and "schema" in bare:
        return bare
    return {"type": "json_schema", "schema": bare}
