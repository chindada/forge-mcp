"""Typed domain errors for forge-mcp.

`LockBusy` lives in `lockfile.py`; pre-run failures use `McpError` from the
MCP SDK. This module only houses errors that cross internal seams.
"""

from __future__ import annotations


class OutputSchemaError(Exception):
    """Raised when a driver fails to parse the model's structured output.

    Design: SDK does not retry on schema mismatch; the orchestrator retries
        once via `with_schema_retry` (§11.6). Carry the raw payload for the
        terminal-fail log when both attempts fail.
    Implementation: stores `raw` (unparsed text) and `reason` (parse error);
        stringification embeds `reason` through the base Exception message.
    Example: raise OutputSchemaError(raw=text, reason='not valid JSON').
    """

    def __init__(self, *, raw: str, reason: str) -> None:
        """Store the raw payload and parse reason on the exception.

        Design: callers (the `_run_and_parse_json` helper, §10.3) need both the
            unparsed text and the parse-error message for the terminal-fail log
            when both schema-retry attempts fail.
        Implementation: forward `reason` to the base Exception message via
            super().__init__, and attach `raw`/`reason` as plain attributes.
        Example: raise OutputSchemaError(raw='{bad', reason='invalid JSON').
        """
        super().__init__(reason)
        self.raw = raw
        self.reason = reason
