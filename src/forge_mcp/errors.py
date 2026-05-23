"""Typed domain errors for forge-mcp.

`LockBusy` lives in `lockfile.py`; pre-run failures use `McpError` from the
MCP SDK. This module only houses errors that cross internal seams.
"""

from __future__ import annotations

from typing import Final, Literal

# §W1 — closed six-kind taxonomy. Adding another kind requires a brief update.
FailureKind = Literal[
    "invalid_params",
    "infra_failure",
    "auth",
    "lock_held",
    "timeout",
    "cancelled",
]

_PREFIX: Final[dict[FailureKind, str]] = {
    "invalid_params": "[FORGE_ERR_INVALID_PARAMS]",
    "infra_failure": "[FORGE_ERR_INFRA_FAILURE]",
    "auth": "[FORGE_ERR_AUTH]",
    "lock_held": "[FORGE_ERR_LOCK_HELD]",
    "timeout": "[FORGE_ERR_TIMEOUT]",
    "cancelled": "[FORGE_ERR_CANCELLED]",
}
PREFIX_START: Final[str] = "[" + "FORGE_ERR_"


def tag(kind: FailureKind, body: str) -> str:
    """Construct the verbatim wire message text for an error category.

    Design: §W2 makes the bracketed prefix stable host-visible API, and this
        helper is the only construction site to prevent drift across modules.
    Implementation: prepend the stored literal prefix, one ASCII space, and the
        unchanged message body supplied by the caller.
    Example: tag("timeout", "runtime cap exceeded").
    """
    return f"{_PREFIX[kind]} {body}"


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

        Design: callers (the `_parse_and_validate` helper, §10.3) need both the
            unparsed text and the parse-error message for the terminal-fail log
            when both schema-retry attempts fail.
        Implementation: forward `reason` to the base Exception message via
            super().__init__, and attach `raw`/`reason` as plain attributes.
        Example: raise OutputSchemaError(raw='{bad', reason='invalid JSON').
        """
        super().__init__(reason)
        self.raw = raw
        self.reason = reason


class PlannerNoOutputError(RuntimeError):
    """Raised when planner recovery still does not produce plan.md (§H).

    Design: a missing planner handoff artifact is an in-run infrastructure
        failure, so the orchestrator should return RunResult(status='failed').
    Implementation: plain RuntimeError subclass used only for error_class
        discrimination; failure_kind remains the closed infra_failure mapping.
    Example: raise PlannerNoOutputError('planner produced no plan.md').
    """
