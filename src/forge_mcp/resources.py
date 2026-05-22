"""§R1.3 / §R2 / §R3 — URI helpers, allowlist, active-run registry.

This module is the only stdlib-only leaf in forge-mcp. It MUST NOT import
mcp.*, forge_mcp.drivers, forge_mcp.orchestrator, or logging. See §R5.1
and the §R9.1 module-isolation pin in tests/test_resources.py.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

_HARNESS_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_ITERATION_DIR_RE = re.compile(r"^iteration-[1-9]\d*$")


@dataclass(frozen=True)
class _ArtifactPattern:
    """One allowlisted artifact (§R2.3).

    Design: §R2 captures the static allowlist as data so listing, reading,
        and tests iterate uniformly without scattered string literals.
    Implementation: frozen dataclass; `regex` matches the full subpath when
        provided, else `subpath` is the exact match; `mime` is the MIME type.
    Example: _ArtifactPattern(subpath="plan/plan.md", regex=None,
        mime="text/markdown").
    """

    subpath: str | None
    regex: re.Pattern[str] | None
    mime: str


_ALLOWED_ARTIFACTS: tuple[_ArtifactPattern, ...] = (
    _ArtifactPattern("inputs/design.md", None, "text/markdown"),
    _ArtifactPattern("inputs/git-state.txt", None, "text/plain"),
    _ArtifactPattern("inputs/git-uncommitted.txt", None, "text/plain"),
    _ArtifactPattern("plan/plan.md", None, "text/markdown"),
    _ArtifactPattern("plan/sessions.json", None, "application/json"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/contract\.md$"), "text/markdown"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/summary\.md$"), "text/markdown"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/eval\.json$"), "application/json"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/eval\.md$"), "text/markdown"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/triage\.json$"), "application/json"),
    _ArtifactPattern(
        None, re.compile(r"^iteration-([1-9]\d*)/sessions\.json$"), "application/json"
    ),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/git-violation\.txt$"), "text/plain"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/verify\.txt$"), "text/plain"),
    _ArtifactPattern("state.json", None, "application/json"),
    _ArtifactPattern("status.log", None, "application/x-ndjson"),
    _ArtifactPattern("unresolved-gaps-overflow.md", None, "text/markdown"),
    _ArtifactPattern("design-flaw-gaps-overflow.md", None, "text/markdown"),
)


def match_artifact(subpath: str) -> _ArtifactPattern | None:
    """Return the allowlist row matching subpath, else None (§R2.3).

    Design: §R2.3 — single source for the "is this subpath served?" decision.
        Returning None lets callers decide whether to skip listing or emit a
        resource-not-found error while reading.
    Implementation: iterate _ALLOWED_ARTIFACTS; exact-match wins by string
        equality; otherwise return the first regex full-match. Patterns are
        disjoint so iteration order is immaterial.
    Example: match_artifact("iteration-3/eval.md") is not None.
    """
    for pattern in _ALLOWED_ARTIFACTS:
        if pattern.subpath is not None and pattern.subpath == subpath:
            return pattern
        if pattern.regex is not None and pattern.regex.fullmatch(subpath):
            return pattern
    return None


def encode_uri(harness_token: str, run_id: str, subpath: str) -> str:
    """Format a forge:// URI from its three components (§R1.2).

    Design: §R1.2 — encoding is total and pure so it can be called from
        orchestrator/result.py without touching any state. Allowlisted
        subpaths are restricted to safe characters by construction (R-Inv 3).
    Implementation: f-string concatenation with input shape asserts; bad
        inputs are a programming error, not a runtime miss.
    Example: encode_uri("aBcDeFgHiJkL", "12345678", "plan/plan.md") ==
        "forge://aBcDeFgHiJkL/12345678/plan/plan.md".
    """
    assert _HARNESS_TOKEN_RE.match(harness_token), harness_token
    assert _RUN_ID_RE.match(run_id), run_id
    return f"forge://{harness_token}/{run_id}/{subpath}"


def decode_uri(uri: str) -> tuple[str, str, str]:
    """Parse a forge:// URI into (harness_token, run_id, subpath) (§R1.2).

    Design: §R1.3 — inverse of encode_uri with strict validation so malformed
        URIs never reach the filesystem layer. Non-forge schemes, missing
        segments, and non-empty query/fragment all raise ValueError.
    Implementation: urlsplit, validate scheme/empty query/empty fragment,
        netloc as token, path.lstrip("/").split("/"), then unpack run_id and
        re-join the rest as subpath. MUST raise ValueError, not assert.
    Example: decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md") ==
        ("aBcDeFgHiJkL", "12345678", "plan/plan.md").
    """
    parts = urllib.parse.urlsplit(uri)
    if parts.scheme != "forge":
        raise ValueError(f"not a forge:// URI: {uri!r}")
    if parts.query:
        raise ValueError(f"forge URI must not have query: {uri!r}")
    if parts.fragment:
        raise ValueError(f"forge URI must not have fragment: {uri!r}")
    harness_token = parts.netloc
    if not _HARNESS_TOKEN_RE.match(harness_token):
        raise ValueError(f"bad harness_token: {harness_token!r}")
    path_parts = parts.path.lstrip("/").split("/")
    if len(path_parts) < 2 or not path_parts[0] or not path_parts[1]:
        raise ValueError(f"forge URI missing run_id or subpath: {uri!r}")
    run_id, *rest = path_parts
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"bad run_id: {run_id!r}")
    return harness_token, run_id, "/".join(rest)


def compute_harness_token(harness_dir: Path) -> str:
    """Derive the 12-char stable token for a harness directory (§R1.2).

    Design: §R1.2 — sha256(os.path.abspath(harness_dir)) then base64url-encode
        and truncate to 12 chars. Stable across processes because the hash
        input is the canonicalized path string, not inode or mtime.
    Implementation: hashlib.sha256(abspath.encode()) digest, urlsafe_b64encode,
        strip "=" padding, slice to 12 chars. The result matches
        ^[A-Za-z0-9_-]{12}$.
    Example: compute_harness_token(Path("/repo/.harness")) returns a 12-char
        string matching ^[A-Za-z0-9_-]{12}$.
    """
    digest = hashlib.sha256(os.path.abspath(str(harness_dir)).encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()[:12]


@dataclass(frozen=True)
class _ResourceScope:
    """One discoverable harness/run pair (§R3.1).

    Design: §R3 — list_resources enumerates active runs without looking inside
        any Orchestrator instance. Scope carries the harness_token computed
        once in the outer run_forge handler alongside the dir and run_id.
    Implementation: frozen, hashable, immutable; harness_token is supplied by
        the caller, not recomputed in the constructor.
    Example: _ResourceScope(run_id="12345678", harness_dir=Path("/r/.harness"),
        harness_token="aBcDeFgHiJkL").
    """

    run_id: str
    harness_dir: Path
    harness_token: str


_ACTIVE_RUNS: dict[tuple[str, str], _ResourceScope] = {}


def register_active_run(scope: _ResourceScope) -> None:
    """Add an in-flight run to the discovery registry (§R3.1).

    Design: §R3.1 — orchestrators call this as the first statement inside the
        engine's outer try so register-time exceptions are captured by the
        same finally that releases the lock.
    Implementation: assert key not already present; assign atomically at the
        Python-dict level so concurrent list_resources reads see consistent
        snapshots.
    Example: register_active_run(_ResourceScope("12345678", Path("/r/.harness"),
        "aBcDeFgHiJkL")).
    """
    key = (scope.harness_token, scope.run_id)
    assert key not in _ACTIVE_RUNS, f"double-register: {key!r}"
    _ACTIVE_RUNS[key] = scope


def deregister_active_run(harness_token: str, run_id: str) -> None:
    """Remove a run from the registry on terminate (§R3.1).

    Design: §R3.1 — called from engine.run's finally so the registry stays
        consistent on cancellation/failure paths. Idempotent defensive
        double-calls are no-ops.
    Implementation: dict.pop with default None.
    Example: deregister_active_run("aBcDeFgHiJkL", "12345678").
    """
    _ACTIVE_RUNS.pop((harness_token, run_id), None)


def list_active_runs() -> list[_ResourceScope]:
    """Snapshot the current active-run registry (§R3.1).

    Design: §R3.1 — list_resources reads through this helper so snapshot
        semantics are explicit. Mid-read mutations affect the next call, not
        this one. Read-only; never mutates.
    Implementation: list(_ACTIVE_RUNS.values()); the list() over dict-values
        is atomic enough for snapshot semantics under the CPython GIL.
    Example: scopes = list_active_runs().
    """
    return list(_ACTIVE_RUNS.values())


def resolve_harness_dir(harness_token: str, harness_root_tokens: dict[str, Path]) -> Path | None:
    """Resolve a harness_token to its harness_dir from registry or config (§R3.1).

    Design: §R1.1 — single lookup path so read_resource treats active runs and
        configured roots uniformly. Active runs win on collision.
    Implementation: scan list_active_runs() for matching harness_token first;
        on miss, look up the token in harness_root_tokens. Return None when
        neither matches.
    Example: harness_dir = resolve_harness_dir("aBcDeFgHiJkL", token_map).
    """
    for scope in list_active_runs():
        if scope.harness_token == harness_token:
            return scope.harness_dir
    return harness_root_tokens.get(harness_token)


def _append_if_file(
    out: list[tuple[str, str, str, str]],
    scope: _ResourceScope,
    path: Path,
    subpath: str,
) -> None:
    """Append one resource row if the file exists and is allowlisted (§R3.1).

    Design: §R3.1 scope expansion should keep file-test and allowlist behavior
        identical across top-level, inputs, plan, and iteration locations.
    Implementation: check Path.is_file(), then match_artifact(), then append a
        tuple shaped for server.py's MCP wrapper layer.
    Example: _append_if_file(rows, scope, run_root / "state.json", "state.json").
    """
    if not path.is_file():
        return
    pattern = match_artifact(subpath)
    if pattern is None:
        return
    out.append(
        (encode_uri(scope.harness_token, scope.run_id, subpath), path.name, pattern.mime, subpath)
    )


def expand_scope_to_resources(scope: _ResourceScope) -> list[tuple[str, str, str, str]]:
    """Walk a scope's harness_dir/run_id/ and emit existing allowlisted artifacts (§R3.1).

    Design: §R5.1 — resources.py stays stdlib-only by returning plain tuples;
        server.py wraps each into an MCP-typed Resource. The walk follows the
        same allowlist that read_resource validates against (R-Inv 3).
    Implementation: walk four locations under scope.harness_dir/scope.run_id
        non-recursively: top-level, inputs/, plan/, and strict iteration-<N>/
        directories. OSError during the walk propagates to the handler.
    Example: rows = expand_scope_to_resources(_ResourceScope("12345678",
        Path("/r/.harness"), "aBcDeFgHiJkL")).
    """
    run_root = scope.harness_dir / scope.run_id
    out: list[tuple[str, str, str, str]] = []

    for name in (
        "state.json",
        "status.log",
        "unresolved-gaps-overflow.md",
        "design-flaw-gaps-overflow.md",
    ):
        _append_if_file(out, scope, run_root / name, name)

    inputs_dir = run_root / "inputs"
    if inputs_dir.is_dir():
        for name in ("design.md", "git-state.txt", "git-uncommitted.txt"):
            _append_if_file(out, scope, inputs_dir / name, f"inputs/{name}")

    plan_dir = run_root / "plan"
    if plan_dir.is_dir():
        for name in ("plan.md", "sessions.json"):
            _append_if_file(out, scope, plan_dir / name, f"plan/{name}")

    if run_root.is_dir():
        for child in run_root.iterdir():
            if not child.is_dir() or not _ITERATION_DIR_RE.match(child.name):
                continue
            for name in (
                "contract.md",
                "summary.md",
                "eval.json",
                "eval.md",
                "triage.json",
                "sessions.json",
                "git-violation.txt",
                "verify.txt",
            ):
                _append_if_file(out, scope, child / name, f"{child.name}/{name}")
    return out
