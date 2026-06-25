"""Git-mutation deny matcher (§7)."""

from __future__ import annotations

import re

# Verbs that constitute a git mutation when found after 'git' in a command.
_MUTATING_VERBS = r"commit|push|branch|tag|rebase|reset|worktree"
_DENY_PATTERN = re.compile(r"\bgit\b.*?(?:" + _MUTATING_VERBS + r"|checkout\s+-b)")


def git_deny_matches(command: str) -> bool:
    """Return True if *command* appears to invoke a git-mutating operation.

    Design: §7 the only git guardrail that survives is this best-effort deny
        matcher, wired into the Claude PreToolUse Bash hook; git-state no longer
        gates completion, so there is no snapshot/diff backstop behind it.
    Implementation: lower-case the full command string (handles &&-chained
        forms) then regex-search for 'git' followed anywhere in the same string
        by a mutating verb or 'checkout -b'. This is intentionally best-effort
        — it will not catch obfuscated forms.
    Example: git_deny_matches('ls && git commit -m x') is True;
        git_deny_matches('git status') is False.
    """
    return bool(_DENY_PATTERN.search(command.lower()))
