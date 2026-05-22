# forge-mcp — Host-Protocol and Planner Extensions

**What:** A normative companion brief that bundles three independent
extensions to forge-mcp under a single document. Each extension lives in
its own `§`-namespace so code citations remain unambiguous:

- **`§S*`** — Resource subscriptions (`subscribe_resource` +
  `notifications/resources/updated` + `notifications/resources/list_changed`).
- **`§W*`** — Wire-visible error discrimination (stable message-prefix
  taxonomy + structured `RunResult.failure_kind`).
- **`§X*`** — Cross-design-doc learning (gated aggregation of
  `design_flaws.json` across the full harness, surfaced to the planner as
  weak statistical priors).

**Source-of-truth precedence.** The base doc (`forge-mcp-design.md`) still
wins on anything it specifies. The long-run-hardening brief still wins on
anything in `§H*`. The long-run-continuity brief still wins on anything in
`§C*`. The resource-surface brief still wins on anything in `§R*`. The
cross-run-learning brief still wins on anything in `§L*`. This brief only
adds new behavior in `§S*` / `§W*` / `§X*` (and their respective
`S-Decision N` / `S-Invariant N`, `W-Decision N` / `W-Invariant N`,
`X-Decision N` / `X-Invariant N` namespaces).

**Schema-pin scope.** The `§18` schema-pin tests continue to derive from
`RunForgeInput.model_json_schema()` / `RunResult.model_json_schema()`
through the low-level `Server` registration (continuity brief made that
shift; nothing here moves it). `§W3` adds **one new optional field** to
`RunResult` (`failure_kind: str | None`); the pin extends to cover it
verbatim. `§S` does not touch `RunForgeInput` / `RunResult` shape.

**Why these three together.** They are independent in surface area but
share one trait: each closes a specific deferral that an existing brief
explicitly named (`§R-Decision 3`, `§6.3` footnote, `§L15`
forward-looking). Bundling avoids three near-empty companion docs while
keeping namespacing strict.

**What this brief deliberately does not do.** Mid-phase SDK-session
resume stays deferred (still §H18 / §C2.5). HTTP-transport stays deferred
(still §R0.1 / §C10). Multi-tool MCP surface stays out by definition
(base §21). Lineage fingerprint override stays gated on operator feedback
(§L15). The `Forward-looking` section at the bottom of this brief
inherits those deferrals verbatim.

**Reading convention.** Pseudocode in this brief carries one-line or
stub docstrings to keep the snippets readable. The implementer MUST
expand each `def` / `async def` to satisfy `scripts/check_docstrings.py`
(Rule 21: `Design:` / `Implementation:` / `Example:` labels, ≥5
non-whitespace chars each). Stub docstrings shown here are normative
about *what* the function does; the three-section form is required at
implementation time.

---

## §0. Thesis and layered precedence

### 0.1 The three gaps this brief closes

1. **`§R-Decision 3` (resource subscriptions deferred).** The resource
   brief shipped `list_resources` / `read_resource` but declared
   `subscribe: false`. The justification — "`task.update_status` already
   covers live status fan-out; subscribe-on-artifact is a forward layer
   with no consumer today" — confuses *task-level* status fan-out with
   *artifact-level* materialization. They are different signals: a host
   UI that wants to render `eval.md` the moment it appears cannot do that
   from `task.update_status` alone. The protocol-spec consumer (any MCP
   host that supports `resources/subscribe`) exists today (Claude
   Desktop, Claude Code, Continue, Zed). The "no consumer" gate is met.

2. **`§6.3` footnote (error code not wire-visible).** FastMCP collapses
   `McpError(code=…)` to an `isError=true` text result; the numeric code
   is dropped on the wire. The base doc accepted this as
   behavior-preserving but flagged it as "an enhancement beyond this
   refactor". Autonomous callers — exactly the agents driving
   `run_forge` — currently cannot programmatically distinguish bad-input
   from infra-failure without fragile message-substring parsing. A
   stable message-prefix tag plus a structured `failure_kind` on
   `RunResult` survives FastMCP intact and ends the fragility.

3. **`§L15` cross-design-doc learning ("YAGNI until evidence").** The
   cross-run-learning brief feeds the planner with summaries of *sibling*
   runs (same design fingerprint). It deferred cross-*design* learning
   citing "YAGNI until evidence that the same pattern reliably recurs
   across designs". The trick: the eligibility-gate defined here
   (`X-Decision 2`: ≥3 distinct-fingerprint occurrences inside a
   30-day window) **is** the evidence-collection mechanism. The
   implementation is silent until evidence accrues naturally — the
   speculative concern is resolved by making the gate itself the empirical
   trigger. This is the "right not easy" play that §L15 implicitly
   invited.

### 0.2 What stays unchanged (north-star preservation)

`§S` / `§W` / `§X` are all artifact-flowing or string-shaped extensions.
None of them threads live agent context across phase boundaries; none of
them allows any agent to feel context-window pressure that would induce
context anxiety (§1 of the base doc).

- `§S` notifies the *host*, not the *agents*. The notification carries a
  URI, not content. The host then re-fetches via `read_resource` — the
  same already-allowlisted, fsync-durable file. No agent ever sees a
  notification.
- `§W` adds string content to error messages and one optional field on
  `RunResult`. No agent reads `RunResult` (the autonomous loop terminates
  before that field is consulted).
- `§X` reads only **disk** (terminal `design_flaws.json` files across
  prior runs) and writes a **file** (`cross_design_patterns.md`) into
  the next planner's `add_dirs`. No agent in the new run sees content
  from old agents' live contexts — only the same artifact-flowing pattern
  §L already uses.

> **`Invariant 0 (north star, this brief):` Every mechanism added in §S,
> §W, and §X either runs deterministically in the orchestrator process or
> hands off through a file. None widens, persists, or threads any agent's
> in-context working set across a phase boundary, a run boundary, or a
> session boundary.**

### 0.3 Layered-precedence relationship in one paragraph

The base doc owns the orchestrator skeleton (`§1`–`§21`). The hardening
brief (`§H*`) added verify gate, convergence detection, iteration-boundary
crash-resume, and the `§H18` deferral list. The continuity brief (`§C*`)
added the task bridge, session-id recording, and `C-Inv 3/4` on
`sessions.json`. The resource-surface brief (`§R*`) added
`list_resources` / `read_resource` and the URI allowlist (`R-Inv 3`).
The cross-run-learning brief (`§L*`) added planner cold-start digesting
of sibling runs via the fingerprint-based lineage discovery and the
`§L7` anti-anchoring directive. This brief is **strictly additive over
all five**: nothing here renames, removes, or weakens a prior invariant;
where this brief needs to touch a prior decision, it does so by
*tightening* (e.g., `S4` reuses `R-Inv 3` exactly) rather than by
overriding.

---

## §S — Resource subscriptions

### S0. Scope, gate, and what stays out

**In scope:**

- Implement `resources/subscribe` and `resources/unsubscribe` MCP
  request handlers on the existing low-level `Server` (`server.py:52`).
- Maintain a per-session in-memory subscription registry.
- Emit `notifications/resources/updated` after each meaningful
  artifact materialization that targets a subscribable URI.
- Flip the resource capability advertisement to `subscribe: true`
  (auto-flips when an `on_subscribe_resource` handler is registered;
  context7-confirmed behavior of the Python SDK).
- Flip `listChanged: true` and emit `notifications/resources/list_changed`
  on active-run registry mutations (register / deregister).
- Cleanup-on-disconnect for both the subscription registry and the
  notifier handle.

**Out of scope:**

- Resource templates (`resources/templates/list`). Forge-mcp's allowlist
  is fully enumerated; templates buy nothing here.
- Resource roots (`roots/list_changed`). Roots are a *client* concept;
  forge-mcp is the server.
- Subscriptions on `run.log`, `iteration-N/triage_log.json`, or any
  other artifact deliberately *outside* the allowlist (R-Inv 3) — those
  remain non-served and non-subscribable.
- HTTP-transport multi-session fan-out — the design tolerates it
  (`S-Decision 4` keeps the registry session-keyed even though stdio has
  one session) but the HTTP transport itself stays deferred to §R0.1 /
  §C10.

**Gate met (vs §R-Decision 3):**

| §R-Decision 3 rejection clause | Status now |
|---|---|
| "`task.update_status` covers live status fan-out" | True for **task** status. False for **artifact** materialization (e.g., the moment `eval.md` becomes readable). These are distinct signals. |
| "subscribe-on-artifact is a forward layer with no consumer today" | The protocol-spec consumer is any MCP host that supports `resources/subscribe`. Claude Desktop, Claude Code, Continue, and Zed all do today. |

The gate clears on protocol-compliance grounds even if no *internal*
forge-mcp tooling consumes subscriptions.

### S1. Capability declaration

**SDK reality (installed version).** The MCP Python SDK currently
installed at `mcp/server/lowlevel/server.py:211-213` hardcodes
`subscribe=False` inside `get_capabilities()` regardless of whether an
`on_subscribe_resource` handler is registered. A later SDK release fixes
this (the `subscribe` flag becomes handler-driven), but the installed
floor (`claude-agent-sdk >=0.1.20, <1` and the MCP SDK that ships with it)
does NOT yet have this fix.

The implementer MUST therefore explicitly override the resource
capability at the `InitializationOptions` construction site to advertise
the truth — both `subscribe: true` and `listChanged: true`:

```python
# cli.py — when constructing InitializationOptions for server.run(...)
caps = server.get_capabilities(
    notification_options=NotificationOptions(resources_changed=True),
    experimental_capabilities={},
)
if caps.resources is not None:
    # §S1 — patch SDK-hardcoded subscribe=False until SDK ships the fix.
    caps = caps.model_copy(update={
        "resources": caps.resources.model_copy(update={"subscribe": True}),
    })
init_opts = InitializationOptions(
    server_name="forge-mcp",
    server_version=...,
    capabilities=caps,
)
```

`NotificationOptions(resources_changed=True)` flips `listChanged` via the
SDK's existing pathway. The `subscribe` flag needs the explicit
`model_copy` override.

> **`S-Decision 1`:** subscribe-capability advertisement is **explicit**
> (post-construction `model_copy` override) for as long as the installed
> SDK hardcodes `subscribe=False`. When the SDK ships the handler-driven
> flip, the override may be removed in the same patch that bumps the
> SDK floor — but until then, `S-Invariant 1` is the contract and the
> override is the mechanism.

> **`S-Decision 1b`:** subscribe-capability behavior is *handler-driven
> in intent*, not config-driven. Forge-mcp does not expose an env
> switch to disable subscriptions once the handler ships; an operator
> who doesn't want subscriptions also doesn't want the
> `subscribe_resource` code on the wire, and `R-Inv 3`'s allowlist
> already constrains scope.

> **`S-Invariant 1` (capability honesty):** `subscribe` and `listChanged`
> in the capability response always reflect actual handler / emitter
> registration. Forge-mcp never advertises a capability it does not back
> with code paths. Whether this is achieved via the SDK's
> `notification_options` + auto-flip mechanism or via an explicit
> `model_copy` override (S-Decision 1) is an implementation detail; the
> invariant is the contract.

### S2. The subscription registry

A new module `subscriptions.py` (top-level under `src/forge_mcp/`, peer
to `resources.py`) provides:

```python
"""§S2 subscription registry — in-memory, per-session, ephemeral."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class SessionHandle(Protocol):
    """Minimal Session protocol the registry needs (§S2.1).

    Design: avoids importing mcp.server.session here so the registry stays
        pure-policy and unit-testable without an SDK instance.
    Implementation: the concrete object is an mcp.server.session.ServerSession
        but we only need an identity hashable and a send_resource_updated.
    Example: SubscriptionRegistry.subscribe(session, uri).
    """

    async def send_resource_updated(self, uri: object) -> None: ...


class SubscriptionRegistry:
    """In-memory subscription state, per process, ephemeral (§S2).

    Design: stdio runs one session at a time; HTTP would multiplex. The
        registry is session-keyed in both cases so the emission path is
        transport-agnostic.
    Implementation: dict[SessionHandle, set[str]]; subscribe/unsubscribe
        are O(1); subscribed_sessions(uri) is O(N) but N is bounded by
        active-session count (1 on stdio).
    Example: registry.subscribe(session, "forge://<token>/<run>/eval.md").
    """

    _by_session: "dict[SessionHandle, set[str]]"

    def __init__(self) -> None:
        self._by_session = {}

    def subscribe(self, session: SessionHandle, uri: str) -> None:
        """Idempotent add (§S2.2)."""

    def unsubscribe(self, session: SessionHandle, uri: str) -> None:
        """Idempotent remove (§S2.2). Missing keys are no-ops."""

    def drop_session(self, session: SessionHandle) -> None:
        """Remove all subscriptions for `session` on disconnect (§S7)."""

    def subscribed_sessions(self, uri: str) -> "Iterable[SessionHandle]":
        """Return sessions that subscribed to exactly `uri` (§S5)."""

    def _sessions_view(self) -> "Iterable[SessionHandle]":
        """Iterator over all currently-keyed sessions (§S7 cleanup only).

        Design: lifespan teardown needs to walk every keyed session; the
            normal subscribe / unsubscribe / subscribed_sessions API does
            not expose that view by design.
        Implementation: return self._by_session.keys() (or a defensive
            tuple copy if the lifespan teardown might mutate while
            iterating).
        Example: for s in registry._sessions_view(): registry.drop_session(s).
        """
```

> **`S-Decision 2`:** registry is **in-memory**, not durable. Sessions
> are ephemeral by MCP semantics; on a reconnect the host must
> re-subscribe (this is also the upstream MCP-spec expectation). Durable
> subscriptions would couple to session-identity persistence — a
> different brief.

> **`S-Decision 3`:** the registry is **a module-level singleton**
> (`subscriptions._REGISTRY: SubscriptionRegistry`) constructed at
> module import time. Forge-mcp is one process per stdio invocation;
> there is no value in multi-instance.

> **`S-Invariant 2` (subscription state is ephemeral):** the registry
> holds no state across forge-mcp process lifecycles. A crashed-and-
> -restarted server starts with an empty registry; hosts must re-subscribe.

### S3. Handler registration

The two handlers register on the existing low-level `Server` instance,
following the same decorator pattern used in `server.py` for
`@server.list_resources()` and `@server.read_resource()`:

```python
"""§S3 subscribe / unsubscribe handlers (server.py addition)."""

from . import subscriptions
from .resources import decode_uri, match_artifact  # §S4 allowlist reuse


@server.subscribe_resource()
async def subscribe_resource_handler(uri: AnyUrl) -> None:
    """Validate URI against R-Inv 3 allowlist; add to registry (§S3).

    Design: subscribe-time validation matches read_resource semantics so
        a host that can read a URI can also subscribe to it (and vice
        versa). The session is read from server.request_context.session.
    Implementation: decode URI, run match_artifact, on miss raise
        McpError(-32002) using the same _resource_not_found shape; on hit
        call subscriptions._REGISTRY.subscribe(ctx.session, str(uri)).
    Example: await subscribe_resource_handler(AnyUrl("forge://<t>/<r>/eval.md")).
    """


@server.unsubscribe_resource()
async def unsubscribe_resource_handler(uri: AnyUrl) -> None:
    """Idempotent remove (§S3)."""
```

> **`S-Decision 4`:** the handler signature passes only the `AnyUrl`
> parameter (matching forge-mcp's existing `@server.read_resource()`
> decorator shape). The current `session` is obtained from
> `server.request_context.session`, the same path used elsewhere. We do
> **not** switch this brief's handlers to the v2 constructor-kwarg
> registration pattern (`on_subscribe_resource=…`); doing so would
> force migrating the existing handlers too and lives outside this
> brief's scope. If the SDK ever drops the decorator shim, the brief's
> implementation guidance is to migrate **all** handlers together, not
> just these two.

### S4. URI validation at subscribe-time (reuse R-Inv 3)

A subscribe-time URI MUST pass through the **exact same** allowlist
gate as `read_resource_handler`. The existing `_read_resource_contents_sync`
(`server.py:196-210`) translates `decode_uri`'s `ValueError` directly to
`_resource_not_found(uri)` (i.e., **-32002**), so subscribe matches:

1. `decode_uri(uri)` raises on malformed URI shape → `McpError(-32002)`
   via `_resource_not_found(uri)` — same translation as
   `server.py:210`.
2. `match_artifact(decoded)` returns `None` on non-allowlisted artifact
   → `McpError(-32002)` via `_resource_not_found(uri)`.
3. Active-run / completed-run discoverability is enforced by
   `match_artifact` already — no second copy here.

Both branches funnel through `_resource_not_found` so the wire shape is
byte-identical to `read_resource`'s rejection (S-Inv 3 / R-Inv 5
allowlist parity). If all checks pass, the URI is added to the registry.
Otherwise the error is returned to the client and **the registry is not
touched**.

> **`S-Decision 5`:** validation and registry-insert are *atomic from
> the client's view* — a successful `resources/subscribe` response
> implies a registered subscription, and a failing response implies no
> registration. This avoids host-side races where a partial-subscribe
> would silently swallow notifications.

> **`S-Invariant 3` (allowlist parity):** any URI that `read_resource`
> would reject, `subscribe_resource` also rejects, with the same code
> (-32002) and same error-data shape. The allowlist is single-sourced
> via `resources.match_artifact`.

### S5. Notifier abstraction and emission hooks

#### S5.1 The Notifier seam

Emission needs to happen from inside the orchestrator (where artifacts
materialize) without coupling orchestrator code to the MCP SDK. A
Protocol seam in `subscriptions.py`:

```python
class ResourceNotifier(Protocol):
    """§S5.1 — orchestrator-facing emission seam, MCP-SDK-free.

    Design: lets pure orchestrator code call notifier.notify(uri) without
        importing mcp.types or knowing how many sessions are subscribed.
    Implementation: concrete StdioNotifier consults the registry and
        invokes session.send_resource_updated; tests use a recording stub.
    Example: await notifier.notify("forge://<t>/<r>/eval.md").
    """

    async def notify(self, uri: str) -> None: ...


class RegistryNotifier:
    """Concrete Notifier reading from a SubscriptionRegistry (§S5.1)."""

    _registry: SubscriptionRegistry

    def __init__(self, registry: SubscriptionRegistry) -> None:
        self._registry = registry

    async def notify(self, uri: str) -> None:
        for session in self._registry.subscribed_sessions(uri):
            # AnyUrl construction error must NOT crash the orchestrator
            # path; emission is best-effort. §S5.4.
            try:
                await session.send_resource_updated(AnyUrl(uri))
            except Exception:  # noqa: BLE001  # §S5.4 fail-soft
                _S5_LOGGER.warning("send_resource_updated failed", exc_info=True)
```

> **`S-Decision 6`:** notifier emission is **fail-soft**. A failed
> `send_resource_updated` (closed pipe, slow drain, malformed URI) is
> logged at WARN and swallowed; it MUST NOT propagate into the
> orchestrator's iteration loop. This is the same posture as `gitguard`
> failures (`§11.3`) and `task.update_status` failures (`§C1.5`):
> **observability-only side-channels are explicitly carved out from
> Rule 8's "fail fast and loud" rule**. Rule 8 governs preflight and
> the load-bearing run paths; a notification a host did not get is not
> a degraded mode of the *run*, only of the *observation*. The run's
> own success/failure determination is unaffected.

#### S5.2 Where emission hooks fire (the materialization sites)

**Framing rule (`S-Decision 7b`):** emission fires after **every fsync
site that lands an allowlisted artifact** (per §R-Inv 3 / `S-Invariant 3`
allowlist parity). The table below enumerates the concrete sites that
must be wired in v1; any new allowlisted artifact added by a future
brief must add a row here in the same patch (a §S-test pins this — see
§-Tests `test_every_allowlisted_artifact_has_emission_site`).

| Materialization | URI shape | Trigger location |
|---|---|---|
| `state.json` rewritten | `forge://<t>/<r>/state.json` | After every successful `write_state` (§7 / §H2) |
| `plan/plan.md` written | `forge://<t>/<r>/plan/plan.md` | After plan-phase fsync |
| `plan/sessions.json` written | `forge://<t>/<r>/plan/sessions.json` | After plan-phase fsync (continuity §C2.5) |
| `inputs/prior_attempts.md` written | `forge://<t>/<r>/inputs/prior_attempts.md` | After §L6 digest write (planner cold-start) |
| `inputs/prior_attempts-overflow.md` written | `forge://<t>/<r>/inputs/prior_attempts-overflow.md` | After §L6.2 overflow write (if cap hit) |
| `inputs/cross_design_patterns.md` written | `forge://<t>/<r>/inputs/cross_design_patterns.md` | After §X3 digest write (planner cold-start, always-write per X-Inv 6) |
| `inputs/git-state.txt` written | `forge://<t>/<r>/inputs/git-state.txt` | After cold-start git-state capture in `Orchestrator.run()` (`engine.py:322`) |
| `inputs/git-uncommitted.txt` written | `forge://<t>/<r>/inputs/git-uncommitted.txt` | After cold-start git-uncommitted capture in `Orchestrator.run()` (`engine.py:325`) |
| `iteration-N/contract.md` written | `forge://<t>/<r>/iteration-N/contract.md` | After planner / remediation handoff write (`phases.py:313, 331, 341, 373`) |
| `iteration-N/summary.md` written | `forge://<t>/<r>/iteration-N/summary.md` | After generator phase fsync |
| `iteration-N/verify.txt` written | `forge://<t>/<r>/iteration-N/verify.txt` | After verify-gate write (§H1 / `phases.py:493`) |
| `iteration-N/git-violation.txt` written | `forge://<t>/<r>/iteration-N/git-violation.txt` | After Rule-11 violation synthesis (§11.3 / `phases.py:567`) |
| `iteration-N/eval.md` written | `forge://<t>/<r>/iteration-N/eval.md` | After evaluator-phase fsync |
| `iteration-N/eval.json` written | `forge://<t>/<r>/iteration-N/eval.json` | Same emission as eval.md (single triggered notify per phase) |
| `iteration-N/triage.json` written | `forge://<t>/<r>/iteration-N/triage.json` | After triage-step fsync |
| `iteration-N/sessions.json` written / rewritten | `forge://<t>/<r>/iteration-N/sessions.json` | After each `write_sessions_json` (continuity §C2.5) |
| `unresolved-gaps-overflow.md` written | `forge://<t>/<r>/unresolved-gaps-overflow.md` | After gap-overflow render (if cap hit; run-root path per `resources.py:_ALLOWED_ARTIFACTS`) |
| `design-flaw-gaps-overflow.md` written | `forge://<t>/<r>/design-flaw-gaps-overflow.md` | After design-flaw-overflow render (if cap hit; run-root path) |
| `design_flaws.json` written | `forge://<t>/<r>/design_flaws.json` | After triage classification (§11.1; run-root path, `artifacts.py:256`) |
| Active-run registered / deregistered | (no per-URI emission; list_changed) | §S6 |

**Deliberately not emitted:**

- `iteration-N/status.log` appends — covered by `task.update_status`
  task-level fan-out (§C1.5). Per-append emission would be O(thousands)
  on a 10-hour run; per-iteration batching would lie about cadence.
- `run.log` — never resource-exposed (R-Inv 3). Subscribing is rejected
  at S4.
- `inputs/design.md` and `inputs/design.fingerprint` — write-once at run
  setup. A subscribe arrives **after** these write-once artifacts are
  already on disk (preflight precedes the run); no future
  materialization event exists to notify on. A subscribe to these URIs
  is accepted (S-Inv 3 allowlist parity), but the subscription's only
  signal is `list_changed` if the active-run is pruned. Document this
  asymmetry in the host-facing README.

> **`S-Decision 7`:** emission is **per-meaningful-write, not
> per-append**. `status.log` is the only mutable-append artifact in the
> exposed allowlist, and `task.update_status` already gives hosts the
> equivalent live signal. Excluding it from subscription emission
> prevents the chattiness that would make the entire subscribe surface
> useless.

> **`S-Decision 8`:** emission fires **after fsync, not before**. The
> host must be able to fetch durable content the moment the
> notification arrives. A pre-fsync emission would race with a power
> loss — the notification would say "go read" while the file has not
> survived to disk.

#### S5.3 Wiring the notifier into the orchestrator

The notifier is request-scoped (tied to the calling tool's session). It
is constructed in `server.py`'s `call_tool` handler and passed through
to the orchestrator the same way `RunContext` already threads
per-request state (Invariant 3).

```python
# server.py — inside the call_tool handler for run_forge.
# `Orchestrator` is constructed with positional + keyword args, then
# `.run()` is invoked with no further args (matches the current
# server.py construction site). The notifier is threaded as a new
# constructor kwarg, alongside the existing `task=`, `task_id=`,
# `harness_token=` kwargs already used by the continuity brief's
# task-API path.
notifier = subscriptions.RegistryNotifier(subscriptions._REGISTRY)
result = await Orchestrator(
    prepared, inputs, config, ctx, drivers,
    task=task, task_id=task_id, harness_token=harness_token,
    notifier=notifier,  # §S5.3 — new kwarg
).run()
```

Inside the orchestrator, the notifier is invoked from a small adapter
that knows the run_id/harness_token and constructs the right URI:

```python
class _Emitter:
    """§S5.3 — orchestrator-internal helper that builds URIs and emits."""

    _notifier: subscriptions.ResourceNotifier
    _harness_token: str
    _run_id: str

    def _uri(self, subpath: str) -> str:
        """Compose a `forge://<token>/<run-id>/<subpath>` URI (§R1.2).

        Design: single URI-construction site inside the orchestrator so
            emission sites cannot drift from the §R1.2 scheme.
        Implementation: f"forge://{self._harness_token}/{self._run_id}/{subpath}".
        Example: emitter._uri("iteration-3/eval.md").
        """

    async def emit_state(self) -> None:
        await self._notifier.notify(self._uri("state.json"))

    async def emit_iteration(self, n: int, filename: str) -> None:
        await self._notifier.notify(self._uri(f"iteration-{n}/{filename}"))
```

> **`S-Decision 9`:** the `_Emitter` lives in the orchestrator package
> (`orchestrator/emitter.py`, new) and never imports the MCP SDK
> directly. The single SDK dependency stays at the `RegistryNotifier`
> seam in `subscriptions.py`. This matches the `_claude` / `_codex`
> Protocol-seam pattern from `§5.2`.

#### S5.4 Concurrency, ordering, and backpressure

- **Concurrency:** the registry is asyncio-safe by virtue of
  forge-mcp's single-event-loop assumption (§8). No `asyncio.Lock`
  needed; subscribe / unsubscribe / emit all run on the same loop.
- **Ordering:** for a single subscriber, notifications arrive in the
  order their corresponding writes completed. This is automatic given
  the loop-based emission.
- **Backpressure:** `send_resource_updated` returns immediately on
  stdio (writes to stdout); slow hosts can be detected only by `epipe`
  errors, which §S-Decision 6 catches at WARN. Forge-mcp never blocks
  the orchestrator on host drain.

> **`S-Invariant 4` (emission-after-durable-write):** every
> `notifications/resources/updated` is emitted strictly after the
> corresponding artifact's durable-write returns. For `state.json`
> the durable write is the existing fsync+dir-fsync in `state.write_state`
> (§7 / §H2). For atomic-replace artifacts (`plan.md`, `eval.md`,
> `eval.json`, `triage.json`, `iteration-N/sessions.json`) the durable
> write is `os.replace` returning — `artifacts.atomic_write_text` and
> `atomic_write_json` MUST be upgraded with an `fsync` of the temp file
> before `os.replace` so the host's re-fetch under power-loss never sees
> a half-written or missing file. The §S5.2 implementation MUST do this
> upgrade in the same patch that wires emission; the invariant is the
> contract, fsync is the mechanism.

### S6. `notifications/resources/list_changed`

The active-run registry (R3.1, in `resources.py`) is the source-of-truth
for which run-resources are discoverable. Registry mutations are:

- **Register**: orchestrator transitions from `running` to the first
  per-iteration state (a new run becomes discoverable).
- **Deregister**: orchestrator hits a terminal state AND `R3.2`
  retention prunes the run.

Each mutation fires a single `send_resource_list_changed()` on every
session (no URI keying — `list_changed` is a global signal).

```python
# resources.py — extension of the existing register_active_run /
# deregister_active_run mutators (no rename, no signature change).
# Real signatures today (per resources.py:170, 187):
#     def register_active_run(scope: _ResourceScope) -> None
#     def deregister_active_run(harness_token: str, run_id: str) -> None
# Both are sync and called from async-context call sites (engine.py:249, 414).

def register_active_run(scope: _ResourceScope) -> None:
    key = (scope.harness_token, scope.run_id)
    assert key not in _ACTIVE_RUNS, f"double-register: {key!r}"
    _ACTIVE_RUNS[key] = scope
    # §S6 — broadcast list_changed best-effort. We use
    # `asyncio.get_running_loop().create_task(...)` rather than
    # `asyncio.create_task` directly so a sync caller without a running
    # loop fails loud (RuntimeError) instead of silently dropping the
    # signal. All current call sites (engine.py:249, 414) sit inside an
    # async context, so the runtime-error guard is defensive.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop → no host to notify; observability-only path
    loop.create_task(_broadcast_list_changed())


def deregister_active_run(harness_token: str, run_id: str) -> None:
    _ACTIVE_RUNS.pop((harness_token, run_id), None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_broadcast_list_changed())
```

**Shutdown semantics.** `deregister_active_run` is reached from
`engine.py:414` inside a `finally` block. On cancellation that finally
runs while the outer task is being torn down. The fire-and-forget
broadcast task may not complete before the loop tears down — that is
acceptable: `list_changed` is observability-only (S-Decision 6's
carve-out applies), and a host that misses the deregister signal will
re-discover the absence at its next `list_resources` poll.

> **`S-Decision 10`:** `list_changed` is broadcast to **all sessions**,
> not gated on subscription state. The MCP spec defines it as a
> connection-level signal, not a per-URI subscription. Forge-mcp
> follows the spec.

> **`S-Decision 11`:** completed-but-not-pruned runs do **not** trigger
> `list_changed` on their own. Discoverability changes only at
> register-time and at prune-time. A run transitioning from `running`
> to `completed` is still discoverable; the host sees the state change
> via the `state.json` subscription (S5.2), not the list_changed
> channel.

### S7. Session lifecycle and cleanup

The SDK exposes session-disconnect via the stream-close of the
underlying transport. The low-level `Server` runs each session inside
a `with` block — when the block exits, the session is dead.

Forge-mcp's stdio entrypoint is `cli.py` (the `forge serve` command),
which is where the `mcp.server.stdio.stdio_server()` lifespan lives.
Cleanup is hooked there, not in `server.py`:

```python
# cli.py — forge serve entrypoint with §S7 cleanup
async def _serve() -> None:
    """§S7 — stdio server lifespan with on-disconnect subscription cleanup."""
    async with stdio_server() as (read_stream, write_stream):
        try:
            await server.run(read_stream, write_stream, init_opts)
        finally:
            # Drop any subscriptions tied to the now-dead session.
            # Stdio has one session at a time; iterate defensively.
            for session in list(subscriptions._REGISTRY._sessions_view()):
                subscriptions._REGISTRY.drop_session(session)
```

> **`S-Decision 12`:** the registry exposes a private `_sessions_view`
> iterator for cleanup. It is private because all *normal* registry
> writes go through `subscribe` / `unsubscribe` / `drop_session`; the
> view is for the disconnect path only.

> **`S-Invariant 5` (no zombie subscriptions):** at any process-quiescent
> moment, the registry contains only subscriptions whose underlying
> session is still open. Disconnected sessions' entries are removed
> synchronously inside the lifespan teardown.

### S8. Decisions log

`S-Decision 1` — capability advertisement is **explicit** (`model_copy` override at the `InitializationOptions` site) for as long as the installed SDK hardcodes `subscribe=False`.
`S-Decision 1b` — capability behavior is *handler-driven in intent*, not config-driven; no env switch to disable subscriptions once the handler ships.
`S-Decision 2` — registry is in-memory, ephemeral; hosts re-subscribe on reconnect.
`S-Decision 3` — module-level singleton registry (one process = one registry).
`S-Decision 4` — keep decorator-style registration to match existing handlers.
`S-Decision 5` — subscribe + registry-insert are atomic from the client's view.
`S-Decision 6` — `send_resource_updated` failures are logged WARN and swallowed.
`S-Decision 7` — emission per-meaningful-write, not per-append (excludes status.log).
`S-Decision 7b` — emission fires at every fsync site that lands an allowlisted artifact (§S5.2 framing rule); new artifacts MUST add an emission site in the same patch.
`S-Decision 8` — emission fires strictly after fsync.
`S-Decision 9` — `_Emitter` lives in `orchestrator/`; SDK seam stays in `subscriptions.py`.
`S-Decision 10` — `list_changed` is broadcast (per MCP spec), not per-URI.
`S-Decision 11` — completed-not-pruned runs don't fire `list_changed`.
`S-Decision 12` — registry exposes private `_sessions_view` for lifespan cleanup only.

### S9. Invariants

`S-Inv 1` (capability honesty)  
`S-Inv 2` (registry ephemeral, never persisted)  
`S-Inv 3` (allowlist parity with `read_resource`, single-sourced)  
`S-Inv 4` (emission-after-fsync)  
`S-Inv 5` (no zombie subscriptions)

---

## §W — Wire-visible error discrimination

### W0. Scope, gate, and what stays out

**In scope:**

- A stable, versioned, six-kind taxonomy of error categories that
  forge-mcp's `run_forge` tool can emit.
- A bracketed prefix on every error-bearing message string
  (`[FORGE_ERR_<KIND>] <message>`) so callers can match without
  substring parsing.
- A new optional `RunResult.failure_kind: str | None` field carrying the
  same lowercase kind as a structured signal on terminal-state results.
- A single source for the kind→prefix mapping (`errors.py` constants);
  no string literals scattered across modules.

**Out of scope:**

- A structured JSON-shaped error payload as the *transport* body. FastMCP
  flattens MCP errors to text on the wire (§6.3 footnote, unchanged).
  The message-prefix approach is the path that *survives* that
  flattening; any alternative would require swapping the transport.
- A "warnings" prefix taxonomy. `RunResult.warnings` already exists and
  is structured; no need for a parallel string-tag scheme.
- A kind for the per-iteration generator non-fatal retries. Those are
  per-attempt internal events, not terminal-state error categories.

**Gate met (vs §6.3 footnote):**

The §6.3 footnote framed wire-visible-error-discrimination as "beyond
the behavior-preserving refactor". The behavior-preserving refactor has
shipped (the brief base ships). Adding the prefix is now an additive
enhancement, not a behavior change.

### W1. The six-kind taxonomy

The taxonomy splits cleanly along the **§6.3 bright line** — pre-run
failures are raised as `McpError`; terminal run states are returned as
`RunResult`. The prefix tag and `failure_kind` value are the same kind
in both paths; only the wire mechanism differs.

**Pre-run (raised `McpError`):**

| Prefix | `failure_kind` value | When | Code |
|---|---|---|---|
| `[FORGE_ERR_INVALID_PARAMS]` | `"invalid_params"` | Pydantic validation, malformed `target_dir`, schema mismatch | `-32602` |
| `[FORGE_ERR_INFRA_FAILURE]` | `"infra_failure"` | Pre-run disk-full, fs permission denied, lockfile / harness-dir setup failure | `-32000` |
| `[FORGE_ERR_AUTH]` | `"auth"` | Claude / Codex authentication failure surfaced by `prepare_run`'s preflight | `-32000` |
| `[FORGE_ERR_LOCK_HELD]` | `"lock_held"` | `lockfile.acquire()` finds an active foreign lock | `-32000` |

**Terminal (returned in `RunResult`):**

| Prefix | `failure_kind` value | When | `RunResult` shape |
|---|---|---|---|
| `[FORGE_ERR_INFRA_FAILURE]` | `"infra_failure"` | Mid-run driver subprocess exit ≠ 0; convergence-failure (§H3); verify-failure (§H1) that the run cannot recover from | `status="failed", failure_kind="infra_failure"` |
| `[FORGE_ERR_TIMEOUT]` | `"timeout"` | Runtime cap (`max_runtime_minutes`) elapsed | `status="incomplete", failure_kind="timeout"` |

The `INFRA_FAILURE` row appears in **both** tables intentionally: the
same kind covers both pre-run setup failures (raised) and mid-run
unrecoverable infra failures (returned). The bright line is preserved
because the path (raise vs return) is dictated by *when* the failure
occurs, not by the kind. `AUTH` and `LOCK_HELD` are pre-run-only by
construction.

**Cancellation (no `RunResult` produced):**

| Prefix | `failure_kind` value | When | Path |
|---|---|---|---|
| `[FORGE_ERR_CANCELLED]` | `"cancelled"` (forensic-only) | Client disconnect → `cancelling → failed` per §8.5 step 4 → re-raise `CancelledError` per §8.5 step 5 | `state.json` records `state="failed", cancelled=True`; the tool re-raises and no `RunResult` reaches the client |

Cancellation is the **only** path where the kind is purely **forensic**:
because §8.5 step 5 re-raises `CancelledError`, `build_result` is never
called on the cancellation path (`engine.py` re-raises before reaching
the `build_result` site), and no `RunResult` exists to carry
`failure_kind="cancelled"`. The kind label still applies to:

- The `state.json` `cancelled=True` flag (which any forensic reader,
  including the resource surface, can map to the `cancelled` taxonomy).
- The `error_message` / `message` body of any McpError-like surface a
  future inspect tool might construct from the forensic state.

`failure_kind="cancelled"` is therefore reserved in the `FailureKind`
Literal for forensic-derived views, but `RunResult.failure_kind` will
never receive it under the current §8.5 contract. A future brief that
changes §8.5 step 5 to return a forensic `RunResult` instead of
re-raising would be free to populate this value; this brief does not
propose that change.

**Coverage rule:** every error-bearing exit (raised `McpError` or
`status` in `{"failed", "incomplete"}`) MUST emit one of these six kinds.
A future error category is added by extending the taxonomy in
`errors.py`, the schema-pin test, and updating any new emission site —
never by silently leaving a kind unset.

> **`W-Decision 1`:** the taxonomy has **exactly six values**. A
> seventh requires a brief revision; reviewers MUST push back on
> single-PR additions that don't update the spec.

> **`W-Decision 2`:** convergence-failure and verify-failure (§H1, §H3)
> map to `failure_kind="infra_failure"` when they manifest as `failed`
> and to `failure_kind="timeout"` when they manifest as `incomplete`
> via cap-hit. They do not receive their own kinds because callers want
> "is this my fault or the system's" — convergence-failed is the same
> "system gave up" signal as out-of-time.

> **`W-Invariant 1` (six-kind closed taxonomy):** at any commit on
> `main`, exactly six distinct `failure_kind` literal values appear in
> `errors.py`, in the prefix table, and in the pinning test (§W-Tests).
> Drift is a CI failure.

### W2. The message-prefix tag

The prefix is **the bracketed token at the head of the message**, made
of five fixed parts in this exact order: an opening bracket `[`, the
literal `FORGE_ERR_`, the kind in uppercase, a closing bracket `]`, and
one ASCII space before the message body. Total length is variable —
shortest is `[FORGE_ERR_AUTH] ` (16 chars for the bracketed token plus
the space = 17), longest is `[FORGE_ERR_INVALID_PARAMS] ` (26 + 1 = 27).
Callers MUST match by structure (regex `^\[FORGE_ERR_[A-Z_]+\] `), not
by fixed length.

```python
# errors.py — addition

from typing import Final, Literal

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


def tag(kind: FailureKind, body: str) -> str:
    """Construct the verbatim message text used on the wire (§W2).

    Design: hosts and autonomous callers match on the verbatim bracketed
        prefix; never construct it ad-hoc — drift is the failure mode.
    Implementation: f"{_PREFIX[kind]} {body}". Single source.
    Example: tag("timeout", "runtime cap of 600 minutes exceeded").
    """
    return f"{_PREFIX[kind]} {body}"
```

> **`W-Decision 3`:** `errors.tag` is the **only** place the bracketed
> prefix string is constructed in source. Any `f"[FORGE_ERR_..."` string
> literal elsewhere in `src/` is a lint failure (a new rule in
> `scripts/check_docstrings.py` or a new `ruff` per-file-ignore catch).
> The single source eliminates the renaming drift §6.3 currently risks.
> Scope: this decision unifies the **string-prefix** taxonomy only. The
> JSON-RPC numeric codes (`-32602` invalid_params, `-32000` server_error,
> `-32002` resource_not_found) remain where they already live
> (`server.py:49` for `INVALID_PARAMS`, `server.py:68` for
> `_RESOURCE_NOT_FOUND_CODE`, `preflight.py:27-28` for the preflight
> copies) — they are MCP-spec values, not forge-mcp taxonomy. Unifying
> numeric codes is out of scope here.

> **`W-Invariant 2` (prefix verbatim stability):** the bracketed prefix
> table is API. Renaming any of the six prefixes is a wire-shape break
> equivalent to changing a tool's input schema. The schema-pin test
> (§W-Tests) freezes the table by character.

### W3. `RunResult.failure_kind`

A new optional field is added to `RunResult` (models.py):

```python
class RunResult(BaseModel):
    # ... existing fields unchanged ...
    failure_kind: FailureKind | None = Field(
        default=None,
        description=(
            "§W3 — categorical kind for status in {failed, incomplete}; "
            "always None for status=completed. Mirrors the bracketed "
            "[FORGE_ERR_<KIND>] prefix in `message` / `error_message`."
        ),
    )
```

**Population rules:**

- `status="completed"` → `failure_kind` is `None`.
- `status="failed"` → `failure_kind == "infra_failure"`.
- `status="incomplete"` → `failure_kind == "timeout"` (the only path
  that yields an incomplete return today).

`"invalid_params"`, `"auth"`, and `"lock_held"` are **pre-run-only** by
construction: they raise `McpError` (see §W1 first table) and never
appear on `RunResult`. A mid-run auth failure escaping preflight is a
preflight bug, not a new RunResult path — fix preflight (Rule 8), don't
broaden the taxonomy.

`"cancelled"` is **forensic-only** by construction (see §W1 third
table): §8.5 step 5 re-raises, so `RunResult` is never built on the
cancellation path. The kind survives in `state.json`'s `cancelled=True`
flag and on any synthesized forensic view.

**Ordering relative to §8.5.** The actual call order in the orchestrator
(`lifecycle.py` then `engine.py`) is:

1. State-machine transition writes the terminal state (`failed` /
   `incomplete`) — `lifecycle.py:116, 134, 172`.
2. Lock is released — §8.5 step 3 already ran before step 4's transition.
3. `build_result(...)` is called — `engine.py:392`.

So `failure_kind` is **derived by `build_result` from the terminal
state-machine state** (the `reason` and `state` fields the transition
wrote), not pre-populated on a half-built result. The single population
point is the `build_result` call site in `result.py`, which reads the
terminal state to choose the kind. By the time `build_result` runs, the
lock is already released — observers seeing the terminal `RunResult`
can assume the lock is free, matching §8.5's invariant.

> **`W-Decision 4`:** `failure_kind` is the **structured** signal;
> `message`'s bracketed prefix is the **string** signal. Both are
> populated on every error path. Hosts that parse JSON read
> `failure_kind`; hosts that scrape text see the prefix. A future
> transport that preserves JSON-RPC codes could drop the prefix without
> losing information (covered by `W-Invariant 3`).

> **`W-Invariant 3` (structured-prefix parity):** for every terminal
> `RunResult`, `failure_kind` is `None` iff the message has no bracketed
> prefix; otherwise the kind in `failure_kind` matches the bracketed
> prefix's kind (case-for-case). Tested in §W-Tests.

### W4. Backwards-compatibility

The prefix is **additive**. A caller that ignores the prefix and reads
the body still gets the same human-readable message as before:

```
[FORGE_ERR_TIMEOUT] runtime cap of 600 minutes exceeded
```

vs the pre-§W

```
runtime cap of 600 minutes exceeded
```

For autonomous callers (the primary forge-mcp user) the prefix is
**ignorable** for matching legacy patterns; new code is encouraged to
match on the prefix and consult `failure_kind`.

> **`W-Decision 5`:** the prefix is **inserted at the front**, not
> appended. Callers that already substring-search on a specific old
> message body still match (the body string is unchanged).

### W5. Decisions log

`W-Decision 1` — exactly six kinds; closed taxonomy.
`W-Decision 2` — convergence/verify failures map to `infra_failure` / `timeout`, not their own kinds.
`W-Decision 3` — `errors.tag` is the single prefix-construction site.
`W-Decision 4` — both signal channels (structured kind + string prefix) populate on every error.
`W-Decision 5` — prefix is prepended; body is unchanged for legacy matching.

### W6. Invariants

`W-Inv 1` (six-kind closed taxonomy at every commit)
`W-Inv 2` (bracketed prefix is API; verbatim-stable)
`W-Inv 3` (structured-prefix parity)

---

## §X — Cross-design-doc learning

### X0. Scope, gate, and the §L15-evidence mechanism

**In scope:**

- Aggregate `design_flaws.json` artifacts across **all** terminal runs
  in the harness, irrespective of design fingerprint (§L's lineage uses
  fingerprint-matched siblings; this brief crosses fingerprints).
- Apply a frequency-and-recency gate: a pattern surfaces only after
  **≥3 distinct-fingerprint runs** in the **last 30 days** have
  recorded it. The gate is the empirical trigger.
- Render a separate digest, `cross_design_patterns.md`, distinct from
  `prior_attempts.md` (§L). Both flow to the planner via `add_dirs`.
- Add an anti-anchoring directive to `planner_system.md` strictly
  stronger than §L7's, framing cross-design patterns as **statistical
  priors**, not requirements.
- Preserve §L's sibling-run digest priority: when both digests would
  recommend conflicting moves, the planner is instructed to follow the
  sibling-run signal.

**Out of scope:**

- Cross-`target_dir` learning. The cross-design aggregation reads only
  the current harness; multi-tenant pooling stays §L15-deferred
  (privacy / tenant-isolation considerations).
- Cross-design feeding the **Evaluator** phase. §L15 explicitly warned
  this would anchor the gatekeeper; the warning still applies. The
  evaluator continues to read only its own iteration's artifacts.
- Cross-design feeding remediation triage. Same gatekeeper-anchoring
  concern as the evaluator; triage stays single-iteration.
- Self-cap on aggregation compute (§L16 risk 10) — this brief inherits
  the §H9 prune cadence as the primary bound. A scan-time deadline can
  be added later if pathological harness depths emerge.

**Gate met (vs §L15 "YAGNI until evidence"):**

§L15's rejection was that adopting cross-design learning was speculative
because there was no observed evidence that the same pattern recurs
across designs. The X-side gate (X-Decision 2) flips this on its head:
the implementation **is** the evidence-collection. Until ≥3
distinct-fingerprint terminal runs in the last 30 days record a pattern,
nothing surfaces. The first design doc ever fingerprinted sees an empty
digest (no priors). The Nth design starts seeing patterns when N has
been around long enough to make the gate fire. The implementation never
acts on patterns that have not crossed the empirical threshold.

> **`X-Invariant 0` (gate-as-evidence):** at any moment, every pattern
> surfaced into `cross_design_patterns.md` has been observed in ≥3
> terminal runs with distinct design fingerprints inside a 30-day
> rolling window. No pattern below threshold is ever surfaced.

### X1. The aggregator: `cross_design.py`

A new module at `src/forge_mcp/orchestrator/cross_design.py`, peer to
`lineage.py` (§L4), reachable only by the planner cold-start path
(§L-Inv 0: no live context — cross-run learning crosses the run
boundary via files only).

```python
"""§X1 cross-design-doc pattern aggregator. Pure-policy, disk-read-only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_RECENCY_WINDOW = timedelta(days=30)  # X-Decision 2 (window)
_FREQUENCY_FLOOR = 3                   # X-Decision 2 (floor)
_PER_PATTERN_BYTES_CAP = 1024          # X-Decision 5 — keep digest bounded


@dataclass(frozen=True)
class CrossPattern:
    """Aggregated cross-design pattern (§X1).

    Design: the smallest unit the planner sees; carries enough context
        to be useful as a *prior* but not so much that it anchors the
        planner to a specific past design.
    Implementation: fault_kind from triage taxonomy (§11.1); observed_in
        is the distinct fingerprint count; first_seen / last_seen mark
        recency boundary.
    Example: CrossPattern("missing_input_validation", 5, ..., ...).
    """

    fault_kind: str
    distinct_fingerprints: int
    last_seen: datetime
    rendered_summary: str


def find_cross_design_patterns(harness_dir: Path) -> list[CrossPattern]:
    """Aggregate design_flaws.json across all terminal runs (§X1).

    Design: read-only; reuses §L4's terminal-state filter so failed and
        completed runs both contribute (cancelled runs do not, mirroring
        §L-Inv 3). Reads the POST-CLASSIFIED `fault_kind` field from
        run-root `design_flaws.json` — the sidecar §11.1's `classify_gaps`
        writes after weak / colliding / missing-kind rows are demoted.
        Raw pre-classification rows in `triage.json` are NEVER consumed
        (they are the iteration-local TriageResult written before
        classification runs, and would re-introduce exactly the noise
        §11.1 filters out).
    Implementation: iterate harness_dir/*/state.json, keep terminal ∧
        not-cancelled, read each run's run-root `design_flaws.json`
        (path per `_ALLOWED_ARTIFACTS` entry `"design_flaws.json"`),
        bucket by fault_kind, apply §X2 eligibility gate, emit.
    Example: patterns = find_cross_design_patterns(Path('/repo/.harness')).
    """


def render_digest(
    patterns: list[CrossPattern],
    *,
    max_total_bytes: int = 8 * 1024,        # X-Decision 5 (total digest cap)
    max_per_pattern_bytes: int = 1024,      # X-Decision 5 (per-pattern cap)
) -> str:
    """Render the §X3 digest markdown from an aggregator output (§X1).

    Design: single owner of the verbatim §X3 header text, the per-pattern
        "ADVISORY ONLY" line, and the byte-cap enforcement (last-seen-first
        truncation). Pinning the renderer behind one function keeps the
        anti-anchoring framing intact across edits.
    Implementation: emit the verbatim header (§X-Inv 2), then sort patterns
        by `last_seen` ascending so older-last-seen patterns drop first
        when the cumulative byte counter exceeds `max_total_bytes`. Truncate
        each pattern body to `max_per_pattern_bytes` with an explicit
        marker.
    Example: digest_text = render_digest(patterns).
    """
```

> **`X-Decision 1`:** aggregation is **disk-read-only**; the module
> does no network I/O, no SDK calls, and never writes outside of
> producing the final digest text. It is unit-testable with a tmp_path
> fixture and a few synthetic `state.json` / `design_flaws.json` /
> `triage.json` files.

> **`X-Decision 2`:** the **frequency floor is 3** and the **recency
> window is 30 days**. These are not env-configurable in v1 —
> overrideable knobs invite tuning before the system has produced
> enough data to justify any specific tuning. Revisit when production
> data exists.

> **`X-Decision 3`:** **distinct-fingerprint** counts, not raw-run
> counts. A single design that hits the same flaw across 4 retries
> contributes **one** to the count. This prevents a single chronic
> failure-prone run from inflating a pattern past the gate.

### X2. Eligibility gate (frequency + recency + distinct-fingerprint)

Pseudocode for the gate, applied per `fault_kind` bucket:

```python
def _eligible(bucket: list[_Hit], now: datetime) -> bool:
    recent = [h for h in bucket if h.observed_at >= now - _RECENCY_WINDOW]
    fingerprints = {h.fingerprint for h in recent}
    return len(fingerprints) >= _FREQUENCY_FLOOR
```

**Three independent filters:**

1. **Recency:** drop hits older than 30 days.
2. **Distinct-fingerprint:** count unique fingerprints, not raw runs.
3. **Frequency floor:** require count ≥ 3.

A pattern that aged out (last hit > 30 days ago) is **dropped**, not
preserved. This is intentional — drift in agent behavior, framework
versions, and SDK semantics makes old patterns of dubious relevance.

> **`X-Invariant 1` (gate is conservative):** if a pattern's eligibility
> is ambiguous (clock skew, missing fingerprint, malformed
> `design_flaws.json`), it is **rejected from the digest**, not
> surfaced. Surfacing a wrong pattern actively misleads the planner;
> missing a correct one only forgoes a marginal hint. The aggregator
> still logs the ambiguity at WARN (so operators can audit) — this is
> "**reject loudly to the log, never silently to the planner**", not
> a Rule-8 silent fallback.

### X3. Digest shape: `cross_design_patterns.md`

Written to a per-run scratch path (e.g., `<run-dir>/inputs/cross_design_patterns.md`)
and added to the planner's `add_dirs` alongside `prior_attempts.md`.

Verbatim shape:

```
# Cross-design patterns (advisory)

These patterns have been observed across **multiple distinct design
documents** in this harness over the last 30 days. They are
**statistical priors**, not requirements for the current design.
Treat each pattern as a *weak signal* — check whether your current
design's requirements actually suggest this pattern is relevant.
**If a pattern doesn't apply, IGNORE IT.** The sibling-run summary in
`prior_attempts.md` (when present) is a stronger signal than anything
here, and conflicts must resolve in favor of sibling-run guidance.

## Pattern: <fault_kind_1>

ADVISORY ONLY — observed in N distinct designs in the last 30 days; last seen YYYY-MM-DD.

<rendered_summary_1>

## Pattern: <fault_kind_2>

ADVISORY ONLY — observed in N distinct designs in the last 30 days; last seen YYYY-MM-DD.

<rendered_summary_2>

(... etc, up to the per-digest byte cap)
```

**Hard caps to keep the planner's working set bounded:**

- Per-pattern body bound: **1 KiB** (`_PER_PATTERN_BYTES_CAP`).
- Total digest bound: **8 KiB** (matching §L5.2's per-summary cap; one
  cross-pattern is shorter than a full sibling-run summary, so this
  permits ~6–8 patterns to surface).

> **`X-Decision 4`:** digest content is **prose**, not structured.
> Structured input would invite the planner to anchor on schema-shape;
> prose framed as "ADVISORY ONLY" lands as guidance.

> **`X-Decision 5`:** per-pattern 1 KiB cap, total digest 8 KiB cap.
> Hard truncation: cumulative bytes are tallied and surplus patterns
> are dropped (last-seen first to drop). The cap is not configurable.

> **`X-Invariant 2` (header invariance):** the digest's header text
> (the "advisory" preamble) is **verbatim-pinned** by §X-Tests. Drift
> in this header is a regression — the anti-anchoring framing depends
> on these exact words.

### X4. Surfacing: `add_dirs` channel (separate from §L)

The planner's `add_dirs` already carries `prior_attempts.md` from §L.
§X adds `cross_design_patterns.md` **as a separate file in the same
directory**:

```
<run-dir>/inputs/
    design.md                       (existing, §3)
    design.fingerprint              (existing, §L2.3)
    prior_attempts.md               (existing, §L6; sibling-run digest)
    cross_design_patterns.md        (NEW, §X3; cross-design digest)
```

Both files are passed to the planner via `add_dirs=[<run-dir>/inputs]`.
The planner sees them as two named, ordered, separate documents.

> **`X-Decision 6`:** the cross-design digest is a **separate file**,
> never appended to `prior_attempts.md`. Concatenation would blur the
> two priority tiers (§X5) and dilute §L's high-signal sibling-run
> framing.

> **`X-Invariant 3` (one-way `add_dirs` channel):** cross-design
> patterns flow into the planner via `add_dirs` only. No other phase
> reads `cross_design_patterns.md`. The evaluator and triage explicitly
> do not consume it (X0 out-of-scope).

#### X4.1 Allowlist extension (parity with §L9.2)

To honor §S-Inv 3 (allowlist parity: subscribe accepts iff `read_resource`
accepts), §X also extends `resources.py:_ALLOWED_ARTIFACTS` so the
digest is host-readable:

| Site | Change | Why |
|---|---|---|
| `resources.py:_ALLOWED_ARTIFACTS` | Add one exact-match entry: `"inputs/cross_design_patterns.md"` | §X3 digest is a per-run input artifact, parallel to `inputs/prior_attempts.md` already added in §L9.2 |
| `resources.py:expand_scope_to_resources` | Add `inputs/cross_design_patterns.md` to the walk-list when the file exists | Same shape as §L9.2's `prior_attempts.md` walk-list addition |

A subscribe to `forge://<t>/<r>/inputs/cross_design_patterns.md` then
matches the allowlist; the §S5.2 emission row fires after the §X3 digest
write; the §X-Inv 6 "always-write" guarantee makes the URI safe to
subscribe to from run start (an empty digest still triggers one emission
after the always-write step).

> **`X-Decision 10`:** `inputs/cross_design_patterns.md` joins the
> resource allowlist in the same patch that lands the §X aggregator
> write. Splitting these would leave a window where §S subscribes
> reject (`-32002`) URIs §X is writing — a wire-shape inconsistency
> §S-Inv 3 forbids.

### X5. Anti-anchoring guarantees

Three layers, all required:

1. **Planner system-prompt directive** (`planner_system.md` addendum, an
   extension of §L7). Verbatim:

   ```
   ## Cross-design patterns (advisory only)

   You may receive a `cross_design_patterns.md` file in your inputs. It
   summarizes patterns observed across **other** design documents in
   this workspace's history. These are **statistical priors**, not
   facts about the current design. They have not been validated against
   the design you are now planning.

   You MUST NOT:
     - Treat cross-design patterns as constraints on the current design.
     - Add gaps or design flaws to your plan solely because a pattern
       was observed in unrelated designs.
     - Anchor your plan's structure on prior designs' shapes.

   You MAY:
     - Mentally check whether each pattern applies to the current
       design's stated requirements.
     - Note in your plan that you considered and dismissed a pattern,
       with one sentence on why it doesn't apply.

   The sibling-run summary in `prior_attempts.md`, when present, is a
   stronger signal than `cross_design_patterns.md`. Where they
   conflict, follow `prior_attempts.md`.
   ```

2. **Per-pattern "ADVISORY ONLY" prefix.** Every pattern in the digest
   carries the ADVISORY ONLY tag and the distinct-fingerprint count, so
   even a careless skim re-encodes the framing.

3. **Verbatim digest header.** §X-Inv 2 pins the preamble; reviewers
   catch silent dilution.

> **`X-Decision 7`:** the anti-anchoring directive is **also** mirrored
> into the planner's *user* message (via `inputs/cross_design_patterns.md`
> header) so it survives even if the system prompt is shortened in some
> future planner reshape. Belt and braces.

> **`X-Invariant 4` (anti-anchoring redundancy):** the anti-anchoring
> framing appears in **two** places (system prompt + digest header);
> losing either is a regression. Tested in §X-Tests.

### X6. Priority relative to `§L` sibling-run digest

When both digests are populated, the planner sees:

| Channel | Priority | Origin | Anti-anchoring strength |
|---|---|---|---|
| `prior_attempts.md` | HIGH | Same-fingerprint sibling runs | §L7 directive |
| `cross_design_patterns.md` | LOW | Different-fingerprint runs | §X5 directive (strictly stronger) |

The planner system prompt MUST instruct conflict-resolution in favor of
`prior_attempts.md`. §L7's existing language already says "prior
attempts are summaries of *real* prior runs against *this* design"; the
§X addendum says explicitly: "cross-design patterns NEVER override
sibling-run guidance".

> **`X-Decision 8`:** priority is enforced **only via the prompt**, not
> by mechanical content-merging. Forge-mcp does not try to detect and
> resolve content conflicts between the two files at digest-build time.
> The planner is the conflict-resolver, instructed by prompt.

> **`X-Invariant 5` (priority hierarchy):** in any planner system
> prompt this brief authors, `prior_attempts.md` is named as
> higher-priority than `cross_design_patterns.md`. A revision that
> reverses or muddies this ordering is a regression.

### X7. Failure tolerance and resume interplay

- **Aggregator failure**: any exception inside `find_cross_design_patterns`
  is logged at WARN and the digest is **empty** (the file is still
  written, with the verbatim header and zero patterns). Mirrors
  §L4.2 ("any failure → cold-start fallback"). Empty digest is safe:
  the planner sees only the anti-anchoring framing and proceeds without
  priors.
- **Resume interplay**: on a §H2 resume, the aggregator re-runs from
  scratch — `cross_design_patterns.md` is regenerated. Newer terminal
  runs that ended **during** the interrupted run's life now become
  eligible. This is intentional: the digest reflects the harness state
  at planner-cold-start time, not at run-start time.
- **Empty harness**: zero terminal runs in the last 30 days → empty
  digest. No special-case needed; the gate is a natural floor.

> **`X-Decision 9`:** aggregator failure → empty digest, never raised.
> Same fail-soft posture as §L's `L-Invariant 1` cold-start fallback —
> **a digest-build failure is explicitly carved out from Rule 8**.
> Rule 8 governs preflight and load-bearing run paths; the cross-design
> digest is an advisory prior fed to the planner. An empty digest is
> functionally equivalent to "no prior runs above the gate" and yields
> the exact same planner behavior as a harness with no eligible
> history. There is no degraded run mode, only a degraded *advisory*
> mode (which is the brief's documented zero-state).

> **`X-Invariant 6` (always-write):** `cross_design_patterns.md` is
> always written (even on aggregator failure), so the planner's
> `add_dirs` shape is stable. Hosts inspecting the input directory see
> a consistent file set.

### X8. Decisions log

`X-Decision 1` — aggregator is disk-read-only; pure-policy, unit-testable.
`X-Decision 2` — frequency floor = 3; recency window = 30 days; not env-configurable in v1.
`X-Decision 3` — count distinct fingerprints, not raw runs.
`X-Decision 4` — digest is prose, framed "ADVISORY ONLY"; not structured.
`X-Decision 5` — per-pattern 1 KiB; total digest 8 KiB. Hard truncation by last-seen.
`X-Decision 6` — separate file from `prior_attempts.md`; never concatenated.
`X-Decision 7` — anti-anchoring directive in both system prompt and digest header.
`X-Decision 8` — sibling-vs-cross priority enforced via prompt, not via mechanical merging.
`X-Decision 9` — aggregator failure → empty digest, never raised.
`X-Decision 10` — `inputs/cross_design_patterns.md` joins `_ALLOWED_ARTIFACTS` in the same patch as the §X aggregator write (allowlist + write co-land).

### X9. Invariants

`X-Inv 0` (gate-as-evidence)
`X-Inv 1` (conservative gate; logs ambiguity loudly, rejects pattern from digest)
`X-Inv 2` (digest header verbatim-pinned)
`X-Inv 3` (one-way `add_dirs` channel; no other phase reads it)
`X-Inv 4` (anti-anchoring redundancy in two places)
`X-Inv 5` (priority hierarchy: sibling > cross-design)
`X-Inv 6` (always-write digest for `add_dirs` stability)

---

## §-Risks (combined)

### §S risks

1. **Emission storm on plan-phase fsync.** Plan-phase writes
   `plan/plan.md` and `plan/sessions.json` back-to-back; a host
   subscribed to both sees two notifications within milliseconds.
   *Mitigation:* this is correct behavior — the host should be able to
   handle bursts. If a real client struggles, add a notifier-level
   coalescer in a follow-on brief, not here.
2. **Lost notifications during disconnect-bridge.** Continuity brief's
   client-disconnect bridge survives the disconnect, but subscriptions
   do not (§S2). On reconnect the host must re-subscribe and re-read
   any changed URIs. *Mitigation:* documented in the README's task /
   subscription guidance; same posture as the underlying MCP spec.
3. **Subscription on a URI whose run has been pruned.** §H9 pruning may
   delete a run-dir between subscribe-time and emit-time. *Mitigation:*
   `list_changed` fires on prune (§S6), so the host learns the URI is
   no longer discoverable; the subscription becomes a dead entry that
   stays in the registry until session disconnect. This is fine —
   `_REGISTRY.subscribed_sessions(uri)` will still emit if the URI is
   ever re-materialized, but it cannot be in this run since the dir
   was pruned. No false-positive notification path.
4. **Misordered notification from two writers.** §1 invariant: state.json
   is single-writer. Other artifacts (eval.md, triage.json) are also
   single-writer per phase. There is no cross-writer race because there
   is no cross-writer at the orchestrator level. *Verified.*

### §W risks

1. **Translation drift if any prefix is renamed.** §W-Inv 2 freezes the
   table; §18 test catches drift on every CI run. *Mitigated by test.*
2. **Caller relies on prefix and ignores `failure_kind`.** Acceptable —
   §W4 says the prefix is the string signal and is sufficient.
3. **A future error category appears without a kind.** §W-Inv 1 requires
   the closed taxonomy at every commit; the test fails if any error
   path omits a tag. *Mitigated by test.*

### §X risks

1. **Aggregator over-counts a noisy design that's been re-run with
   small edits.** Per §L2.1 canonicalization, trivial edits produce
   the SAME fingerprint; the distinct-fingerprint requirement
   (X-Decision 3) handles this naturally. *Mitigated.*
2. **Planner anchors despite the anti-anchoring directive.** §X5 has
   three layers (system prompt + per-pattern prefix + digest header)
   and §X6 explicitly ranks sibling-run higher. The risk is non-zero
   but bounded; observable via `triage.json` — design-flaw rates per
   `fault_kind` for newly-fingerprinted runs would spike if anchoring
   occurred. *Operational signal, no design fix.*
3. **30-day window is wrong.** It might be too long (drift) or too
   short (sparse). v1 picks one and pins it (X-Decision 2); revise on
   data.
4. **Pathological harness with thousands of runs.** §H9 prunes by
   default; aggregator wall-clock cost is bounded by retention.
   Aggregator failure → empty digest (X-Decision 9), so a slow
   aggregator never blocks the run.
5. **Privacy boundary leak.** Cross-design crosses fingerprint inside
   a single harness directory; it does NOT cross `target_dir` and
   does NOT consult any external store. The X-side privacy boundary
   is the same as §L's. *No new leakage surface.*

---

## §-Tests (§18-style pinning extensions)

The §18 test list grows by these. Each is a pinning test — the kind §18
already enumerates for cancellation-vs-timeout, citation rules, multi-ref
violation synthesis, etc.

### §S tests

- **`test_subscribe_capability_flag_true`** — register the handler, run
  `server.get_capabilities()`, assert `resources.subscribe is True`.
  Detects regression where the handler is removed.
- **`test_subscribe_capability_listChanged_true`** — same, for
  `resources.listChanged`.
- **`test_subscribe_handler_rejects_unallowed_uri`** — subscribe to a
  URI matching no allowlisted artifact; assert raises `McpError(-32002)`
  with the same message shape as `read_resource`'s rejection.
- **`test_subscribe_handler_accepts_allowed_uri_and_registers`** —
  subscribe to a real artifact URI; assert registry contains the entry.
- **`test_unsubscribe_handler_is_idempotent`** — unsubscribe from a URI
  never subscribed; assert no exception, registry unchanged.
- **`test_emission_on_state_write`** — invoke `write_state`; assert
  `notifier.notify` was called with the state.json URI.
- **`test_emission_only_after_fsync`** — patch `os.fsync` to capture
  call ordering; assert `notify` is awaited only after `fsync` returns.
- **`test_emission_failure_is_swallowed`** — patch
  `session.send_resource_updated` to raise; assert orchestrator path
  continues uninterrupted; assert WARN log emitted.
- **`test_status_log_not_emitted`** — drive a status update; assert
  `notifier.notify` was NOT called for `status.log`. Pins §S-Decision 7.
- **`test_every_allowlisted_artifact_has_emission_site`** — collect the
  set of allowlisted artifact patterns from `resources.py:_ALLOWED_ARTIFACTS`
  (excluding the deliberately-not-emitted set documented in §S5.2);
  assert each pattern has a corresponding `notifier.notify` call site
  somewhere in `src/forge_mcp/`. Pins §S-Decision 7b — prevents silent
  contract drift when a future brief adds a new allowlisted artifact.
- **`test_list_changed_on_register`** — register an active run; assert
  `send_resource_list_changed` was called on the active session.
- **`test_list_changed_not_on_completed_until_prune`** — transition a
  run to `completed`; assert no `list_changed` yet; trigger prune;
  assert `list_changed` fires.
- **`test_session_drop_clears_registry`** — exit the server lifespan;
  assert registry has zero entries for the dropped session.

### §W tests

- **`test_failure_kind_taxonomy_is_exactly_six`** — assert
  `errors._PREFIX` has exactly 6 keys, matching the verbatim spec
  table (§W1).
- **`test_failure_kind_prefix_verbatim`** — for each of the 6 kinds,
  assert `errors.tag(kind, "body")` == verbatim expected string.
- **`test_run_result_failure_kind_field_present`** — assert
  `RunResult.model_json_schema()` lists `failure_kind` under
  `properties` with the documented type (`string | null`) and the
  documented enum-or-anyOf shape covering the six literals.
- **`test_completed_result_has_null_failure_kind`** — construct a
  successful RunResult; assert `failure_kind is None`.
- **`test_timeout_result_has_timeout_kind`** — drive a cap-hit; assert
  result.failure_kind == "timeout" AND result.message starts with
  "[FORGE_ERR_TIMEOUT] ".
- **`test_cancelled_path_is_forensic_only`** — drive a client-disconnect
  path; assert `state.json` records `state="failed"` AND `cancelled=True`;
  assert NO `RunResult` is returned to the caller (§8.5 step 5
  re-raises). The `failure_kind="cancelled"` taxonomy is preserved in
  the `FailureKind` Literal for forensic-derived views but is not
  populated on `RunResult` under the current §8.5 contract.
- **`test_invalid_params_raise_has_prefix`** — call run_forge with bad
  input; assert raised McpError's message starts with
  "[FORGE_ERR_INVALID_PARAMS] ".
- **`test_lock_held_raise_has_prefix`** — pre-populate a foreign lock;
  call run_forge; assert raised McpError's message starts with
  "[FORGE_ERR_LOCK_HELD] ".
- **`test_prefix_construction_single_source`** — grep `src/` for any
  string matching `r"\[FORGE_ERR_"` outside `errors.py` and the test
  module; assert empty. Pins §W-Decision 3.

### §X tests

- **`test_aggregator_empty_harness_yields_empty_digest`** — tmp_path
  with zero runs; `find_cross_design_patterns` returns [];
  digest file is written with the verbatim header.
- **`test_aggregator_below_floor_does_not_surface`** — two distinct
  fingerprints both record the same fault_kind; aggregator returns [].
- **`test_aggregator_at_floor_surfaces`** — three distinct fingerprints
  record the same fault_kind; aggregator returns 1 pattern.
- **`test_aggregator_recency_window_drops_old`** — three fingerprints,
  one older than 30 days; aggregator returns [].
- **`test_aggregator_distinct_fingerprints_not_runs`** — same
  fingerprint recorded 10 times; aggregator returns [].
- **`test_digest_header_verbatim`** — pin §X3 header text exactly.
- **`test_digest_per_pattern_cap`** — synthesize a pattern with a 2-KiB
  body; assert the rendered digest entry is truncated to 1 KiB plus
  the truncation marker.
- **`test_digest_total_cap`** — synthesize 20 valid patterns; assert
  total bytes ≤ 8 KiB; assert excess patterns are dropped last-seen-first.
- **`test_planner_system_includes_cross_design_directive`** — read
  `planner_system.md`; assert the X-side anti-anchoring block is
  present verbatim.
- **`test_priority_directive_present`** — assert system prompt names
  `prior_attempts.md` as higher-priority than
  `cross_design_patterns.md`.
- **`test_aggregator_failure_emits_empty_digest`** — patch
  `find_cross_design_patterns` to raise; assert digest file is
  written, contains only the verbatim header, and the run proceeds.
- **`test_resume_re_aggregates`** — synthesize a §H2 resume; assert
  the digest is regenerated from scratch (newer runs are now
  included).
- **`test_cross_design_not_consumed_by_evaluator`** — drive an evaluator
  phase; assert the evaluator driver's `add_dirs` does NOT include
  `cross_design_patterns.md`. Pins X-Inv 3.

---

## §-Forward-looking

Still deferred after this brief lands:

- **Mid-phase SDK-session resume** — still §H18 / §C2.5. The continuity
  brief's `sessions.json` recording layer (C-Inv 3 / 4) remains
  forensic-only; no reader.
- **HTTP-transport variant** — still §R0.1 / §C10. The S-side registry
  is session-keyed in anticipation, but the transport itself is not
  shipped here.
- **Multi-tool MCP surface** — still §21 by definition.
- **Lineage fingerprint override** — still §L15-deferred; gate
  ("operator feedback") not yet met.
- **Cross-design feeding the Evaluator phase** — explicitly out of §X
  scope; §L15's gatekeeper-anchoring warning still applies.
- **Cross-`target_dir` learning** — multi-tenant; out of §X scope on
  privacy grounds.
- **Coalesced / batched notifications** — emission is per-meaningful-write
  in v1; a future brief can add a notifier-side coalescer if hosts
  observe burst pain.
- **Aggregator scan-time self-cap** — §L16 risk 10; defer until
  pathological harness depths are observed in production.
- **Renaming any of the six `[FORGE_ERR_*]` prefixes** — a wire-shape
  break; treat as a major version bump and own the migration plan.
- **Wider error taxonomy (7+ kinds)** — by W-Decision 1 design, requires
  a brief revision, not a single PR.

---

## §-Adoption sequencing

The three sub-briefs are independent and ship in any order; CI
catches regressions within each namespace independently. A sensible
order:

1. **`§W`** first — smallest surface (~50–100 LOC + tests), no new
   modules, single-source error construction in `errors.py`. Lands the
   structured signal hosts will want before they discover §S
   subscriptions.
2. **`§S`** second — new `subscriptions.py` module, two handler
   registrations on `server.py`, an `_Emitter` adapter in
   `orchestrator/`, and emission-site wiring at 5–7 fsync sites. Most
   reviewable.
3. **`§X`** third — new `cross_design.py`, a `planner_system.md`
   addendum, a new `inputs/cross_design_patterns.md` write site on the
   planner cold-start path, and the digest tests. Smallest behavioral
   blast-radius (planner-only input).

CLAUDE.md gains a fifth companion-brief reference after each lands —
not all at once — so the in-tree narrative stays incremental.

---

## §-Best-practice grounding

- **`§S`** — context7 (`/modelcontextprotocol/python-sdk`) confirms the
  low-level `Server` is the documented subscription registration site;
  `subscribe` capability auto-flips when the handler is registered;
  `ctx.session.send_resource_updated(AnyUrl)` is the canonical emission
  call. Forge-mcp's existing `Server("forge-mcp")` already follows the
  low-level-Server path (`server.py:52`), so no SDK-pattern migration is
  required.
- **`§W`** — the bracketed-prefix idiom is widely used in tool-chain
  output (e.g., Python's `logging` LEVELNAME prefix, `clang -fdiagnostics-color`,
  rust's panic prefix `thread 'main' panicked at`). It is parseable by
  regex, ignorable by humans, and survives any text-flattening
  transport. The single-source `errors.tag` discipline matches the
  "one taxonomy module" pattern from §11.1's triage namespace.
- **`§X`** — the "statistical priors" framing draws from the
  ensemble-learning literature: weak learners contribute signal but
  should not dominate strong learners (sibling-run digest is the strong
  learner; cross-design patterns are weak learners). The gate-as-
  evidence approach mirrors how empirical Bayes work in practice — the
  prior tightens as evidence accrues.
