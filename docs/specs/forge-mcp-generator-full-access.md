# forge-mcp — Generator Full Access

**What:** A normative brief that moves the Codex generator from a
`workspace-write` sandbox to **unconditional full access**
(`Sandbox.full_access` + `ApprovalMode.deny_all`) and deletes the now-dead
`network_access` knob from `RunForgeInput`. Companion to
`forge-mcp-design.md` and the eight prior briefs (seven companions plus
the SDK-realignment remediation brief). Like the SDK
realignment brief — and unlike the purely additive companions — this is
**partially a supersession brief**: it explicitly overrides two specified
behaviors (named in F0.4); on everything else the base doc and every prior
brief still win in their own namespaces. This brief's namespaces are `§F*`,
`F-Invariant N`, and `F-Decision N`, so code comments can cite it
unambiguously (e.g. `# §F2 full-access thread start`,
`# §F-Inv 4 no dead knobs`).

**Status:** Design complete. All SDK facts in §F1 were verified at design
time against the installed pinned beta (`openai-codex 0.1.0b2` via the
committed `uv.lock`) by direct introspection, and the policy guidance was
verified against the official Codex documentation via context7
(`/websites/developers_openai_codex`). The §18 schema-pin tests **shift for
this brief** — the only brief so far to change the `RunForgeInput` pin, and
the first to *remove* a property (the host-protocol brief's §W3 had already
*extended* the `RunResult` pin to cover `failure_kind`):
`RunForgeInput.model_json_schema()` loses the `network_access` property and
the pins update to the new verbatim shape. `RunResult.model_json_schema()`
is untouched (asserted byte-identical in §F7 scenario 3).

**Audience:** The implementing agent. Precision over prose; the seam
signatures and the `thread_start` call in §F2 are themselves normative —
every divergence from those shapes is a defect.

**Scope (chosen explicitly):** record the design-time SDK facts (§F1);
swap the generator thread to the full-access preset and shrink the seam
(§F2); delete the `network_access` input field — breaking, loud (§F3);
documentation (§F4) and drift pins (§F5). **Explicitly out of scope:** any change to the
Claude drivers (already `bypassPermissions` — `_claude.py:100`), the
verifier, doctor/preflight, prompts (the brief deliberately stays out of
the §P namespace — `generator_system.md` contains no sandbox language and
needs none, see F2.5), `RunResult`, retention/resource layers, and the
committed `uv.lock` (no SDK version change).

---

## F0. Thesis, north star, and supersessions

### F0.1 The asymmetry this brief ends

Before this brief, the generator was the **only sandboxed component in the
entire pipeline**:

- The planner and evaluator run the Claude SDK with
  `"permission_mode": "bypassPermissions"` at the single options
  chokepoint (`_claude.py:100`) — full permission.
- The §H1 verify gate runs the caller's arbitrary shell pipeline via
  `subprocess.run(shell=True, cwd=target_dir)` (`verifier.py:72`) —
  no sandbox of any kind. The code that command executes (the project's
  tests) is **authored by the generator**, so generator-authored code
  already ran with full host access every iteration.
- The generator alone ran under `workspace-write` with
  `writable_roots=[target_dir, iteration_dir]` and `ApprovalMode.deny_all`
  — any write outside those roots (package-manager caches such as
  `~/.cache/uv`, `~/.npm`, global tool installs) failed hard with **no
  escalation path**, because `deny_all` never surfaces an approval request.

That is not a security boundary; it is a capability asymmetry. The
unsandboxed verify gate can populate caches and exercise tools that the
generator — the agent that must *make that gate pass* — cannot touch. §H7
already conceded the honest position: *"the real isolation boundary is the
deployer's OS/container sandbox, which no in-process policy can substitute
for."* This brief takes that position to its conclusion: the in-process
generator sandbox is removed, and the documentation states plainly that
isolation is the deployer's job (F-Inv 5).

The north star (§1, context anxiety) is served, not eroded: a sandbox
denial mid-run is exactly the kind of friction that makes a long-horizon
agent abandon the real fix and fake a workaround. Removing denials removes
one incentive to produce dishonest convergence. No handoff, session, or
boundary mechanism changes.

### F0.2 Best-practice grounding (verified, not assumed)

Official Codex guidance (context7, `/websites/developers_openai_codex`, at
design time):

- *"Completely disable sandboxing by setting the sandbox mode to
  'danger-full-access'. **Use this only if your environment already
  provides sufficient process isolation.**"* — forge-mcp's documented
  deployment posture (§H7, README "Security model") *is* that environment:
  a disposable/scratch checkout inside an OS/container sandbox the deployer
  controls.
- *"`--ask-for-approval never` … works with all sandbox modes, allowing
  Codex to operate with the defined autonomy level."* — full access +
  never-ask is the documented full-autonomy pairing (the CLI's
  `--dangerously-bypass-approvals-and-sandbox` preset is exactly this
  combination).
- The docs' conservative hint — *"For granting write access to additional
  directories, prefer `--add-dir` over `--sandbox danger-full-access`"* —
  was considered and rejected for this codebase: see F-Decision 1.

### F0.3 What does **not** change

- **Rule 11 (no git mutations on the target) is untouched and fully
  load-bearing.** Both layers survive: the `generator_system.md` prompt
  forbid and gitguard's multi-ref post-iteration diff with high-severity
  gap synthesis. The sandbox never contributed here — git mutations inside
  `target_dir` were always writes inside the writable roots.
- **Approvals stay non-interactive.** `never_approval_mode()` →
  `ApprovalMode.deny_all`, unchanged (F-Inv 3). Under full access nothing
  can request escalation anyway; the mode is retained as the explicit
  statement that a run never blocks on a decision.
- Claude drivers, verifier, doctor, preflight, prompts, `RunResult`,
  artifacts layout, cancellation ordering (§8.5), retention (§H9),
  resource surface (§R), learning (§L/§X): untouched.

### F0.4 Supersessions (explicit)

1. **Base doc §10.2 / Decision 9, sandbox clause.** The
   `sandbox_policy_for` / `sandbox_config_for` workspace-write
   construction (`writableRoots`, `networkAccess`) and Decision 9's
   "workspace-write sandbox, `networkAccess=True`" wording are superseded:
   the generator thread now starts with the full-access preset (§F2).
   Decision 9's dual-layer git-mutation prevention and `deny_all` clauses
   stand.
2. **Hardening brief §H7, the knob.** `RunForgeInput.network_access`, the
   `sandbox_config_for(network_access=…)` parameter (§H7 — like every
   prior doc that names the helper — spells it `sandbox_policy_for`; the
   code has since renamed it to `sandbox_config_for`), the
   `GeneratorDriver.implement(network_access=…)` parameter, and the
   `phases.py` threading are superseded and removed (§F2, §F3). §H7's
   threat-model *documentation duty* is retained and strengthened (§F4);
   its "alternatives considered & rejected" reasoning (denylist = security
   theater) is continued, not contradicted.

`F-Decision 1 (unconditional full access; no knob):` the generator always
runs full access. Alternatives rejected:

> **A `sandbox_mode` input knob (default full).** Rejected: contradicts
> the requirement ("always"), grows the input schema, and leaves a
> rarely-exercised zombie `workspace-write` path that still has the F0.1
> asymmetry whenever flipped — an untested mode is a liability, not a
> safety net.
>
> **Keep `workspace-write`, widen `writable_roots`, switch to
> `auto_review`.** Rejected: root lists are whack-a-mole across package
> managers and platforms; `auto_review` (the SDK's automatic
> approval-reviewer, §F1) inserts a second model's judgment as a latency
> and availability dependency in the middle of a 10-hour unattended run;
> and the sandbox still is not a real boundary while the verify gate runs
> unsandboxed.
>
> **Keep the field as a deprecated no-op.** Rejected: a knob that
> silently does nothing is a lie to the caller — a caller setting
> `network_access=false` expects isolation and would get none. Rule 8
> (fail fast and loud) decides this: see F-Decision 2.

---

## F1. SDK facts at design time (pinned `openai-codex 0.1.0b2`)

Recorded so the drift-detection layer (`tests/test_sdk_contract.py`) can
pin them and so future betas are diffed against a written baseline, in the
same spirit as the SDK realignment brief:

- `openai_codex.Sandbox` members: `read_only = "read-only"`,
  `workspace_write = "workspace-write"`, **`full_access = "full-access"`**.
- **String-namespace trap:** the v2 `Sandbox` enum wire value is
  `"full-access"`, but codex `config.toml` (and the config-override dict
  accepted by `thread_start(config=…)`) names the same mode
  `"danger-full-access"`. Today's code got away with passing
  `{"sandbox_mode": "workspace-write"}` because that string is spelled
  identically in both namespaces — that luck does **not** extend to full
  access. Consequence: F-Inv 2.
- `openai_codex.ApprovalMode` members: `deny_all`, `auto_review` — exactly
  two; there is **no** `accept_all`. `auto_review` is the SDK analog of
  the documented `approvals_reviewer = "auto_review"` automatic reviewer.
  `thread_start`'s default is `auto_review`, so forge must keep passing
  `deny_all` explicitly (F-Inv 3).
- `AsyncCodex.thread_start` signature (keyword-only, abridged to what
  forge uses): `approval_mode: ApprovalMode = ApprovalMode.auto_review`,
  `config: JsonObject | None = None`, `cwd: str | None = None`,
  `sandbox: Sandbox | None = None`.
- `AsyncThread.turn(input, *, cwd=…, approval_mode=…, sandbox=…)` retains
  `sandbox` / `approval_mode` / `cwd` params (existing pin
  `test_async_thread_turn_signature_has_sandbox_approval_cwd` stays).

`F-Invariant 1 (full access always):` every generator Codex thread starts
with `sandbox=Sandbox.full_access`. No forge code path may construct a
read-only or workspace-write generator thread. Enforced structurally by
the single `thread_start` call site (§F2.2), the impl-capture pin
(§F5 item 4), and the grep guard (§F5 item 5).

`F-Invariant 2 (typed preset only):` the sandbox preset travels **only**
via `thread_start`'s typed `sandbox=` parameter. No `config=` argument is
passed at all, and no config-override dict may ever carry `sandbox_mode`
or `sandbox_workspace_write` keys — the `"full-access"` vs
`"danger-full-access"` namespace trap makes string keys a silent-failure
surface.

`F-Invariant 3 (non-interactive approvals, unchanged):`
`never_approval_mode()` → `ApprovalMode.deny_all` is passed explicitly on
`thread_start` and `turn` (the SDK default is `auto_review`, so omission
would change behavior). A run must never block awaiting an approval
decision.

`F-Decision 3 (deny_all retained; auto_review rejected):` with full
access nothing can request escalation, so `auto_review` would change
nothing in the happy path while inserting a second automated reviewer as
a dependency if the SDK's semantics shift; `deny_all` + full access is
the documented full-autonomy pairing (F0.2) and keeps "never block on a
decision" structurally true (F-Inv 3).

---

## F2. Seam contract (`_codex.py`, `generator.py`, `phases.py`)

### F2.1 `drivers/_codex.py` — delete `sandbox_config_for`

`sandbox_config_for` (currently `_codex.py:89-108`) is **deleted with no
replacement helper**. The preset is one enum reference at the single
`thread_start` call site. The `CodexRunner` Protocol and
`CodexRunnerImpl.turn` drop the `sandbox_config` parameter:

```python
async def turn(
    self,
    *,
    instructions: str,
    server_config: Any,
    approval_mode: Any,
    env: dict | None,
    run_log_path: Path | None = None,
) -> CodexSession: ...
```

`F-Decision 4 (preset hardcoded at the seam, not threaded through it):`
the prior code already hardcoded `sandbox=Sandbox.workspace_write` inside
`CodexRunnerImpl.turn` while redundantly carrying the detail in the
config dict; this brief keeps the hardcoded-preset pattern and deletes the
dict. Hardcoding at the one call site makes F-Inv 1 enforceable at a
single point; threading a `sandbox` parameter through the Protocol would
re-open the "some caller passes the wrong preset" surface for zero
flexibility anyone needs. `approval_mode` remains a parameter — surgical
change; the approval flow is deliberately untouched.

### F2.2 `CodexRunnerImpl.turn` — the normative call

```python
thread = await codex.thread_start(
    sandbox=Sandbox.full_access,  # §F2 full-access thread start; §F-Inv 2 typed preset only
    approval_mode=approval_mode,
    cwd=cwd,
)
```

No `config=` kwarg (F-Inv 2). The subsequent
`thread.turn(TextInput(text=instructions), cwd=cwd, approval_mode=approval_mode)`
call is unchanged. Everything else in the runner — stderr tee (B7),
`last_thread_id` (§C2.2), interrupt (§H10), idempotent close (§8.5) — is
untouched. Docstrings that currently describe "workspace-write preset +
sandbox_config overrides" (the `CodexRunnerImpl.turn` docstring — phrase
at `_codex.py:290`, stale `0.131`/`0.132` version prose at `:284-288`;
`sandbox_config_for`'s docstring leaves with the function) are re-pointed
to `§F2` and shed that stale version prose (the pinned beta is
`0.1.0b2`); per the conventions a stale citation left behind is itself an
audit defect.

### F2.3 `drivers/generator.py`

`GeneratorDriver.implement` loses the `network_access` parameter and the
`sandbox_config_for` import/call:

```python
async def implement(
    self,
    ctx: RunContext,
    *,
    codex_bin: str,
    status_cb: Callable[..., Awaitable[None]],
    env: dict | None = None,
) -> None:
```

The `self._runner.turn(...)` call drops its `sandbox_config=` argument and
is otherwise unchanged (instructions assembly, status forwarding,
`summary.md` backstop, `run_log_path` tee). The `implement` docstring's
"seam configs with §H7 network toggle" line (`generator.py:61`) is
re-pointed to `§F2` likewise.

### F2.4 `orchestrator/phases.py` — `_run_generator`

The §H7 signature-inspection branch (`phases.py:210-211`) and the
docstring's backward-compatible-fake rationale are deleted;
`_run_generator` becomes an unconditional call passing `codex_bin` and
`status_cb` only. Keep `import inspect` if `_evaluate`'s §H8 branch still
uses it; `ruff` (F401) arbitrates.

### F2.5 Prompts: deliberately zero changes

`generator_system.md` contains no sandbox/permission language, and the
Codex harness itself announces the effective sandbox state to the model in
its environment context — under full access the model self-knows. Adding
"you have full access" prose would duplicate harness-provided truth and
drag this brief into the §P namespace for no behavioral gain.

---

## F3. Input schema: delete `network_access` (breaking, loud)

`RunForgeInput.network_access: bool = True` (`models.py:43`) is deleted.
`RunForgeInput` keeps `model_config = ConfigDict(extra="forbid")`, so a
caller still passing the field gets a pydantic `extra_forbidden`
validation error, surfaced over the wire as the §18-pinned `isError=true`
shape — a loud, immediate, self-explaining failure at the pre-run
boundary, consistent with the §6.3 taxonomy (validation failure, not a
terminal run state).

`F-Decision 2 (delete, not deprecate):` removing the field over keeping a
no-op is Rule 8 applied to the tool surface: a knob that silently does
nothing is worse than a breaking change that explains itself. The §18
schema pin shifts **once**, deliberately, with this brief.

`F-Invariant 4 (no dead knobs):` `RunForgeInput` carries no
sandbox/network/approval knob. A caller passing `network_access` (or any
future sandbox-shaped field) MUST fail loudly at validation, never be
silently accepted. Pinned by §F5 item 2.

**Migration note (for the README and the change description):** callers
that pass `network_access` — saved MCP invocations, scripts, recipes —
must drop the field. There is no behavioral replacement: network is always
available to the generator, as it already was by default
(`network_access` defaulted to `true`).

---

## F4. Documentation updates (in scope, same change)

### F4.1 `README.md`

1. **Input table:** delete the `network_access` row (currently line 91).
2. **"Security model" section** (header currently at line 244, body
   through the §H7 see-also line): replaced by the following normative
   text (link targets preserved):

> The Generator executes with **full host access**
> (`Sandbox.full_access` — the SDK spelling of codex's
> `danger-full-access`): unrestricted filesystem writes, unrestricted
> network, and no approval prompts (`ApprovalMode.deny_all`). forge-mcp
> provides **no in-process isolation boundary at all**. This is a
> deliberate, documented posture, not an oversight: the verify gate
> already runs caller-supplied shell commands unsandboxed, the
> planner/evaluator already run with `bypassPermissions`, and
> generator-authored code already executes unsandboxed whenever the
> verify gate runs it — a generator-only sandbox provided asymmetric
> friction, not security. forge-mcp also deliberately ships no in-process
> command denylist (security theater for an agent that can author and
> execute scripts). The only real isolation boundary is the one the
> deployer provides: **run forge-mcp exclusively against a disposable or
> scratch checkout inside an OS/container sandbox you control** (dev
> container, VM, jail). Official Codex guidance sanctions full access
> precisely and only for such externally-isolated environments.
>
> See the [generator full-access brief (§F)](docs/specs/forge-mcp-generator-full-access.md)
> for the rationale; §H7 of the
> [long-run hardening brief](docs/specs/forge-mcp-long-run-hardening.md)
> records the threat-model groundwork this brief completes.

`F-Invariant 5 (documented honesty):` `README.md` and `CLAUDE.md` must
state plainly that forge provides no in-process isolation for the
generator and that the deployer's OS/container sandbox is the only
boundary. Softening this language ("sandboxed where possible",
"best-effort isolation") is a defect.

### F4.2 `CLAUDE.md`

Two updates, mirroring the established pattern:

1. **Source-of-truth section:** append the ninth-brief paragraph after
   the SDK-realignment paragraph. It MUST: name this brief
   (`docs/specs/forge-mcp-generator-full-access.md`); name the `§F*`,
   `F-Invariant N`, `F-Decision N` namespaces with citation examples
   (`# §F2 full-access thread start`, `# §F-Inv 4 no dead knobs`); state
   the precedence chain (base + eight prior briefs win on what they
   specify **except** the two clauses F0.4 explicitly supersedes); and
   state the §18 posture: **the pin shifts for this brief** —
   `RunForgeInput.model_json_schema()` loses `network_access` and the pin
   tests update verbatim; `RunResult` is untouched.
2. **Environment-overrides table / commands:** no changes — the knob was
   an input field, not an env var, and no command changes.

---

## F5. Test plan & pins (the §18-extension list)

1. **Schema pins update (the deliberate shift).** Every test pinning
   `RunForgeInput.model_json_schema()` or constructing the model with
   `network_access` updates to the new verbatim shape — at design time
   that is `tests/test_models.py` alone: the `inp.network_access is
   True` default assertion at `:274` is deleted, and the `:472`
   no-top-level-combinators pin re-derives from the shrunk schema with
   no text change. `tests/test_mcp_transport.py` and `tests/test_server.py`
   carry no input-schema pin or `network_access` construction — their
   only hits are the fake `implement` signatures item 6 owns; likewise
   `tests/test_errors_taxonomy.py` needs no change — it pins only
   `RunResult.model_json_schema()` and constructs `RunForgeInput`
   without `network_access`.
   `RunResult.model_json_schema()` pins must not change.
2. **New rejection pin (F-Inv 4).** Constructing
   `RunForgeInput(..., network_access=True)` raises a pydantic
   `ValidationError` (extra forbidden) — the loud-failure half of
   F-Decision 2, pinned at the model layer; the transport-level
   `isError=true` shape is already covered by the §18 transport pin
   style.
3. **SDK-contract pins (`tests/test_sdk_contract.py`).**
   `test_thread_start_carries_sandbox_workspace_write_overrides` is
   **replaced** by a full-access pin asserting: `Sandbox.full_access`
   exists with `.value == "full-access"` (the namespace-trap canary), and
   `thread_start` still exposes `sandbox` / `approval_mode` / `cwd`
   parameters. The `SandboxWorkspaceWrite` import and field
   assertions are deleted. The existing
   `test_async_thread_turn_signature_has_sandbox_approval_cwd` pin stays.
4. **New impl-capture pin (F-Inv 1 + F-Inv 2).** Following the existing
   monkeypatched-`CodexRunnerImpl` wiring pattern
   (`tests/test_drivers_protocols.py:644+`; note the existing
   `_FakeAsyncCodex.thread_start(self, **kwargs)` *discards* its kwargs —
   extend it or add a capturing variant): a fake `AsyncCodex` captures
   `thread_start` kwargs; assert `kwargs["sandbox"] is
   Sandbox.full_access`, `"config" not in kwargs`, and
   `kwargs["approval_mode"] is ApprovalMode.deny_all` when `turn` is
   called with `approval_mode=never_approval_mode()`.
5. **Grep guard (F-Inv 1 structural, in the repo's §15-guard
   tradition).** A test asserting that no file under `src/forge_mcp/`
   contains any of: `workspace-write`, `workspace_write`,
   `sandbox_config_for`, `network_access`. Scope is `src/` only (tests
   legitimately reference `Sandbox` members when pinning the SDK).
6. **Mechanical migration.** `tests/test_phases.py`: delete
   `test_generator_receives_network_access` (`:459-509`) — its sole
   subject, §H7's `inputs.network_access` threading, is removed
   wholesale, and its `deps.inputs.network_access = False` (`:503`)
   cannot even be written against the shrunk `RunForgeInput`. In
   `tests/test_drivers_protocols.py`:
   `test_sandbox_config_network_access_flip` (`:368`) is **deleted** —
   its sole subject is the removed `sandbox_config_for` helper, and its
   `from forge_mcp.drivers._codex import sandbox_config_for` would fail
   at collection; the five real-runner `CodexRunnerImpl.turn(...)` call
   sites drop their `sandbox_config=` argument (`:666`, `:725`, `:766`,
   `:809`, `:906`) — the `_FakeThread` / `_FakeAsyncCodex` doubles absorb
   extra kwargs via `**k` catch-alls and need no signature change. Fake
   generator drivers drop `network_access` from their `implement`
   signatures (`test_server.py:198`, `test_mcp_transport.py:301`).
7. **Full gate.** `bash scripts/ci.sh` (≡ `make ci`) green: ruff, format,
   pyright, docstring checker (all changed `def`s keep Rule 21
   three-section docstrings citing `§F*`), fast pytest.

---

## F6. Non-goals (re-asserted)

- **No Claude-side changes.** `bypassPermissions` at `_claude.py:100` is
  prior art this brief cites as motivation, not a surface it touches.
- **No prompt changes.** F2.5.
- **No restricted-mode knob, env var, or config file.** F-Decision 1. If
  a future deployment genuinely needs a restricted generator, that is a
  new explicit brief (§F8), not a quiet flag.
- **No approval-handler work.** The SDK default handler discussion in
  base-doc §20 note 2 stands; `deny_all` makes it moot.
- **No verifier/doctor/preflight changes.** The verify gate's
  unsandboxed `shell=True` posture is §H1's, recorded here only as
  evidence.
- **No `uv.lock` / dependency changes.** Same pinned beta; this brief is
  policy, not version.

---

## F7. Verification scenarios

The implementing agent MUST run these before declaring done and report
PASS / FAIL for each:

1. `bash scripts/ci.sh` exits 0 (all five checks).
2. `grep -rn "workspace.write\|network_access\|sandbox_config_for" src/forge_mcp/`
   returns nothing (also enforced forever by §F5 item 5).
3. A schema diff: `RunForgeInput.model_json_schema()` no longer contains
   `network_access` and differs from the pre-change schema **only** by
   that property (and its `required`/`properties` bookkeeping);
   `RunResult.model_json_schema()` is byte-identical pre/post.
4. A transport-level call passing `network_access` returns
   `isError=true` with an extra-forbidden validation message (covered by
   the updated §F5 item 1-2 tests; run them explicitly).
5. *(Optional, slow, requires `claude` + `codex` + auth)* `make e2e` — the
   real-CLI end-to-end converges; `run.log` shows the Codex session
   operating without sandbox-denial errors. If skipped, say so.

---

## F8. Forward-looking (NOT this brief)

- **Restricted-mode revival.** If a future deployment (shared host,
  multi-tenant CI) needs a confined generator, it returns as an explicit
  opt-in brief: a new input field, a reconstructed workspace-write
  config-override helper (minding the F1 namespace trap), and an
  un-supersession note against this brief. Record here so nobody
  re-derives it as a quiet flag.
- **Deployment recipe.** A README appendix or `docs/` page with a worked
  dev-container/VM example for the F-Inv 5 posture ("disposable checkout
  inside isolation you control") — documentation-only, separate change.

## F9. Risks and mitigations

1. **Host blast radius (accident or hostile design doc).** A malicious or
   confused `design.md` can now direct writes anywhere the forge process
   user can write. Honest assessment: this was already
   arbitrary-code-execution — the generator authors the tests the
   unsandboxed verify gate runs — so the delta is removing a speed bump,
   not opening a new capability class. Mitigations: the F-Inv 5 deployment
   posture (REQUIRED, stated in README), Rule 11's two layers unchanged,
   and the run artifacts (`run.log`, gitguard multi-ref diff) preserving
   forensics.
2. **Future beta renames/removes `Sandbox.full_access`.** Caught fast and
   in PR CI by the §F5 item 3 contract pin — the exact drift class
   `tests/test_sdk_contract.py` exists for (same playbook as the
   `make update` hazard already documented).
3. **Existing callers passing `network_access` break.** Intended and
   loud (`extra_forbidden` on the wire). The migration note (§F3) ships
   in the same change.
4. **Slow e2e now runs an unsandboxed generator on dev machines.** The
   `slow` marker is opt-in and the README posture applies to dev runs
   too; the e2e targets a scratch project by design.
5. **Regression re-introducing config-dict sandbox keys** (the
   `"danger-full-access"` vs `"full-access"` trap producing a silently
   wrong override). Mitigated three ways: F-Inv 2's flat prohibition, the
   §F5 item 4 capture pin (`"config" not in kwargs`), and the §F5 item 5
   grep guard.

---

End of brief.
