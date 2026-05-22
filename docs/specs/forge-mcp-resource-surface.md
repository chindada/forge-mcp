# forge-mcp — Resource Surface

**What:** A normative enhancement brief that adds a **standard MCP resource
surface** so a host can call `list_resources` / `read_resource` to read a run's
on-disk artifacts (`plan.md`, `eval.md`, `state.json`, `sessions.json`,
`status.log`, …) over the MCP transport itself instead of filesystem-poking.
Companion to `forge-mcp-design.md` and the two prior briefs
(`forge-mcp-long-run-hardening.md`, `forge-mcp-long-run-continuity.md`): the
base doc wins on anything it already specifies; the hardening doc wins on
anything it specifies; the continuity doc wins on anything it specifies; this
brief only adds new behavior in its own `§R*`, `R-Invariant N`, and
`R-Decision N` namespaces so code comments can cite it unambiguously (e.g.
`# §R1 list_resources`, `# §R-Inv 3 allowlist not denylist`).

**Status:** Design complete. The base implementation, the H1–H10 hardening,
and the C1–C2 continuity work are all shipped and CI-green. This brief is the
implementation contract for the next increment. It assumes the §C1.4 Path B
low-level `Server` migration is the registration venue — the handlers are
registered on the low-level `Server`, not on FastMCP's `@mcp.tool`-derived
surface.

**Audience:** The implementing agent. Precision over prose; every interface
sketch is normative and carries the Rule 21 three-section docstring
(`Design:` / `Implementation:` / `Example:`).

**Scope (1 enhancement, chosen explicitly):** R1 `list_resources` +
`read_resource` MCP server surface for on-disk run artifacts. **Explicitly
out of scope:** `subscribe_resource` and the `notifications/resources/updated`
emit path (deferred — §C1.5's `task.update_status` already covers live status
fan-out, and a subscribe-on-artifact layer is unwarranted until a real
consumer exists); any reader-of-resources by the orchestrator itself (the
surface is host-facing only — `R-Inv 2`).

---

## R0. Thesis, north star, and what does *not* change

### R0.1 The gap this brief closes

`RunResult.artifacts` returns *filesystem paths*. To read a run's artifacts a
host must filesystem-poke the machine the server runs on. Three places this
becomes load-bearing:

- **Once a run detaches from the connection** (§C1's task mode), the only
  visibility into in-flight `plan.md`/`eval.md`/`sessions.json` is the
  filesystem. A task-mode poller that wants to render progress beyond
  `task.update_status` lines has no protocol-level way to do it.
- **If forge-mcp is ever deployed remote** (§C10's HTTP-transport future-work
  note), filesystem access disappears entirely — MCP resources become the only
  channel for artifact bodies.
- **Standard host UX** for an MCP server is "what's available?" via
  `list_resources`. Every reference server in the MCP Python SDK exposes its
  content this way (context7: `examples/servers/simple-resource/`,
  `examples/servers/simple-pagination/`). forge-mcp is non-standard in
  returning paths-only.

context7 (queried at design time against `/modelcontextprotocol/python-sdk`
v1.12.4) confirms the surface: `@server.list_resources()` returns a
`ListResourcesResult` of `Resource(uri, name, mimeType, …)` objects,
paginated via `PaginatedRequestParams` / `nextCursor`;
`@server.read_resource()` returns content blocks (typically
`TextResourceContents` for our markdown/json/log artifacts); resource
subscription is opt-in via `subscribe=true` in the resources capability and is
explicitly opt-OUT in this round (`R-Decision 3`).

### R0.2 The binding constraint (the north star is preserved)

The surface is **read-only and post-write**:

- Resource handlers never call back into a driver / SDK, never thread an
  artifact's contents into a phase prompt, never mutate `state.json`. They are
  pure disk reads with strict path validation.
- The active-run registry that powers `list_resources` is **forensic-only**
  (`R-Inv 1`): orchestrators register a `_ResourceScope(run_id, harness_dir,
  harness_token)` on start (§R3.1; keyed by `(harness_token, run_id)`) and
  deregister in `finally`; reads are best-effort snapshots; the registry is
  *never* the truth-of-record for run state (that stays in `state.json`,
  Invariant 1).
- Drivers are not touched at all. Agent context is unchanged.

`R-Invariant 0 (north star):` the resource surface widens the *host*
observability of a run but never widens or persists an agent's in-context
working set across a phase boundary; the only durable additions are
host-facing on-disk URIs.

### R0.3 Architectural stance: minimum blast radius

> **Alternative considered & rejected.** Adding a `subscribe_resource`
> capability + a `notifications/resources/updated` emit path so a host gets
> live pushes when `status.log` appends or `eval.json` materializes.
> Rejected: §C1.5's `task.update_status` already fans live status to a
> task-mode poller with the same human line a `ctx.info` consumer sees, so the
> marginal value of subscribe-on-`status.log` is small. The right move (not
> the easy one) is to ship the standard `list_resources` / `read_resource`
> pair on its own and let a real subscribe consumer drive any future
> companion. Adding subscribe speculatively buys a registry and an emit
> pipeline whose only consumer is hypothetical.

> **Alternative considered & rejected.** Replacing the existing `*_path`
> fields on `ArtifactIndex` / `IterationArtifacts` with `*_uri` fields.
> Rejected: callers reading `*_path` today (the §13 filesystem story) keep
> working only if those fields stay. `R-Decision 6` adds `*_uri` companions
> next to `*_path` so both addressing modes coexist; this is the
> back-compat-safe path.

---

## R1. The mechanism (`list_resources` + `read_resource`)

### R1.1 Handlers (`server.py`)

Two handlers register on the §C1.4 Path B low-level `Server`. Both are
declared on the *same* server instance that owns `run_forge`, so a single
stdio process serves both surfaces and capability advertisement (resources +
tools) is consistent.

```python
@server.list_resources()
async def list_resources(
    request: types.ListResourcesRequest,
) -> types.ListResourcesResult:
    """Enumerate run artifacts across active and configured harnesses (§R1.1).

    Design: §R1.1 — discoverable surface for hosts that don't already have a
        forge:// URI. Sources, in priority order: (a) the active-run registry
        for runs in flight in this process; (b) optional FORGE_HARNESS_ROOTS
        (env, comma-separated) for completed runs under known harness dirs.
        Without (b), past runs are still readable by URI but invisible to
        list_resources (forensic-only, R-Inv 1). Pagination is cursor-based
        per the MCP spec (R-Decision 7: eager scan, no cache). The signature
        matches the SDK's canonical pagination example
        (modelcontextprotocol/python-sdk README): the decorator passes the
        full ListResourcesRequest; cursor is at request.params.cursor and
        request.params may be None on a cursorless first call.
    Implementation: collect _ResourceScope rows from list_active_runs() +
        FORGE_HARNESS_ROOTS expansion, dedup by (harness_token, run_id),
        expand each to its on-disk allowlist (R-Inv 3), sort by
        (harness_token, run_id, subpath asc) so listing is deterministic
        without reading state.json, parse cursor as int offset (treat
        non-numeric cursors as start=0 — graceful degradation, not an
        error; numeric-but-past-end cursors clamp to len(rows) and return
        an empty page with nextCursor=None, the standard MCP pagination
        end-of-list signal), slice
        [start : start + page_size], emit nextCursor=str(start + page_size)
        when more remain. Failures during a single scope's expansion (per-
        scope OSError on expand_scope_to_resources) AND failures while
        listing a configured harness root (per-root OSError on iterdir
        when a FORGE_HARNESS_ROOTS path vanishes post-startup) are both
        logged and skipped — one missing run dir OR one vanished root
        must not poison the whole list (the two guards live in §R6 and
        are pinned independently by §R9.3 poison-resistance tests).
    Example: list_resources(types.ListResourcesRequest(method="resources/list",
        params=None))  # first page; cursor=None.
    """
```

```python
@server.read_resource()
async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
    """Resolve a forge:// URI and serve the artifact's contents (§R1.1).

    Design: §R1.1 — read-only access for one allowlisted artifact under a
        registered or configured harness. Path validation runs BEFORE the
        read (R-Inv 4) and the artifact MUST be in the §R2 allowlist
        (R-Inv 3). run.log and run.lock are structurally unreachable — they
        are not in the allowlist (R-Decision 2). This handler is a **pure
        disk read** — it never calls into a driver, an SDK, or any
        orchestrator state; resource bodies are written by the orchestrator
        layer and only read here (R-Inv 2). Return shape matches the SDK's
        decorator contract: Iterable[ReadResourceContents] (a helper type
        from mcp.server.lowlevel.helper_types with fields `content` and
        `mime_type`); the decorator wraps each item into the on-wire
        TextResourceContents/BlobResourceContents under one ReadResourceResult.
        Returning a pre-built ReadResourceResult is wrong on this decorator
        and silently mis-iterates the result.
    Implementation: decode uri to (harness_token, run_id, subpath) — raise
        the chain to McpError via the helper on ValueError; reject non-
        allowlisted subpaths via match_artifact (R-Inv 3); resolve the
        harness_dir via resolve_harness_dir (R-Inv 4); run the §R6 four-
        step containment (i abspath-commonpath, ii realpath-commonpath,
        iii Path.is_symlink leaf, iv Path.is_file) — each step on
        violation `raise _resource_not_found(uri)`. **Use `raise`, NOT
        `assert`** — the handler's only fallthrough errors are
        ValueError/OSError/UnicodeDecodeError, and `assert` would (a)
        propagate AssertionError uncaught and (b) be stripped under
        `python -O`, silently allowing path-traversal/symlink-escape
        exfiltration (same discipline as decode_uri, §R1.3). Then read
        bytes/text with strict UTF-8 for JSON artifacts (errors="strict")
        and errors="replace" for plain-text/log artifacts; return
        [ReadResourceContents(content=text, mime_type=pattern.mime)].
        Missing/denied/escaped paths, OSError on read, and strict-decode
        failures all surface as McpError(ErrorData(code=-32002,
        message="resource not found: <uri>")) — -32002 is the MCP-spec
        resource-not-found code, distinct from §6.3's INVALID_PARAMS
        (-32602) and SERVER_ERROR (-32000) so the bright line stays
        unmuddied (R-Inv 5; R-Decision 8).
    Example: read_resource(AnyUrl("forge://aBcDeFgHiJkL/12345678/plan/plan.md"))
        -> [ReadResourceContents(content="...", mime_type="text/markdown")].
    """
```

### R1.2 URI scheme

`forge://<harness_token>/<run_id>/<artifact_subpath>`

| Segment | Value |
|---|---|
| `harness_token` | `base64url(sha256(abs_harness_dir))[:12]` — 72 bits, stable across server restarts, collision-safe across every `target_dir` a single server has ever touched (`R-Decision 9`). |
| `run_id` | The existing 8-char hex from §6.5 (`^[0-9a-f]{8}$`). |
| `artifact_subpath` | Path relative to `<harness_dir>/<run_id>/`, e.g. `plan/plan.md`, `iteration-3/eval.md`. |

The two-segment form (`harness_token` + `run_id`, not a flat `run_id`-only
URI) is `R-Decision 1`: cross-target run-id collisions are theoretically
possible (4-byte hex = ~4B space; not zero), and the token also lets the URI
survive across server restarts — re-hashing the same `abs_harness_dir` gives
the same token, so a URI minted in process A still resolves in process B.

> **§13 canonicalization note.** `harness_token` hashes the
> `os.path.abspath`-canonicalized harness dir, NOT `os.path.realpath`. This
> matches §13's "symlinked aliases get distinct roots — intentional"
> discipline: two aliases for the same physical harness produce distinct
> tokens. An auditor relying on "the URI shape uniquely identifies the
> harness dir as the caller named it" must remember this aliasing semantics.

### R1.3 URI encode/decode (`resources.py`)

A new top-level leaf module owns the URI helpers, the allowlist, and the
active-run registry. **Stdlib + `forge_mcp` siblings only** — never `mcp.*`.
Concrete allowlist (single source of truth; §R5.1 mirrors it): `hashlib`,
`base64`, `os`, `os.path`, `pathlib`, `re`, `urllib.parse`, `dataclasses`,
`typing`. No `mcp.*` import (the `McpError` raise sites live in `server.py`'s
handlers, never in `resources.py`); no `logging` import either (the
`_RESOURCE_LOGGER` bound in §R6 lives in `server.py`, and
`expand_scope_to_resources`'s `OSError` propagates to the handler for
logging there — see §R6 list_resources sketch).

```python
def encode_uri(harness_token: str, run_id: str, subpath: str) -> str:
    """Format a forge:// URI from its three components (§R1.2).

    Design: §R1.2 — encoding is total and pure so it can be called from
        orchestrator/result.py without touching any state. The subpath uses
        forward slashes verbatim (no quoting beyond what the MCP AnyUrl
        validator imposes); allowlisted subpaths are restricted to safe
        characters by construction (R-Inv 3).
    Implementation: f"forge://{harness_token}/{run_id}/{subpath}"; assert
        harness_token matches ^[A-Za-z0-9_-]{12}$ and run_id matches
        ^[0-9a-f]{8}$ — bad inputs are a programming error, not a runtime
        miss.
    Example: encode_uri("aBcDeFgHiJkL", "12345678", "iteration-1/eval.md").
    """


def decode_uri(uri: str) -> tuple[str, str, str]:
    """Parse a forge:// URI into (harness_token, run_id, subpath) (§R1.2).

    Design: §R1.2 — the inverse of encode_uri, with strict validation so
        malformed URIs never reach the filesystem layer. Non-forge schemes,
        missing segments, AND non-empty `?query` / `#fragment` parts all
        raise ValueError so the read_resource handler can map them to
        McpError(-32002) (R-Inv 5). Rejecting query/fragment preserves the
        encode/decode roundtrip pin (§R9.1) and forecloses any "two URIs
        decode to the same artifact" cache-poisoning shape.
    Implementation: urlsplit; raise ValueError if scheme != "forge"; raise
        ValueError if parts.query or parts.fragment is non-empty; split
        netloc as the harness_token; **`parts.path.lstrip("/").split("/")`**
        then unpack `[run_id, *subpath_parts]` — the lstrip is load-bearing
        because `urlsplit("forge://t/12345678/plan/plan.md").path` is
        `/12345678/plan/plan.md`, so a naive `parts.path.split("/")` would
        unpack the leading empty as `run_id=""` on every well-formed URI
        (the `^[0-9a-f]{8}$` regex would then reject it as a false-positive
        malformed URI); validate each segment against the patterns (raise
        ValueError on mismatch); rebuild subpath with "/" so it
        round-trips with encode_uri. **MUST `raise ValueError`, NOT
        `assert`** — the handler at §R6 catches `except ValueError:`, and
        `assert` (a) propagates `AssertionError` uncaught, (b) is stripped
        entirely under `python -O`. (Compare `encode_uri`'s `assert`
        above: that one is an internal programming-error guard on
        already-validated values; `decode_uri` receives untrusted
        external URIs and must fail loud.)
    Example: decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md")
        -> ("aBcDeFgHiJkL", "12345678", "plan/plan.md");
        decode_uri("forge://aBcDeFgHiJkL/12345678/plan/plan.md?x=1") raises.
    """


def compute_harness_token(harness_dir: Path) -> str:
    """Derive the 12-char stable token for a harness directory (§R1.2).

    Design: §R1.2 — sha256(os.path.abspath(harness_dir)) then base64url-encode
        and truncate to 12 chars (72 bits). Stable across processes and
        restarts because the hash input is the canonicalized path string,
        not an inode or mtime. Distinct symlinked aliases produce distinct
        tokens (intentional, §13).
    Implementation: digest = hashlib.sha256(os.path.abspath(str(harness_dir))
        .encode()).digest(); base64.urlsafe_b64encode(digest).rstrip(b"=")
        .decode()[:12]; the result matches ^[A-Za-z0-9_-]{12}$.
    Example: compute_harness_token(Path("/repo/.harness")) -> "aBcDeFgHiJkL".
    """
```

The encode/decode pair is pure and is the only piece of URI machinery the
result builder needs. The active-run registry (§R3) is the only piece of
state the handlers need.

---

## R2. Artifact catalog (allowlist, not denylist)

### R2.1 Served artifacts

The allowlist enumerates every artifact `read_resource` will serve. A
filename match is by *exact* relative subpath (or one of the iteration-N
patterns); anything else raises `McpError(-32002)` via the
`_resource_not_found(uri)` helper (§R1.1). The mime types mirror the file
extension semantics.

| Subpath pattern | MIME |
|---|---|
| `inputs/design.md` | `text/markdown` |
| `inputs/git-state.txt` | `text/plain` |
| `inputs/git-uncommitted.txt` | `text/plain` |
| `plan/plan.md` | `text/markdown` |
| `plan/sessions.json` (§C2) | `application/json` |
| `iteration-<N>/contract.md` | `text/markdown` |
| `iteration-<N>/summary.md` | `text/markdown` |
| `iteration-<N>/eval.json` | `application/json` |
| `iteration-<N>/eval.md` | `text/markdown` |
| `iteration-<N>/triage.json` | `application/json` |
| `iteration-<N>/sessions.json` (§C2) | `application/json` |
| `iteration-<N>/git-violation.txt` | `text/plain` |
| `iteration-<N>/verify.txt` (§H1) | `text/plain` |
| `state.json` | `application/json` |
| `status.log` | `application/x-ndjson` |
| `unresolved-gaps-overflow.md` | `text/markdown` |
| `design-flaw-gaps-overflow.md` | `text/markdown` |

The iteration patterns match `^iteration-([1-9]\d*)/<name>$` where `<name>`
is one of the iteration-N entries above. `N` is a positive integer with no
leading zeros; `iteration-0/...` and `iteration-007/...` are rejected.

### R2.2 Never-served artifacts

- **`run.log`** — sensitive (0600, §13). The base doc says: *"`run.log` is
  deliberately ABSENT [from `ArtifactIndex`] — run.log is sensitive (0600)
  and must never cross the tool boundary."* This brief honors that by
  structurally omitting `run.log` from the allowlist. A future contributor
  adding it would have to add a row to `_ALLOWED_ARTIFACTS` — a §R9 negative
  test pins `"run.log" not in _ALLOWED_ARTIFACTS` permanently.
- **`run.lock`** — operational; in the *harness root*, not a run dir, so it
  would not match the URI shape even if it were allowlisted, but the
  allowlist makes it doubly unreachable.
- **Anything outside `<harness_dir>/<run_id>/`** — `target_dir`'s code,
  parent directories, symlink targets — rejected by path-containment
  validation (`R-Inv 4`) before any read attempt.

### R2.3 The allowlist as a data structure

```python
@dataclass(frozen=True)
class _ArtifactPattern:
    """One allowlisted artifact (§R2.3).

    Design: §R2 — capturing the static allowlist as data lets the resource
        handlers iterate uniformly (list_resources expansion, read_resource
        validation, the §R9 sanity tests) without scattered string literals.
    Implementation: frozen dataclass; `regex` matches the *full* subpath when
        provided, else `subpath` is the exact match; `mime` is the
        MIME type.
    Example: _ArtifactPattern(subpath="plan/plan.md", regex=None, mime="text/markdown").
    """
    subpath: str | None       # exact-match subpath (mutually exclusive with regex)
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
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/sessions\.json$"), "application/json"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/git-violation\.txt$"), "text/plain"),
    _ArtifactPattern(None, re.compile(r"^iteration-([1-9]\d*)/verify\.txt$"), "text/plain"),
    _ArtifactPattern("state.json", None, "application/json"),
    _ArtifactPattern("status.log", None, "application/x-ndjson"),
    _ArtifactPattern("unresolved-gaps-overflow.md", None, "text/markdown"),
    _ArtifactPattern("design-flaw-gaps-overflow.md", None, "text/markdown"),
)
```

```python
def match_artifact(subpath: str) -> _ArtifactPattern | None:
    """Return the allowlist row matching subpath, else None (§R2.3).

    Design: §R2.3 — single source for the "is this subpath served?" decision,
        used by both list_resources expansion and read_resource validation.
        Returning None (rather than raising) lets the caller decide whether
        to skip silently (listing) or emit McpError(-32002) (reading).
    Implementation: iterate _ALLOWED_ARTIFACTS; exact-match wins by string
        equality; otherwise return the first regex full-match. Order in the
        tuple is deterministic but immaterial (patterns are disjoint).
    Example: match_artifact("iteration-3/eval.md") -> _ArtifactPattern(...).
    """
```

---

## R3. Server-level state: the active-run registry

### R3.1 Registry shape

```python
@dataclass(frozen=True)
class _ResourceScope:
    """One discoverable harness/run pair (§R3.1).

    Design: §R3 — list_resources needs to enumerate active runs without
        looking inside any Orchestrator instance (per-call lifetime,
        Invariant 6). The scope carries the harness_token (already
        derived once in the outer `run_forge` handler and threaded through
        `Orchestrator.__init__` to `self._harness_token`, so
        `list_resources` reads it without recomputing per call) alongside
        the dir and run_id.
    Implementation: frozen, hashable, immutable; `harness_token` is the
        value of `self._harness_token`. Chain of custody (see §R6 sketch +
        §R10 step 5): the outer `run_forge` handler calls
        `compute_harness_token(prepared.harness_dir)` ONCE; the resulting
        string is passed as a `harness_token=…` kwarg into
        `_orchestrator_entry` (which only **forwards** — it does NOT call
        `compute_harness_token` itself), which threads it into
        `Orchestrator(...)`'s constructor, which stores it on
        `self._harness_token`, from which the engine's
        `register_active_run(scope=_ResourceScope(..., harness_token=
        self._harness_token))` reads it. The dataclass constructor itself
        does NOT call `compute_harness_token`; doing so would couple
        registration to disk-based canonicalization and duplicate the
        outer-handler computation.
    Example: _ResourceScope(run_id="12345678", harness_dir=Path("/r/.harness"),
        harness_token="aBcDeFgHiJkL").
    """
    run_id: str
    harness_dir: Path
    harness_token: str


_ACTIVE_RUNS: dict[tuple[str, str], _ResourceScope] = {}    # key = (harness_token, run_id)


def register_active_run(scope: _ResourceScope) -> None:
    """Add an in-flight run to the discovery registry (§R3.1).

    Design: §R3.1 — orchestrators call this as the **first statement inside
        the same engine try-block whose finally calls
        `self._prepared.lock.release()`** (§R6) — engine.py has nested
        try-blocks today; the relevant one is the outer try whose finally
        owns lock release + umask restore. Placement inside that try (a)
        lets list_resources see the run, AND (b) ensures a register-time
        exception (e.g., the assertion below) is captured by the same
        finally that releases the lock and deregisters. The key is
        the (harness_token, run_id) pair (matching the URI shape of §R1.2 /
        R-Decision 1): Invariant 5 forbids two concurrent runs on the *same*
        target_dir (so same harness_token), but cross-target run_id
        collisions are theoretically possible (4-byte hex), so a flat
        run_id-only key would conflate them. Using the pair makes the key
        space match the URI space exactly.
    Implementation: assert (scope.harness_token, scope.run_id) not in
        _ACTIVE_RUNS; assign; atomic at the Python-dict level so concurrent
        list_resources reads see consistent snapshots.
    Example: register_active_run(_ResourceScope("12345678", Path("/r/.harness"),
        "aBcDeFgHiJkL")).
    """


def deregister_active_run(harness_token: str, run_id: str) -> None:
    """Remove a run from the registry on terminate (§R3.1).

    Design: §R3.1 — called from engine.run's finally so the registry stays
        consistent even on cancellation/failure paths. Idempotent — a
        re-deregister (defensive double-call) is a no-op, not a raise, to
        match TargetLock.release's idempotence pattern (§6.5). Takes the
        (harness_token, run_id) pair so the call site doesn't need to
        reconstruct the dict key.
    Implementation: _ACTIVE_RUNS.pop((harness_token, run_id), None).
    Example: deregister_active_run("aBcDeFgHiJkL", "12345678").
    """


def list_active_runs() -> list[_ResourceScope]:
    """Snapshot the current active-run registry (§R3.1).

    Design: §R3.1 — list_resources reads through this helper rather than
        the global dict so the snapshot semantics are explicit (a single
        atomic copy at read time; mid-read mutations affect the *next*
        list_resources call, not this one). Read-only — never mutates.
    Implementation: return list(_ACTIVE_RUNS.values()); Python list() over
        dict-values is atomic enough for snapshot semantics.
    Example: scopes = list_active_runs().
    """


def resolve_harness_dir(
    harness_token: str, harness_root_tokens: dict[str, Path]
) -> Path | None:
    """Resolve a harness_token to its harness_dir from registry or config (§R3.1).

    Design: §R1.1 — single lookup path for read_resource so the handler
        treats active runs and configured roots uniformly. Active runs win on
        collision (a harness_dir that is *both* in the registry and under a
        FORGE_HARNESS_ROOTS root still resolves the same Path; active scope
        lookup just short-circuits the O(N) scan).
    Implementation: scan list_active_runs() for a matching harness_token
        first (O(N) over a small N — typically 1 active run, occasionally
        a few); on miss, look up the token in harness_root_tokens.
        Returns None when neither lookup finds the token.
    Example: harness_dir = resolve_harness_dir("aBcDeFgHiJkL",
        config.harness_root_tokens).
    """


def expand_scope_to_resources(
    scope: _ResourceScope,
) -> list[tuple[str, str, str, str]]:
    """Walk a scope's harness_dir/run_id/ and emit one tuple per existing
    allowlisted artifact (§R3.1; called by list_resources, §R6).

    Design: §R5.1 keeps resources.py stdlib-only by returning plain tuples;
        server.py wraps each into an MCP-typed Resource (§R6 list_resources).
        The walk follows the same _ALLOWED_ARTIFACTS allowlist that the
        read_resource handler validates against (R-Inv 3), so listing and
        reading are guaranteed-consistent — anything listed is readable;
        anything not listed (e.g., run.log) is also not readable.
    Implementation: walk FOUR locations under
        scope.harness_dir/scope.run_id/ non-recursively, each covering a
        disjoint slice of §R2.1's allowlist:
          (1) the run dir's top level — state.json, status.log,
              unresolved-gaps-overflow.md, design-flaw-gaps-overflow.md.
          (2) inputs/ — design.md, git-state.txt, git-uncommitted.txt.
          (3) plan/ — plan.md, sessions.json.
          (4) every iteration-<N>/ subdir whose name matches
              ^iteration-[1-9]\\d*$ — contract.md, summary.md, eval.json,
              eval.md, triage.json, sessions.json, git-violation.txt,
              verify.txt.
        For each present file whose relative subpath match_artifact()
        accepts, append (encode_uri(scope.harness_token, scope.run_id,
        subpath), basename(subpath), pattern.mime, subpath) to the
        result list (the signature returns a concrete `list[tuple[...]]`;
        not a generator). Non-existent allowlisted entries are silently
        omitted (no false promises).
        Filesystem errors propagate as OSError to the handler, which logs
        and skips the whole scope (§R6 list_resources). Missing any of the
        four walk locations would silently drop ~5/17 allowlisted artifacts
        from list_resources — the §R9.3 "one Resource per existing
        allowlisted artifact" test pins this by exercising at least one
        artifact from EACH location.
    Example: rows = expand_scope_to_resources(_ResourceScope("12345678",
        Path("/r/.harness"), "aBcDeFgHiJkL")) ->
        [("forge://aBcDeFgHiJkL/12345678/plan/plan.md", "plan.md",
          "text/markdown", "plan/plan.md"),
         ("forge://aBcDeFgHiJkL/12345678/inputs/design.md", "design.md",
          "text/markdown", "inputs/design.md"), ...].
    """
```

### R3.2 Lifecycle

The orchestrator is the *only* writer to `_ACTIVE_RUNS`. It calls
`register_active_run` as the **first statement inside the engine try-block
whose finally owns `self._prepared.lock.release()`** (engine.py has nested
try-blocks; the relevant one is the outer try, where the lock release + umask
restore live in `finally:`) and `deregister_active_run` in that same
`finally:` — so a
register-time exception (e.g., the assertion in `register_active_run`'s
docstring) is captured by the same finally that releases the lock. The
`_ResourceScope` value is constructed *before* the `try:` (so `scope` is in
scope for the `finally:` deregister call), but no observable state mutates
until the `try:` body begins. This is two new lines plus one local in
`orchestrator/engine.py` and **no new logic** — it mirrors the existing
lock-release-in-finally pattern.

A successful run, a timeout, a cancellation, and a hard failure all flow
through the same `finally:`, so the registry is always consistent at
terminal state regardless of path. The §8.5 cancellation ordering is
untouched — `deregister_active_run` runs *after* the lock release and after
the final-state transition, in the engine's normal finally (the registry has
no liveness contract; an observer seeing a state-machine terminal can simply
not find the run via `list_resources`).

**Resume interplay (§H2).** A `resume=true` run hits `engine.run` through the
same entry path, so register / deregister fire exactly the same way. The
`harness_token` is recomputed from the same `prepared.harness_dir` (stable
across processes, §R1.2) and matches the pre-crash URI shape; existing URIs
issued by the pre-crash process resolve unchanged once the resumed engine
has registered. No special resume-aware code is required in either handler
or the registry.

`R-Invariant 1 (registry is forensic-only):` `_ACTIVE_RUNS` is read-only from
outside the orchestrator that wrote its own entry; resource handlers never
mutate it; the truth-of-record for run state stays in `state.json`
(Invariant 1).

### R3.3 Cross-process discovery: `FORGE_HARNESS_ROOTS`

Across server restarts the in-memory registry resets. To make completed runs
discoverable in `list_resources` past a process boundary, an operator MAY set
`FORGE_HARNESS_ROOTS` (env, comma-separated absolute paths). When set,
`list_resources` *also* enumerates every `<root>/<run_id>/` subdir as a
synthesized `_ResourceScope` (harness_token computed once at process start,
cached on `RunConfig`).

```python
@dataclass(frozen=True)
class RunConfig:
    # ... existing fields ...
    harness_roots: tuple[Path, ...] = ()         # NEW (§R3.3)
    harness_root_tokens: dict[str, Path] = field(default_factory=dict)  # NEW: token -> root
```

- Empty by default. The honest "I don't know about past runs" surface.
- Validated in `RunConfig.from_env`: each root MUST be an absolute path and
  MUST exist + be a directory + be readable, else `RunConfig.from_env`
  raises (fail-fast, Rule 8). A misconfigured root is a config bug, not a
  silent skip.
- `harness_root_tokens` is the inverse map populated once at config load,
  amortizing the hash across many `read_resource` calls. The
  `read_resource` handler consults `_ACTIVE_RUNS` first; if the token isn't
  there, it falls back to `config.harness_root_tokens`.
- Past runs under a non-active root are *readable by URI* regardless of
  whether `FORGE_HARNESS_ROOTS` is set, but only *listed* when it is —
  exactly the "discovery is opt-in, reading is by-URI" model that respects
  forge-mcp's per-call architecture (`R-Decision 4`).

> **§H9 retention interplay.** `prune_old_runs` (§H9) is unchanged. After
> pruning, a URI for a pruned run no longer resolves on disk;
> `read_resource` returns `McpError(-32002)` and `list_resources`
> omits it. There is no separate "purge URIs" step — the filesystem is the
> truth-of-record (`R-Inv 1`).

---

## R4. Public API & model changes (consolidated)

All additions are **optional fields** so the §18 input/output schema pins
hold (input + output schemas stay object-root, no top-level combinators) and
existing callers are unaffected.

### R4.1 `RunForgeInput` — no change

Resource discovery is per-server-process (env-driven `FORGE_HARNESS_ROOTS`),
not per-call. No new tool argument.

### R4.2 `ArtifactIndex` — new optional `*_uri` companions

```python
class ArtifactIndex(BaseModel):
    # existing fields (paths) unchanged ...
    plan_uri: str | None = None                          # NEW (§R4.2)
    plan_sessions_uri: str | None = None                 # NEW (§R4.2)
    status_log_uri: str | None = None                    # NEW (§R4.2)
    state_json_uri: str | None = None                    # NEW (§R4.2)
    git_state_uri: str | None = None                     # NEW (§R4.2)
    git_uncommitted_uri: str | None = None               # NEW (§R4.2)
    unresolved_gaps_overflow_uri: str | None = None      # NEW (§R4.2)
    design_flaw_gaps_overflow_uri: str | None = None     # NEW (§R4.2)
    # run.log/run_log_uri DELIBERATELY ABSENT — R-Inv 3 (allowlist not denylist)
```

### R4.3 `IterationArtifacts` — new optional `*_uri` companions

```python
class IterationArtifacts(BaseModel):
    # existing fields (paths) unchanged ...
    contract_uri: str | None = None                      # NEW (§R4.3) ← contract_path
    summary_uri: str | None = None                       # NEW (§R4.3) ← summary_path
    eval_json_uri: str | None = None                     # NEW (§R4.3) ← eval_json_path
    eval_md_uri: str | None = None                       # NEW (§R4.3) ← eval_md_path
    triage_json_uri: str | None = None                   # NEW (§R4.3) ← triage_json_path (parity with eval_json_uri)
    sessions_uri: str | None = None                      # NEW (§R4.3) ← sessions_path (§C2)
    git_violation_uri: str | None = None                 # NEW (§R4.3) ← git_violation_path
    verify_uri: str | None = None                        # NEW (§R4.3) ← verify_path (§H1)
```

### R4.4 `RunResult` — no new top-level field

Existing fields are unchanged. This brief adds **only** the `*_uri`
companions on the artifact index (`ArtifactIndex` + `IterationArtifacts` —
§R4.2 / §R4.3). Top-level `RunResult` fields added by the prior bricks
(§H1 `verification: VerificationSummary | None`, §H2
`resumed_from_iteration: int | None`, §C3 `task_id: str | None`) are
unchanged here.

### R4.5 `RunConfig` — new optional fields

```python
harness_roots: tuple[Path, ...] = ()                               # NEW (§R3.3)
harness_root_tokens: dict[str, Path] = field(default_factory=dict)  # NEW (§R3.3) — token -> root
```

`RunConfig` is currently `@dataclass(frozen=True, slots=True)` (config.py:10), so:
- Use `field(default_factory=dict)` for the mutable default — a bare `= {}`
  raises `ValueError: mutable default ...` at class-construction time.
- The current `config.py` imports only `from dataclasses import dataclass`;
  **extend that import to `from dataclasses import dataclass, field`** when
  adding the new field, otherwise `field` is `NameError` at module load.
- All validation + token-precomputation happens **inside** `RunConfig.from_env`
  before the frozen instance is constructed; you can't mutate the dict after
  `cls(...)` returns (frozen-rebind protection doesn't cover in-place mutation,
  but treating the field as build-time-populated keeps the invariant explicit).

### R4.6 Server capability advertisement

The low-level `Server` declares the resources capability via handler
registration. The capability shape (post-registration):

```json
{
  "resources": {
    "listChanged": false,
    "subscribe": false
  }
}
```

`subscribe: false` is honest about what we don't support (`R-Decision 3`).
`listChanged: false` is honest too — the active-run registry changes
constantly, but emitting `notifications/resources/list_changed` on every
change would be noisy without a real consumer. A future companion can flip
either flag when there's a use case.

**No bootstrap change is required to emit this shape.** The MCP Python SDK
derives the `resources` capability from the combination of (a) which
handlers are registered and (b) the `NotificationOptions` passed into
`server.create_initialization_options(...)`. With this brief: registering
only `@server.list_resources()` + `@server.read_resource()` (no subscribe
handler — `R-Decision 3`) plus the SDK's default
`NotificationOptions(resources_changed=False)` yields the
`ResourcesCapability(subscribe=False, listChanged=False)` shape directly
(verified at `mcp/server/lowlevel/server.py`'s
`get_capabilities(notification_options, ...)`). The existing `cli.py` /
`forge serve` bootstrap therefore extends naturally — no new
`NotificationOptions` flag, no explicit `ResourcesCapability(...)`
construction in user code. §R9.4's capability test reads
`server.get_capabilities(NotificationOptions(), {}).resources` directly
and pins both flags as `False` (not `None`, not missing) to catch a
future regression that accidentally flips `resources_changed=True`.
Note that the SDK at `mcp/server/lowlevel/server.py` hardcodes
`subscribe=False` in `get_capabilities` regardless of which handlers are
registered — adding a stray `@server.subscribe_resource()` would NOT
surface in the capability wire shape. R-Decision 3's "no subscribe in
this round" therefore needs a SEPARATE pin: `assert types.SubscribeRequest
not in server.request_handlers` (a future contributor wiring subscribe
must delete this assertion deliberately).

---

## R5. Module map & dependency-graph compliance

### R5.1 New module: `resources.py`

Top-level leaf, stdlib only. Owns:

- `_ResourceScope` dataclass.
- `_ACTIVE_RUNS` dict + `register_active_run` / `deregister_active_run` /
  `list_active_runs`.
- `resolve_harness_dir(harness_token, harness_root_tokens) -> Path | None`
  — the single token-to-harness lookup used by `read_resource`.
- `_ALLOWED_ARTIFACTS` tuple + `match_artifact` helper.
- `encode_uri` / `decode_uri` / `compute_harness_token` helpers.
- `expand_scope_to_resources(scope)` helper that walks four disjoint
  locations under `harness_dir/run_id/` (top level, `inputs/`, `plan/`,
  every `iteration-<N>/`) and emits one `(uri, name, mime_type, subpath)`
  tuple per allowlisted artifact that exists (server.py wraps these in
  MCP `Resource` types so the leaf stays stdlib-only). See §R3.1 sketch
  for the full walk contract.

Imports allowed: stdlib (`hashlib`, `base64`, `os`, `os.path`, `pathlib`,
`re`, `urllib.parse`, `dataclasses`, `typing`). No `mcp.*`, no orchestrator,
no drivers — `resources.py` returns plain dicts / dataclasses / tuples; the
MCP-typed `Resource` / `ListResourcesResult` / `ReadResourceContents` values
are constructed in `server.py` from those plain returns. `ReadResourceContents`
(the helper type from `mcp.server.lowlevel.helper_types`) is imported by
`server.py` only.

### R5.2 Changed files

| File | Change | Section |
|---|---|---|
| `resources.py` (new top-level leaf) | URI encode/decode, allowlist, active-run registry, scope expansion. Stdlib only. | §R1.3 / §R2.3 / §R3.1 |
| `server.py` | Add `@server.list_resources()` + `@server.read_resource()` handlers; build `Resource` + `ListResourcesResult` for the listing path and return `list[ReadResourceContents]` from the read path (the SDK decorator wraps it into the on-wire `ReadResourceResult` itself — §R1.1 explicitly warns against constructing it in user code). | §R1.1 |
| `orchestrator/engine.py` | Accept `harness_token: str \| None = None` on `Orchestrator.__init__` (mirrors §C3's `task_id` pattern). When the value is non-None, construct `scope = _ResourceScope(...)` from `prepared.harness_dir`, `prepared.run_id`, and the supplied token **before** the outer `try:` block (so `scope` is in scope for the outer `finally:`); call `register_active_run(scope)` as the **first statement inside** that outer `try:` (the same try whose `finally:` owns `self._prepared.lock.release()` + umask restore — engine.py has nested try-blocks; this is the outer one); call `deregister_active_run(scope.harness_token, scope.run_id)` in that same outer `finally:`. When `harness_token` is None (step-4-only intermediate state), skip register/deregister entirely — the engine still runs, just without resource-surface visibility. This shape ensures a register-time exception is captured by the same finally that releases the lock (§R3.2 lifecycle). | §R3.2 |
| `orchestrator/result.py` | `_artifact_index` populates `*_uri` companions next to each `*_path` it already populates, using `encode_uri(harness_token, run_id, subpath)` — but only when `harness_token is not None` (otherwise the `*_uri` fields stay `None`, their schema default). `build_result` takes a new `harness_token: str \| None = None` keyword arg sourced from `Orchestrator.__init__`'s constructor-passed token (`server.py`'s `_orchestrator_entry` flips it to the real value in step 5, per §R10). | §R4.2 / §R4.3 |
| `config.py` | `RunConfig.harness_roots` (`tuple[Path, ...]`) + `RunConfig.harness_root_tokens` (`dict[str, Path]`) + `FORGE_HARNESS_ROOTS` env parsing in `RunConfig.from_env` with strict validation (Rule 8). | §R3.3 / §R4.5 |
| `models.py` | Optional `*_uri` companions on `ArtifactIndex` and `IterationArtifacts`. | §R4.2 / §R4.3 |

### R5.3 Dependency-graph compliance (§5.2 preserved)

**Allowed new edges:**
- `server → resources` (handlers consume the encode/decode + scope helpers)
- `orchestrator.engine → resources` (register/deregister)
- `orchestrator.result → resources` (URI encode)
- `config → resources` (only `compute_harness_token` — already stdlib-only)
- `resources → (stdlib only)`

**Forbidden edges** (still forbidden, no new violations):
- `drivers/* → orchestrator/*` (untouched; drivers don't know about resources)
- `drivers/* → doctor` (untouched)
- `resources → drivers/*` (resources is a leaf; never imports drivers)
- `resources → orchestrator/*` (resources is a leaf)

**Invariant 1 (single state writer) preserved:** `resources.py` only *reads*
`state.json` (and only when explicitly requested via a `state.json`-shaped
`read_resource`). Only `orchestrator/*` (via `RunStateMachine`) writes it.

**Invariant 6 (per-call orchestrator) preserved:** `_ACTIVE_RUNS` is server
*module* state (process-level), not orchestrator state. Each `Orchestrator`
instance is still per-`run_forge`-call; the registry is the discovery aid,
not control-flow state. This is `R-Decision 5`.

---

## R6. Enhanced control flow (integrated)

Augments §C5 / §H13 control flow in two places — orchestrator entry (register)
and engine finally (deregister). The handler-side flows are independent of
the orchestrator's flow.

```
server.py (Path B handler, with §C1.4 control flow unchanged):
  os.umask(0o077); RunForgeInput.validate(...); config = RunConfig.from_env()
  prepared = await prepare_run(inputs, config)
  # task_id is NOT computed at this scope — `task` is not in scope here.
  # It is extracted inside _orchestrator_entry (below), where `task` IS a
  # parameter (the §C1.4 pattern: task_id is per-invocation, derived from
  # the live ServerTaskContext or None).
  harness_token = compute_harness_token(prepared.harness_dir)              # §R3.2
  if _client_requested_task_mode(ctx):
    async def work(task):
      return _result_to_call_tool_result(
          await _orchestrator_entry(prepared, inputs, config, ctx,
                                    task=task, harness_token=harness_token))
    return await ctx.experimental.run_task(work)
  return _result_to_call_tool_result(
      await _orchestrator_entry(prepared, inputs, config, ctx,
                                task=None, harness_token=harness_token))

_orchestrator_entry(prepared, inputs, config, ctx, *, task, harness_token):  # server.py
  task_id = _extract_task_id(task)
  return await Orchestrator(prepared, inputs, config, ctx, drivers=...,
                            task=task, task_id=task_id,
                            harness_token=harness_token).run()


Orchestrator.run():  # unchanged structure, with §R3.2 register/deregister
  ... existing pre-try setup (§8.1, §C5) ...
  # Step-4-only intermediate state: harness_token may be None until step 5
  # (server.py) lands and threads the real token in. When None, skip the
  # resource-surface visibility entirely — engine still runs unchanged.
  # (Required because _ResourceScope.harness_token is `str`, not `str | None`;
  # passing None would crash the frozen-dataclass constructor.)
  scope: _ResourceScope | None = None
  if self._harness_token is not None:
    scope = _ResourceScope(run_id=self._prepared.run_id,                   # §R3.2 — pre-try
                           harness_dir=self._prepared.harness_dir,
                           harness_token=self._harness_token)
  try:
    if scope is not None:
      register_active_run(scope)                                           # §R3.2 — INSIDE try
    ... existing try-body (§8.1, §H13, §C5) unchanged, EXCEPT the existing
        `result = build_result(...)` call (engine.py:298-307) gains the
        new `harness_token=self._harness_token` kwarg — see §R5.2. The
        assignment-then-`return result` shape (engine.py:298 → :308) is
        unchanged, and the return stays INSIDE the try (it always has
        been), so the finally below still runs on the success path; there
        is NO new return-after-finally.
  finally:
    if not ledger.lock_released:                                           # existing (engine.py:310-312)
      self._prepared.lock.release()
      ledger.lock_released = True                                          # existing engine.py:312
    # existing handler teardown (inline at engine.py:313-315; no helper)
    os.umask(previous_umask)
    if scope is not None:
      deregister_active_run(scope.harness_token, scope.run_id)             # §R3.2 — idempotent


# server.py module scope:
_RESOURCE_CONFIG: RunConfig = RunConfig.from_env()                         # §R3.3 — bound once
_RESOURCE_LOGGER: logging.Logger = logging.getLogger("forge_mcp.resources") # leaf logger; never run.log


def _resource_not_found(uri: AnyUrl) -> McpError:                          # §R1.1 helper
  """One-line helper: build the McpError shape for resource-not-found.

  Design: -32002 is the MCP-spec resource-not-found code; consolidating
      the construction in one spot keeps the handler bodies short and the
      code/message shape consistent across all reject paths.
  Implementation: McpError(ErrorData(code=-32002,
      message=f"resource not found: {uri}", data=None)).
  Example: raise _resource_not_found(uri).
  """


@server.list_resources()                                                   # §R1.1
async def list_resources(request: types.ListResourcesRequest) -> types.ListResourcesResult:
  # config, logger captured from module scope
  cursor = request.params.cursor if request.params is not None else None
  scopes_by_key: dict[tuple[str, str], _ResourceScope] = {                 # dedup by (token, run_id)
      (s.harness_token, s.run_id): s for s in list_active_runs()           # active runs first
  }
  for root_token, root_dir in _RESOURCE_CONFIG.harness_root_tokens.items():  # §R3.3
    # Wrap iterdir() in its own try/except OSError: a configured root may
    # vanish post-startup (operator-initiated cleanup, mount unmount, etc.)
    # — uncaught OSError here would poison the whole list_resources call,
    # including ACTIVE runs in the registry that have nothing to do with
    # the failed root. The §R1.1 "one missing run dir must not poison the
    # whole list" claim depends on this guard.
    try:
      run_id_dirs = list(root_dir.iterdir())
    except OSError as exc:
      _RESOURCE_LOGGER.warning("root iterdir failed: %s root=%r", exc, root_dir)
      continue
    for run_id_dir in run_id_dirs:
      # Filter to BOTH directories AND matching run-id shape; a stray file
      # named '12345678' at the harness root would otherwise be enumerated
      # as a (non-existent) scope and surface as a no-op or spurious error
      # later in expand_scope_to_resources.
      if not run_id_dir.is_dir():
        continue
      if not re.match(r"^[0-9a-f]{8}$", run_id_dir.name):
        continue
      key = (root_token, run_id_dir.name)
      if key in scopes_by_key:                                             # active wins on collision
        continue
      scopes_by_key[key] = _ResourceScope(run_id=run_id_dir.name,
                                          harness_dir=root_dir,
                                          harness_token=root_token)
  # expand_scope_to_resources returns plain tuples (resources.py stdlib-only,
  # §R5.1); server.py wraps each into an MCP-typed Resource here.
  rows: list[Resource] = []
  for scope in scopes_by_key.values():
    try:
      for uri_str, name, mime_type, subpath in expand_scope_to_resources(scope):
        rows.append(Resource(uri=AnyUrl(uri_str), name=name, mimeType=mime_type))
    except OSError as exc:
      _RESOURCE_LOGGER.warning("expand failed: %s scope=%r", exc, scope); continue
  rows.sort(key=lambda r: str(r.uri))                                      # deterministic; cast for AnyUrl ordering
  page_size = 50
  try:
    start = int(cursor) if cursor is not None else 0
  except ValueError:
    start = 0                                                              # stale/malformed cursor → first page (graceful, no raise)
  start = max(0, min(start, len(rows)))                                    # clamp
  items = rows[start : start + page_size]
  next_cursor = str(start + page_size) if start + page_size < len(rows) else None
  return types.ListResourcesResult(resources=items, nextCursor=next_cursor)


@server.read_resource()                                                    # §R1.1
async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
  # config captured from module scope: _RESOURCE_CONFIG
  try:
    harness_token, run_id, subpath = decode_uri(str(uri))                  # raise ValueError on malformed input (§R1.3)
  except ValueError:
    raise _resource_not_found(uri)                                         # R-Inv 5: malformed URI → -32002
  pattern = match_artifact(subpath)
  if pattern is None:                                                      # R-Inv 3
    raise _resource_not_found(uri)
  harness_dir = resolve_harness_dir(harness_token,
                                    _RESOURCE_CONFIG.harness_root_tokens)
  if harness_dir is None:
    raise _resource_not_found(uri)
  abs_run_root = Path(os.path.abspath(harness_dir / run_id))               # R-Inv 4 (§13)
  abs_full = Path(os.path.abspath(harness_dir / run_id / subpath))
  # Four-step containment (R-Inv 4):
  #   (i)   Lexical commonpath on abspath rejects ../ escape.
  #   (ii)  Realpath commonpath rejects SYMLINK escape — a symlink under
  #         iteration-N/ pointing at /etc/passwd would pass step (i)
  #         (lexically inside the run dir) but its realpath lands outside.
  #         The Codex Generator can write inside iteration-N/ per §H7
  #         writable_roots, so this is a real exfiltration vector, not
  #         theoretical.
  #   (iii) is_symlink() on the leaf — defence in depth. Allowlisted
  #         artifacts are NEVER symlinks in legitimate use; a symlink at
  #         an allowlisted name is a planted attack.
  #   (iv)  is_file() rejects dir or missing.
  # commonpath is wrapped in try/except ValueError so pathological input
  # (NUL byte, mixed roots) maps to a clean -32002 instead of an uncaught
  # exception. abspath is used (NOT realpath) for the URI-shape containment
  # to keep §13's "symlinked aliases get distinct roots" discipline; realpath
  # is a SEPARATE defence-in-depth check, not the URI-identity check.
  try:
    if os.path.commonpath([str(abs_full), str(abs_run_root)]) != str(abs_run_root):
      raise _resource_not_found(uri)                                       # R-Inv 4 (i): ../ escape
    real_run_root = Path(os.path.realpath(abs_run_root))
    real_full = Path(os.path.realpath(abs_full))
    if os.path.commonpath([str(real_full), str(real_run_root)]) != str(real_run_root):
      raise _resource_not_found(uri)                                       # R-Inv 4 (ii): symlink escape
  except ValueError:
    raise _resource_not_found(uri)                                         # R-Inv 4: pathological path
  if abs_full.is_symlink():
    raise _resource_not_found(uri)                                         # R-Inv 4 (iii): leaf symlink
  if not abs_full.exists() or not abs_full.is_file():
    raise _resource_not_found(uri)                                         # R-Inv 4 (iv): dir or missing
  # JSON artifacts must decode strictly so a corrupt byte does not become �
  # silently producing invalid JSON; plain-text/log artifacts tolerate replace
  # so a partial logline still surfaces (R-Inv 5: read errors stay outside §6.3).
  json_mime = pattern.mime == "application/json"
  try:
    text = abs_full.read_text(encoding="utf-8",
                              errors="strict" if json_mime else "replace")
  except (OSError, UnicodeDecodeError):                                    # TOCTOU + bad-UTF-8
    raise _resource_not_found(uri)
  return [ReadResourceContents(content=text, mime_type=pattern.mime)]
```

> **Module-state binding (§R3 lifecycle interplay).** `_RESOURCE_CONFIG` is
> bound **once** at `server.py` module import time via `RunConfig.from_env()`.
> The per-call `run_forge_handler` (§C1.4) calls `RunConfig.from_env()` a
> *second* time per invocation — both reads are independent and pure
> (env-only, no side effects), so they always observe the same values during
> a server's lifetime, and a divergence between them would only occur if env
> changed mid-process (which forge-mcp does not support — Rule 8 "configure
> at startup"). The two-read shape is intentional: the per-call read keeps
> §C1.4's tool handler self-contained; the module-scope read amortizes the
> hash precomputation for `harness_root_tokens` across many resource calls.
> The `RunConfig` is `frozen=True, slots=True` so a single shared instance
> is safe to read concurrently from handlers. `_RESOURCE_LOGGER` is a
> module-scope leaf logger separate from any run's `run.log` (which is
> sensitive, §13, R-Inv 3) — expand-failure warnings go to stderr / the
> ambient logging config, never into a run's forensic log.

---

## R7. Invariant & taxonomy preservation (summary)

The §6.3 bright line, the §8.5 ordering, the §C1.6 / §C-Inv 1 single
cancellation authority, and the §H-Inv chain (0–7) are **all unchanged**.
`H-Inv 0` (north-star) is mirrored by `R-Inv 0` immediately below.
Resource I/O is outside the run-result taxonomy:

- A `read_resource` for a missing/denied artifact raises
  `McpError(ErrorData(code=-32002, message="resource not found: <uri>"))`
  via the `_resource_not_found(uri)` helper (§R1.1). The code `-32002` is
  the MCP-spec resource-not-found code, distinct from the `INVALID_PARAMS`
  (`-32602`) and `SERVER_ERROR` (`-32000`) codes §6.3 reserves for
  `run_forge`'s pre-run failures. §6.3's bright line — "terminal run states
  are normal `RunResult` returns; only pre-run `run_forge` failures raise
  `McpError`" — is scoped to the `run_forge` tool's terminal semantics;
  resource handlers are a different RPC method and their `McpError(-32002)`
  is therefore *outside* that bright line by code separation (`R-Inv 5`;
  `R-Decision 8`).
- A `list_resources` call during a run never blocks, transitions, or fails
  the run. The handler is independent of the orchestrator's lifecycle.
- The active-run registry mutates only on `Orchestrator.run` boundaries
  (first statement *inside* `try:`, then in `finally:`). A `list_resources`
  call mid-run returns a snapshot that includes the in-flight run; a call
  after `finally:` returns one that omits it. Either is consistent.

**New invariants summary:**

- **`R-Inv 0`** — neither the surface nor the registry widens or persists an
  agent's in-context working set across a phase boundary.
- **`R-Inv 1`** — the active-run registry is forensic-only; resource handlers
  never mutate it; `state.json` stays the truth-of-record (Invariant 1).
- **`R-Inv 2`** — resource handlers are pure disk reads; they MUST NOT call
  back into any driver/SDK or thread artifact contents into an agent's
  context.
- **`R-Inv 3`** — only artifacts in `_ALLOWED_ARTIFACTS` are served;
  `run.log` and `run.lock` are *structurally* unreachable (allowlist, not
  denylist).
- **`R-Inv 4`** — every URI's resolved absolute path must be *strictly
  inside* a registered/configured `<harness_dir>/<run_id>/`, enforced by
  the §R6 four-step containment IN ORDER: (i) `os.path.commonpath` on
  abspath rejects `../` escape, (ii) `os.path.commonpath` on realpath
  rejects symlink escape, (iii) `Path.is_symlink()` rejects the leaf
  symlink, (iv) `Path.is_file()` rejects dir / missing. URI **identity**
  uses `os.path.abspath` (not `realpath`), matching §13's symlinked-aliases-
  get-distinct-roots discipline; realpath in step (ii) is a SEPARATE
  defence-in-depth check, not the URI-identity check (see §R12 risk #3).
  Skipping any of (ii)/(iii) re-opens the exfiltration vector iteration 6
  closed.
- **`R-Inv 5`** — resource handlers stay outside §6.3's `run_forge`-scoped
  bright line by **using a different JSON-RPC error code**: failures raise
  `McpError(ErrorData(code=-32002, ...))` (MCP-spec resource-not-found),
  never `INVALID_PARAMS` (-32602) or `SERVER_ERROR` (-32000), and never a
  `RunResult`-style terminal status. -32002 is wire-distinguishable from
  §6.3's codes, so an observer can tell a "resource fetch failed" from
  "run_forge pre-run failed."

---

## R8. Best-practice grounding (article + context7)

| Surface | Article principle | context7 evidence |
|---|---|---|
| `list_resources` + `read_resource` | 11 ("instrument for observability over long runs"); the article's framing that an autonomous run's *visibility* matters as much as its *correctness* | `/modelcontextprotocol/python-sdk` (queried on v1.12.4 at design time; verified again on v1.27.x against the installed package) — `examples/servers/simple-resource/` registers `@server.list_resources()` + `@server.read_resource()` directly on a low-level `Server`; `examples/servers/simple-pagination/` shows the server-side handler taking `request: types.ListResourcesRequest` and reading `request.params.cursor if request.params is not None else None`, returning `types.ListResourcesResult(resources=…, nextCursor=…)`; the client side passes `params=PaginatedRequestParams(cursor=…)` to `session.list_resources()` and consumes `TextResourceContents` from `session.read_resource(AnyUrl(...))`. |
| Allowlist vs denylist for sensitive artifacts | "stress-test components' assumptions" (the article's discipline of being honest about what is and is not protected) | The MCP spec's resource model has no built-in confidentiality layer — server authors are responsible for what they expose. An allowlist makes the "we never serve `run.log`" property structural rather than a remember-to-skip rule. |
| Honest capability advertisement (`subscribe: false`, `listChanged: false`) | Same — the article's "strip components that are no longer load-bearing" reframed as "don't advertise what isn't real" | The MCP capability shape supports declaring partial coverage; declaring `subscribe: false` is correct today (`R-Decision 3`). |
| `os.path.abspath` (not `realpath`) for path containment | "components encode assumptions" — §13 already encodes the symlink-aliases-get-distinct-roots assumption | The MCP spec is silent on canonicalization; `os.path.abspath` matches forge-mcp's existing posture and avoids reintroducing the realpath-collapsing semantic. |

The base architecture's observability story (status fan-out, NDJSON sidelog,
on-disk artifacts) already faithfully implements the article's "make state
externally visible" theme. This brief closes the *MCP-protocol-level*
observability corner that the existing surface left open: a host that speaks
MCP but cannot reach the filesystem.

---

## R9. Testing strategy (extends §18, §H16, §C8)

TDD discipline matches §H16 / §C8: write the failing test first; all existing
tests stay green; Rule 21 docstrings on every new `def`; `scripts/ci.sh`
green at the end.

### R9.1 Pure / unit (no SDK / no disk)

- `encode_uri` / `decode_uri` roundtrip: `decode_uri(encode_uri(t, r, s)) ==
  (t, r, s)` for every allowlisted shape; malformed inputs raise.
- `compute_harness_token` stability: same `abs_harness_dir` input across two
  calls → same token; different inputs → different tokens; the token matches
  `^[A-Za-z0-9_-]{12}$`; symlinked aliases of the same physical dir produce
  distinct tokens (intentional, §13).
- `match_artifact` coverage: every member of `_ALLOWED_ARTIFACTS` matches the
  expected subpath; nothing else matches.
- **`_ALLOWED_ARTIFACTS` exclusion pins (permanent regression guard):**
  ```python
  # Exact-subpath additions
  assert "run.log" not in {p.subpath for p in _ALLOWED_ARTIFACTS}
  assert "run.lock" not in {p.subpath for p in _ALLOWED_ARTIFACTS}
  # Regex additions — test against the realistic subpath shapes a future
  # contributor might try to allowlist (NOT "/run.log" with a leading slash:
  # the allowlist regexes are anchored ^...$ and never match a path with a
  # leading slash, so search("/run.log") would silently pass even when a
  # hypothetical r"^iteration-([1-9]\d*)/run\.log$" pattern IS present).
  forbidden_subpaths = [
      "run.log",
      "run.lock",
      "iteration-1/run.log",
      "iteration-99/run.log",
      "plan/run.log",
      "inputs/run.log",
  ]
  for path in forbidden_subpaths:
      assert match_artifact(path) is None, f"{path} unexpectedly allowlisted"
  ```
  These pin `R-Inv 3` — a future contributor adding either an exact-subpath
  row or a regex-form row that would match any of `forbidden_subpaths`
  has to delete these assertions deliberately. Cite `# §R-Inv 3` on the
  test. The `match_artifact(path) is None` form tests through the actual
  matching surface (exact + regex), so any allowlist addition that would
  accept any forbidden shape is caught — closing the loophole where a
  bare `"/run.log"` `.search()` call silently passed against anchored
  regexes.
- `match_artifact` rejects `..`-escape attempts: `match_artifact("../foo")`,
  `match_artifact("iteration-1/../../etc/passwd")`, `match_artifact("/abs/path")`
  → `None`.
- **`resources.py` module-isolation pin (R-Inv 0 / R-Inv 2 structural).**
  R-Inv 0 ("surface never widens an agent's context across phase boundaries")
  and R-Inv 2 ("resource handlers are pure disk reads — never call into a
  driver/SDK") are architectural: there is no behavioral run-time signal for
  either, and both are preserved iff the §R5.1 import discipline holds.
  Pin structurally with an AST scan of `src/forge_mcp/resources.py`:
  `ast.parse` the source, walk every `ast.Import` / `ast.ImportFrom` node,
  and assert each top-level module name is in the stdlib allowlist
  (`hashlib`, `base64`, `os`, `os.path`, `pathlib`, `re`, `urllib.parse`,
  `dataclasses`, `typing`) — no `mcp.*`, no `forge_mcp.drivers`, no
  `forge_mcp.orchestrator`, no `logging`. A future contributor adding
  `from forge_mcp.drivers import ...` or `from mcp.server import ...` to
  `resources.py` must delete this assertion deliberately. Cite
  `# §R-Inv 0` and `# §R-Inv 2` on the test — this is the only practical
  pin for two otherwise unobservable invariants.

### R9.2 Active-run registry units

- `register_active_run` then `deregister_active_run(token, run_id)` leaves
  `_ACTIVE_RUNS` empty.
- An exception inside the registered block still results in deregistration
  (test the engine.run finally path with a synthetic exception).
- Concurrent `register_active_run` calls with different `(token, run_id)`
  pairs are independent; two distinct harness_dirs that happen to mint
  colliding run_ids both register successfully.
- `list_active_runs` returns a snapshot — mutations after the call don't
  affect the returned list.
- Defensive double-`deregister_active_run` is a no-op (idempotence).

### R9.3 Resource handler units (mocked filesystem)

- `list_resources` with no active runs and `FORGE_HARNESS_ROOTS` unset →
  empty result (`request.params=None` path).
- `list_resources` with one active run + a populated iteration tree → returns
  one `Resource` per existing allowlisted artifact; non-existent ones are
  omitted (no false promises). **The fixture MUST include at least one
  artifact from EACH of the four `expand_scope_to_resources` walk locations
  (top-level, `inputs/`, `plan/`, `iteration-N/`)** so the test catches a
  walk that silently omits a directory.
- `list_resources` paginates: large run sets return `nextCursor`; subsequent
  calls with `request.params.cursor` set to the prior `nextCursor` return
  the next page until exhaustion.
- `list_resources` dedups: an active run whose `harness_dir` is also under
  a configured `FORGE_HARNESS_ROOTS` root appears **once** (active wins on
  collision).
- `list_resources` poison-resistance — two independent paths:
  - **Per-scope OSError**: with two scopes where the first raises `OSError`
    on `expand_scope_to_resources` (e.g., a run dir vanished between
    enumeration and expansion), `list_resources` still returns the second
    scope's allowlisted resources and emits one `_RESOURCE_LOGGER.warning(
    "expand failed: ...")` entry. Pins the per-scope try/except in §R6.
  - **Per-root iterdir OSError**: configure `FORGE_HARNESS_ROOTS` with TWO
    roots, then (after `RunConfig.from_env` validates both) delete the
    first root's directory and call `list_resources`. The handler should
    still return resources from the second root's runs (plus any active
    runs in the registry) and emit one `_RESOURCE_LOGGER.warning("root
    iterdir failed: ...")` entry — NOT propagate the `OSError` to the
    caller, which would poison every concurrent `list_resources` call.
    Pins the per-root iterdir try/except added to defend against this
    exact failure mode (§R6 list_resources sketch).
  - These two together pin the "one missing run dir must not poison the
    whole list" claim (§R1.1 docstring, §R3.1 `expand_scope_to_resources`
    Implementation, and the per-root guard in §R6).
- `list_resources` stale cursor:
  - non-numeric cursor (e.g., `"abc"`) → `ValueError` caught, start = 0 →
    returns the first page; never raises.
  - numeric-but-past-end cursor (e.g., `"99999"` on a 10-row list) → clamps
    to `len(rows)`, returns an empty `resources=[]` page with
    `nextCursor=None`. This is the standard MCP pagination "end of list"
    signal; clients reaching it should stop polling. (NOT the first page —
    that would create infinite-loop hazards for clients using cursor==EOL
    to detect termination.)
- **Wire-code distinguishability (R-Inv 5 pin for every `read_resource`
  -32002 assertion that follows).** Every `read_resource(...) →
  McpError(-32002)` test below MUST verify `exc.error.code == -32002`
  (not merely `isinstance(exc, McpError)`). A regression in which the
  handler raises `McpError(INVALID_PARAMS, ...)` (-32602) or
  `McpError(SERVER_ERROR, ...)` (-32000) must fail the test, not silently
  pass — R-Inv 5's wire-distinguishability bright line (§6.3 codes are
  reserved for `run_forge` pre-run failures; -32002 is the MCP-spec
  resource-not-found code) depends on the contrastive code check.
- `read_resource("forge://.../run.log")` → `McpError(-32002)` (not a
  successful read). Pin via test (this is `R-Inv 3` from the read side).
- `read_resource("forge://.../iteration-1/eval.md")` → returns
  `[ReadResourceContents(content="...", mime_type="text/markdown")]` (the
  decorator wraps this into `ReadResourceResult` on the wire; the user
  function never constructs `ReadResourceResult` itself).
- `read_resource("forge://wrong-token/.../foo")` (token not in registry or
  config) → `McpError(-32002)`.
- `read_resource("forge://.../iteration-1/../../etc/passwd")` →
  `McpError(-32002)`. **Defense chain pinned (not what naive reading
  suggests).** The actual rejection chain at the handler level is:
  1. **Pydantic `AnyUrl` normalization** (upstream of `read_resource`):
     `Annotated[AnyUrl, UrlConstraints(...)]` normalizes path `..`
     segments at parameter-validation time. The handler receives
     `forge://<harness_token>/etc/passwd` (the `12345678/iteration-1/..`
     prefix collapsed to `/`), NOT the literal `..`-containing path.
  2. **`decode_uri` run_id regex** then runs on the normalized URI: path
     `/etc/passwd` splits to run_id `etc`, which fails the
     `^[0-9a-f]{8}$` validation → `ValueError` → handler raises
     `_resource_not_found`.
  3. `match_artifact()` and the four-step containment are **never
     reached** for this URI — they would be the next layers if the run_id
     somehow passed.
  This test therefore exercises the **AnyUrl normalization → decode_uri
  run_id validation** chain. It does NOT directly pin `match_artifact()`'s
  `..`-rejection (that contract is pinned by the §R9.1 unit test
  `match_artifact("iteration-1/../../etc/passwd") → None`, which calls
  `match_artifact()` directly, bypassing AnyUrl). Step (i)
  `abspath`-commonpath is also unreachable for any caller-supplied URI —
  it remains as redundant defense-in-depth against future regressions
  (e.g., a `read_resource(uri: str)` rewrite that drops the AnyUrl layer,
  or a malformed-allowlist-regex regression). §R12 risk #2's "skipping any
  of (ii)/(iii) re-opens the exfiltration vector" deliberately omits step
  (i) for the same reason. The load-bearing defenses §R9.3 pins are
  decode_uri validation (this test), R-Inv 3 (the §R9.1 direct
  `match_artifact` test), step (ii) (realpath escape), and step (iii)
  (leaf symlink).
- **Symlink defenses (§R6 steps (ii)–(iii); load-bearing security pins).**
  The two tests below must each pin a DIFFERENT step independently, so
  that removing **only** the named step (while keeping the other) causes
  **only** its own test to fail. To make this hold, the symlink must be
  placed on a DIFFERENT path component in each test — leaf vs.
  intermediate — because `Path.is_symlink()` only checks the leaf:
  - **Pin step (ii) — realpath-escape rejection (symlink on an
    INTERMEDIATE component, leaf is a regular file).** Plant a symlink
    *directory* at `<harness>/<run_id>/iteration-1` whose target is a
    directory OUTSIDE the harness tree (e.g., `/tmp/attacker-dir/`); inside
    that target dir, place a regular file named `eval.md`. So
    `<harness>/<run_id>/iteration-1/eval.md` is a regular file reached via
    a symlinked parent. `read_resource("forge://.../iteration-1/eval.md")`
    → `McpError(-32002)`. Walks the §R6 four-step containment:
    (i) abspath is lexically inside the run dir → passes;
    (ii) realpath follows the `iteration-1` symlink and lands at
    `/tmp/attacker-dir/eval.md` outside the run dir → **REJECTS**;
    (iii) `abs_full.is_symlink()` returns **False** (the leaf eval.md is
    a regular file — the symlink was the parent) → would NOT have
    rejected;
    (iv) `is_file()` returns True → would NOT have rejected.
    Step (ii) is therefore the ONLY defense that fires. Remove step (ii)
    and this test fails (eval.md's real content from `/tmp/attacker-dir/`
    would be served). Keep step (ii) and remove step (iii) — the test
    still passes (step (ii) still rejects). This is the genuine
    step-(ii)-only pin.
  - **Pin step (iii) — leaf-symlink rejection (symlink on the LEAF,
    realpath stays INSIDE).** Plant a symlink at the LEAF
    `<harness>/<run_id>/iteration-1/eval.md` whose target is another
    allowlisted artifact under the SAME run dir, e.g.
    `<harness>/<run_id>/iteration-1/contract.md`. `read_resource(...)` →
    `McpError(-32002)`. Walks the containment:
    (i) abspath lexical → passes;
    (ii) realpath lands at `contract.md` *inside* the run dir → passes;
    (iii) `abs_full.is_symlink()` returns **True** → **REJECTS**;
    (iv) would have returned True.
    Step (iii) is the ONLY defense that fires. Remove step (iii) and the
    handler follows the symlink and serves `contract.md`'s content (no
    error) — test fails. This is the genuine step-(iii)-only pin.
  - These two together pin the iteration-6 fix and the §R12 risk #2
    contract: an attacker plants a leaf symlink → step (iii) catches; an
    attacker plants an intermediate-component symlink → step (ii)
    catches. Neither defense alone suffices, and each test independently
    proves the corresponding defense is present. Cite
    `# §R-Inv 4 step (ii)` and `# §R-Inv 4 step (iii)` respectively on
    the two tests.
- `read_resource` for a corrupt-UTF-8 JSON artifact → `McpError(-32002)`
  (strict-decode; never silently substitute `U+FFFD` in `application/json`).
  Same artifact under `.log`/`.txt` MIME → returns content with replacement
  characters (best-effort for forensic logs).
- `read_resource` for a URI that decodes correctly but whose on-disk file
  was pruned by §H9 `prune_old_runs` → `McpError(-32002)` (the
  filesystem is the truth-of-record).
- `read_resource` for a malformed `forge://` URI (wrong scheme, missing
  segment, bad token shape) → `McpError(-32002)` via the `decode_uri`
  `ValueError` → handler conversion.

### R9.4 Transport / schema pins (extend §18 + §C8)

- `RunResult` post-`*_uri`-additions still produces an object-root
  `outputSchema` with **no top-level combinators** — the §18 / §C8 pin
  holds, since each new field is `str | None` defaulting to `None`.
- A `FakeClientSession` invokes `session.list_resources()` against the
  in-memory server; it sees the active run's allowlisted artifacts; the
  returned `Resource.uri` round-trips through `decode_uri`.
- The same fake reads one resource via `session.read_resource(AnyUrl(...))`
  and observes the expected `TextResourceContents`.
- Capability advertisement: the server's initialize response declares
  `resources` with `subscribe=false` and `listChanged=false` — a host that
  attempts `resources/subscribe` should receive the standard "method not
  found" response. The test reads
  `server.get_capabilities(NotificationOptions(), {}).resources` and
  asserts both flags are exactly `False` (not `None`, not missing).
- **No-subscribe structural pin (R-Decision 3).** Because the SDK at
  `mcp/server/lowlevel/server.py` hardcodes `subscribe=False` in
  `get_capabilities` regardless of registered handlers, the capability
  test above does NOT catch a stray `@server.subscribe_resource()`
  registration. Pin it separately:
  `assert types.SubscribeRequest not in server.request_handlers`. A
  future contributor wiring subscribe must delete this assertion
  deliberately. Cite `# R-Decision 3` on the test.

### R9.5 Integration / driver units

- `engine.run` calls `register_active_run` as the first statement inside
  the outer `try:` (the same try whose `finally:` releases
  `self._prepared.lock`) and `deregister_active_run` in that same
  `finally:`; verify by inspecting `_ACTIVE_RUNS` inside the try body and
  after, and by injecting an exception into the `register_active_run` call
  (e.g., monkeypatch to raise) to confirm the lock is still released by
  `finally:` (the iteration-3 fix this test pins).
- `build_result` populates every `*_uri` companion for every existing
  `*_path` it populates today, and *only* those — no spurious URIs for
  artifacts the run didn't produce.
- A fresh-run + resumed-run (§H2) both populate URIs for the same set of
  artifacts that ended up on disk; the URIs use the same `harness_token`
  (it's stable across resume by construction).

### R9.6 Negative tests for `FORGE_HARNESS_ROOTS`

- Non-existent path → `RunConfig.from_env` raises (fail-fast, Rule 8).
- A file (not a dir) → raises.
- An unreadable dir (chmod 000) → raises. *Gate this test with
  `@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses mode bits")`*
  — root CI runners (rootful containers) ignore mode bits, so the test
  would falsely succeed without the skip.
- A valid root with no child dirs → `list_resources` returns only active
  runs (no error, just no entries from that root).

---

## R10. Implementation sequencing

Each step lands behind its new optional surface; partial rollout is identical
to today when callers don't opt in.

1. **`resources.py` (leaf):** `_ResourceScope`, `_ACTIVE_RUNS` +
   register/deregister/list, `_ALLOWED_ARTIFACTS` + `match_artifact`,
   `encode_uri` / `decode_uri` / `compute_harness_token`. Pure unit tests
   (§R9.1, §R9.2). No callers yet.
2. **`models.py`:** add the optional `*_uri` companion fields to
   `ArtifactIndex` and `IterationArtifacts`. Schema-pin tests stay green.
3. **`config.py`:** add `harness_roots` + `harness_root_tokens`; parse
   `FORGE_HARNESS_ROOTS` in `from_env` with strict validation.
4. **`orchestrator/engine.py` + `orchestrator/result.py`:** thread an
   **optional** `harness_token: str | None = None` through
   `Orchestrator.__init__` → `build_result` (mirroring §C3's `task_id: str |
   None = None` pattern — `_orchestrator_entry` in server.py threads the
   real value in step 5; until then the kwarg defaults to `None` and the
   `*_uri` companions stay unpopulated). Add register/deregister calls
   gated on `harness_token is not None`. The §R9.5 integration tests gate
   this step. (Rationale: keeps step 4 landable in isolation without
   forcing server.py changes in the same PR; matches the §C2/§C3 staged
   rollout pattern.)
5. **`server.py`:** register `@server.list_resources()` + `@server.read_resource()`
   on the low-level `Server`; for `list_resources` build
   `Resource` + `ListResourcesResult`; for `read_resource` return
   `list[ReadResourceContents]` (the SDK decorator constructs
   `TextResourceContents` / `BlobResourceContents` + the wrapping
   `ReadResourceResult` itself — do NOT build them in user code, per §R1.1).
   In the **outer** `run_forge` handler (post-`prepare_run`, before the
   task-mode branch), compute `harness_token =
   compute_harness_token(prepared.harness_dir)` once and thread it as a
   `harness_token=harness_token` kwarg into `_orchestrator_entry` (both the
   `work(task)` closure and the direct-call branch); `_orchestrator_entry`
   passes it straight into `Orchestrator(...)`, flipping the step-4
   default from `None` to the real token. (See §R6 sketch for the exact
   call shape.) The §R9.3 / §R9.4 transport tests gate this step.
6. **Documentation:** README gains a short "Reading run artifacts over MCP"
   section pointing at `list_resources` / `read_resource` and noting the
   optional `FORGE_HARNESS_ROOTS` env var.
7. **`CLAUDE.md`:** add this doc as a fourth normative source (alongside the
   base + hardening + continuity docs) and document the `§R*` / `R-Invariant
   N` / `R-Decision N` citation namespaces. Note that the §18 schema pins
   continue to derive from `RunForgeInput.model_json_schema()` /
   `RunResult.model_json_schema()` (no shift for this brief).
8. Full `scripts/ci.sh` green.

---

## R11. Decisions log (forge-mcp resource surface)

| § | Decision | Rationale |
|---|---|---|
| §R1.2 (**R-Decision 1**) | Two-segment URI (`harness_token` + `run_id`), not flat `run_id`-only | Cross-target run-id collisions are theoretically possible (4-byte hex); the harness_token also lets URIs survive process restarts. |
| §R2 (**R-Decision 2**) | Allowlist, not denylist, for permitted artifacts | Structurally enforces §13's `run.log` secrecy; future artifact additions are opt-in. |
| §R4.6 (**R-Decision 3**) | No `subscribe_resource` in this round | §C1.5's `task.update_status` already covers live status fan-out; subscribe-on-artifact is a forward layer with no consumer today. |
| §R3.3 (**R-Decision 4**) | Honor optional `FORGE_HARNESS_ROOTS`; no auto-discovery from prior tool-call history | Respects forge-mcp's per-call architecture (Invariant 6); explicit config keeps server behavior deterministic — call history is not. |
| §R3.1 / §R5.3 (**R-Decision 5**) | Active-run registry as server-module state, not orchestrator state | Orchestrator instances are per-call (Invariant 6); coupling the registry to them would defeat discoverability for *active* runs (their lifetime would equal the call's). |
| §R0.3 / §R4.2 (**R-Decision 6**) | `*_uri` fields are **companion** to existing `*_path` fields, not replacements | Back-compat with existing callers (the §13 filesystem story); double field count is acceptable; the §18 schema pins stay valid. |
| §R1.1 (**R-Decision 7**) | Eager scan on every `list_resources` call; no cache | A typical harness has ≤10 runs (§H9 `keep_runs`); pagination amortizes cost; cache invalidation complexity is unwarranted. |
| §R1.1 (**R-Decision 8**) | Read failures raise `McpError(ErrorData(code=-32002, ...))` (MCP-spec resource-not-found), never the §6.3 codes (-32602 / -32000) | The MCP low-level `Server` has no other clean error path; -32002 is a *distinct* wire code, so the §6.3 bright line (scoped to `run_forge` pre-run codes) is preserved by code separation (`R-Inv 5`). **Implementation note:** the SDK does NOT export `-32002` as a Python symbol (`mcp.types` defines `INVALID_PARAMS = -32602`, `CONNECTION_CLOSED = -32000`, etc., but no `RESOURCE_NOT_FOUND`); inline the literal `-32002` per the §R6 `_resource_not_found` helper — do not attempt `from mcp.types import RESOURCE_NOT_FOUND`. |
| §R1.2 (**R-Decision 9**) | 12-char `harness_token` from `base64url(sha256(abs_harness_dir))[:12]` (72 bits) | 72 bits is collision-safe for any realistic deployment — birthday bound is approximately N² / 2^73 (standard formula for k-bit hash: N²/2^(k+1)); for N = 10⁶ distinct harness dirs (already hyperbolic — "every harness dir on Earth", per §R12 risk #5) the collision probability is ~10¹² / 9·10²¹ ≈ 10⁻¹⁰, comfortably negligible. See §R12 risk #5 for the same worked example with rationale. Shorter would be human-friendlier but margin-less. |

**Forward-looking (deferred, with stale-assumption notes):**

- **`subscribe_resource` + `notifications/resources/updated`:** adopt when a
  real consumer exists (a host UI that wants live pushes beyond
  `task.update_status`'s human lines). The MCP capability flip is one-line;
  the emit pipeline + subscription registry are the work.
- **`notifications/resources/list_changed`:** flip `listChanged: true` and
  emit on register/deregister + on artifact materialization. Useful only if a
  host renders an artifact tree live; same consumer gate as subscribe.
- **HTTP-transport variant:** `streamable_http_app` already serves this same
  resource surface (the MCP Python SDK examples are transport-agnostic).
  **Adoption gate:** when a remote forge-mcp deployment is wanted (the §C10
  trigger — currently noted as "forward-looking" with no consumer). The
  resource surface defined here ports verbatim; only the `forge serve`
  bootstrap in `cli.py` changes. The §C10 forward-looking HTTP-transport
  note remains the canonical tracker.

---

## R12. Risks & verification notes for the implementer

1. **`run.log` allowlist confidence.** The §R9.1 negative pin (`"run.log"
   not in _ALLOWED_ARTIFACTS`) is the load-bearing regression guard. A
   contributor adding the row would have to delete the assertion deliberately
   — make sure the test's failure message points back at `R-Inv 3` so the
   reviewer reads the rationale before approving.
2. **Path validation must run BEFORE the read, AND must include the
   symlink defenses.** A `read_resource` handler that (a) opens the file
   before validating the path, OR (b) validates only via `os.path.abspath`
   without also checking `realpath` containment and `is_symlink()` on the
   leaf, is **wrong**: `Path.exists()` / `Path.is_file()` follow symlinks,
   so a planted symlink at an allowlisted name (the Codex Generator can
   write inside `iteration-N/` per §H7 writable_roots) would pass a
   lexical-only check and serve `/etc/passwd`. Per §R6 the four required
   steps in order are: (i) abspath-commonpath rejects `../` escape, (ii)
   realpath-commonpath rejects symlink escape, (iii) `is_symlink()` rejects
   the leaf-symlink case, (iv) `is_file()` rejects dir or missing. Skipping
   ANY of (ii)/(iii) re-opens the exfiltration vector iteration 6 closed.
3. **Two canonicalizations, two purposes — keep them straight.**
   `os.path.abspath` is the **URI-identity** canonicalization (matching
   §13's symlinked-aliases-get-distinct-roots discipline). `os.path.realpath`
   is a **defence-in-depth** canonicalization used only inside the
   `read_resource` containment check (§R6 step (ii)) — it never feeds back
   into `harness_token` or any URI shape. Pin both with tests: (a) a harness
   dir that is a symlink to a sibling — `abspath` keeps it distinct from
   the target (the URI shape stays unique per caller); (b) a planted
   symlink under `iteration-N/` whose realpath lands outside the run dir
   — `read_resource` rejects with `McpError(-32002)`.
4. **`FORGE_HARNESS_ROOTS` misconfiguration.** Fail-loud at config-load time
   (Rule 8). A typo'd root that points at the *parent* of a real harness dir
   would otherwise enumerate every sibling run-shaped directory. A regex on
   `^[0-9a-f]{8}$` for the `run_id` subdir name filters most accidents, but
   the right defence is "we don't enumerate paths the operator didn't
   declare."
5. **`harness_token` collision under hash truncation.** 12 chars of
   `base64url(sha256(...))` = 72 bits. Birthday-collision probability for
   `N` distinct harness dirs is approximately `N² / 2^73`. For `N = 10⁶`
   (every harness dir on Earth, hyperbolically) the collision probability is
   ~`10¹² / 9·10²¹ ≈ 10⁻¹⁰` — comfortably negligible. If a future deployment
   wants longer tokens, bump the truncation length and re-test; the URIs are
   not stored, so back-compat is irrelevant.
6. **MCP type imports stay in `server.py`.** `resources.py` returns plain
   dataclasses / strings / tuples; the MCP-typed values constructed in
   `server.py` are `Resource` + `ListResourcesResult` (for the listing
   path) and `ReadResourceContents` (for the read path — the SDK decorator
   wraps these into the on-wire `ReadResourceResult` with
   `TextResourceContents` / `BlobResourceContents` itself; user code MUST
   NOT build either). This keeps `resources.py` a stdlib-only leaf
   (§R5.1) and the `TYPE_CHECKING` discipline (§C4) doesn't have to
   extend to it.
7. **Capability advertisement is honest.** `subscribe: false`,
   `listChanged: false`. A future consumer flipping either flag is a real
   design step (a registry + emit pipeline), not a config tweak — name the
   forward-looking work in §R11 so a reader can find it.
8. **§H2 resume + URI continuity.** `harness_token` is derived from
   `abs_harness_dir`, which doesn't change across resume (§H2 continues the
   same `run_id` and same `harness_dir`). So a URI minted in the pre-crash
   process is still valid in the resumed process; the §R9.5 test pins this.
9. **Four normative docs now.** Update `CLAUDE.md`'s "Source of truth" line
   to name this companion (fourth normative source) and the `§R*` / `R-Inv N`
   / `R-Decision N` citation namespaces. Base, hardening, and continuity
   sections all still win on anything they specify.
10. **Pydantic `AnyUrl` normalization is a load-bearing upstream defense.**
    The MCP SDK passes `req.params.uri: AnyUrl` to `read_resource` after
    pydantic validation, which **normalizes path `..` segments** before the
    handler ever runs (verified empirically: `AnyUrl('forge://.../iteration-1/../../etc/passwd')`
    yields `forge://.../etc/passwd`). The §R9.3 caller-URI `..`-escape test
    actually exercises this normalization plus `decode_uri`'s run_id regex
    — NOT `match_artifact()`'s `..`-rejection (that's pinned separately by
    the §R9.1 unit test). A future maintainer who rewrites `read_resource`
    to take `uri: str` (e.g., to drop pydantic) would silently delete this
    upstream defense. If the URI parameter type is ever changed, the
    `..`-rejection chain must be re-verified end-to-end against the new
    input shape — and a new direct handler-level `match_artifact` /
    step (i) test added to compensate.
