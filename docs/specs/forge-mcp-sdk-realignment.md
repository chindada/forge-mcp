# forge-mcp SDK realignment & drift-proofing

**Status:** design / remediation brief
**Date:** 2026-05-23
**Scope:** realign both SDK seams (`drivers/_claude.py`, `drivers/_codex.py`) and
preflight to the installed `@main` SDKs, fix two design-required-but-dead
forensic features, add a drift-detection layer so this class of defect cannot
recur, and preserve one bundled pre-existing non-SDK correctness fix (D12).
Executed from a **clean tree** (all prior ad-hoc fixes reverted).

This brief supersedes design-doc **§276** (skill-probe mechanism) with documented
rationale (A2 below). Everything else *adds to* or *repairs* existing behavior;
the base design doc still wins on anything it specifies that this brief does not
explicitly supersede.

---

## 1. Root cause

Both SDK seams were written against **assumed/older SDK shapes**. The unit-test
doubles (`_FakeClaudeSDKClient`, the Codex fakes, the sandbox test) modeled those
*same assumed shapes*, so the full test suite stayed green while the runtime was
broken end-to-end across every Claude and Codex call. The one test that exercises
the real SDKs — `tests/test_e2e_real_clis.py` — is `@pytest.mark.slow`, excluded
from default CI (`pytest -m "not slow"`), and was never run during development.

**Therefore the remediation is two-pronged:** (a) realign the code to the real
SDKs, and (b) close the test-fidelity gap so drift is *detected* — fixing only
(a) leaves the trap armed for the next SDK bump.

Installed SDK versions this brief targets (both pinned `@main`, reproducibility
via committed `uv.lock` per Decision 7):
- `claude-agent-sdk` **0.2.85**
- `openai-codex` **0.131.0a4**

> **Superseded (2026-06-01):** the `openai-codex` `@main` git pin described
> here was later retired in favor of the published PyPI beta
> (`>=0.1.0b2`); see the updated Decision 7 / §19 in the base design doc.
> This brief's SDK-shape facts and drift-detection layer
> (`tests/test_sdk_contract.py`) still hold — the contract tests pass
> unchanged against the published beta.

---

## 2. Verified SDK facts (the executor must not re-derive these)

**claude-agent-sdk 0.2.85**
- `ClaudeSDKClient.receive_response()` exists and is the **documented** way to
  consume one turn — it yields all messages up to and including `ResultMessage`,
  then terminates. (Confirmed via context7 `/anthropics/claude-agent-sdk-python`:
  the README example uses `async for msg in client.receive_response()`.)
  `receive_messages()` is the **unbounded** stream — it blocks forever after the
  turn drains; iterating it without a stop condition deadlocks every call.
- Structured output: `output_format` must be the **envelope**
  `{"type": "json_schema", "schema": <bare-schema>}`. The subprocess transport
  emits the `--json-schema` CLI flag only when `output_format["type"] ==
  "json_schema"`; a bare schema (top-level `type: "object"`) is silently dropped
  and no structured output is produced. The result lands on
  `ResultMessage.structured_output` (a dict), **not** as a `StructuredOutput`
  tool-use block.
- `TextBlock` has only `.text` (no `.type`); `ToolUseBlock` has `.id/.name/.input`.
- `ClaudeAgentOptions` fields used by `build_options` are all valid in 0.2.85:
  `permission_mode, tools, setting_sources, output_format, add_dirs,
  disallowed_tools, mcp_servers, cwd, cli_path, hooks` — **plus `stderr`** (a
  callback) for subprocess stderr.
- The init `SystemMessage` (`subtype == "init"`) exposes a **deterministic**
  `data["skills"]` list (e.g. `["superpowers:writing-plans", …]`), alongside
  `slash_commands`, `tools`, `mcp_servers`, `agents`, etc.
- `CLIConnectionError`, `HookMatcher`, `AssistantMessage`, `SystemMessage`,
  `ResultMessage` all exist (no drift in `is_transient_error`, `git_deny_hooks`,
  `collect_writes_to_basename`).

**openai-codex 0.131.0a4** (not in context7; authoritative refs = installed SDK
source + harness-mcp `src/harness_mcp/drivers/_codex.py`)
- `AppServerConfig(codex_bin=…, cwd=…, env=…)` — field is `codex_bin`, **not**
  `executable`. `.cwd`, `.env`, `.codex_bin` read back as set.
- `AsyncCodex(config: AppServerConfig)` — **single** `config` kwarg. Supports
  `async with` / `__aenter__` / `__aexit__` / `close()`.
- `await codex.thread_start(*, developer_instructions=…, …)` → `AsyncThread`
  (has `.id`). `await thread.turn(input: RunInput, *, cwd=…, sandbox_policy=…,
  approval_mode=…, …)` → `AsyncTurnHandle`. `TextInput(text: str)` is a `RunInput`.
- `handle.stream()` yields events exposing **`.method` (str)** and **`.payload`**
  (a typed object or dict); it terminates after the `turn/completed` event.
- `SandboxPolicy` lives in `openai_codex.types` (not the package root) and is a
  tagged-union **RootModel**: build via
  `SandboxPolicy.model_validate({"type": "workspaceWrite", "writableRoots": [...],
  "networkAccess": <bool>})`; read the flag back at `policy.root.network_access`.
- `ApprovalMode.deny_all` exists. `TransportClosedError`, `is_retryable_error`
  exist (no drift in the Codex `is_transient_error`).
- Codex subprocess stderr is captured at `codex._client._sync._stderr_lines`
  (a `deque(maxlen=400)`, drained by an internal daemon thread).

---

## 3. Fix inventory

### Group A — functional realignment (best-practice)

**A1 — `ClaudeRunnerImpl.run_with_messages` uses `receive_response()`.**
Replace the `receive_messages()` loop with `async for msg in
client.receive_response()`. This is the documented idiom and removes the deadlock
at its source (no hand-rolled `break`). `_absorb_system_message` still runs per
message (init `SystemMessage` is included in the response stream).

**A2 — Skill probe reads init `SystemMessage.data["skills"]` (supersedes §276).**
Open a Claude SDK session with `setting_sources=CLAUDE_SETTING_SOURCES`, issue a
minimal query (its model answer is **ignored** — it only triggers the response
stream), consume `receive_response()`, and capture the init `SystemMessage`'s
`data["skills"]`. **Break out of the stream as soon as that init message is read**
— do not wait out the throwaway turn (the `async with` client teardown handles
the early exit). Then verify `REQUIRED_SKILLS ⊆ skills`. **No `output_format`, no
model self-report.** Bound the whole probe with `asyncio.wait_for(...,
SKILL_PROBE_TIMEOUT_SECONDS)` raising `SkillProbeTimeout` (Rule 8, fail fast and
loud — preflight holds the target lock). If the init message lacks a `skills`
field, raise (cannot verify → hard fail). Observing init-message shape requires a
**live session**, so the init-`skills` field is guarded against SDK drift by the
**e2e (C11)** — *not* by the no-subprocess C10 contract tests, which cannot open a
session to see it.
- *Preserves §6.4 intent:* this still opens a real Claude SDK session over the
  same `setting_sources` path the later phases use, so a broken auth/CLI/settings
  path still fails preflight — only the *skill-detection mechanism* changes from
  model-prose to the CLI's structured init data.
- *Rationale (best-practice / "right not easy"):* a model asked to "list your
  loaded skills" answers in nondeterministic prose and intermittently omits a
  skill that is in fact loaded (observed twice). The CLI's structured init data
  is ground truth. This also removes the probe's dependency on the (separately
  fixed) `output_format` path.
- *Supersession:* design-doc §276 specifies "JSON-schema output". This brief
  replaces that mechanism with the deterministic init-data read; rationale
  recorded here per the "design doc wins unless explicitly superseded" rule.

**A3 — `output_format` envelope + `drain_text` realignment.**
`build_options`: when `output_format` is a bare schema, wrap it as
`{"type": "json_schema", "schema": output_format}`; pass an already-enveloped
value through unchanged (idempotent; detect via `output_format.get("type") ==
"json_schema"`). `drain_text`: read structured output from
`ResultMessage.structured_output` (keep the last non-None dict) and text from any
content block exposing a string `.text` (`TextBlock`). Still required for the
evaluator's `EVAL_RESULT_SCHEMA` / `TRIAGE_RESULT_SCHEMA`; the probe (A2) no
longer uses it.

**A4 — `check_claude_auth` accepts env / file / macOS Keychain.**
Port harness-mcp's logic: `ANTHROPIC_API_KEY` → `CLAUDE_CODE_OAUTH_TOKEN` →
non-empty `.credentials.json` (leading dot) under `CLAUDE_CONFIG_DIR` (or
`~/.claude`) → on Darwin, `security find-generic-password -a $USER -s "Claude
Code-credentials<suffix>"` where `suffix = "-" + sha256(abspath(CLAUDE_CONFIG_DIR))
[:8]` when `CLAUDE_CONFIG_DIR` is set (empty when unset). Existence-only probe
(returncode 0); never reads/logs the secret; `OSError`/timeout → "not found".
Keep forge's `"claude_auth"` label.

**A5 — `build_app_server_config` uses `codex_bin=`.**
`AppServerConfig(codex_bin=codex_bin, cwd=str(cwd), env=env or {})`. Drop the
stale `executable=` kwarg and its `# type: ignore[call-arg]`.

**A6 — Codex turn/sandbox/stream re-port** (keep forge's `turn()` seam contract;
§10.2/§8.5/Invariant 2-3 unchanged):
- `sandbox_policy_for(*, target_dir, iteration_dir, network_access)` →
  `SandboxPolicy.model_validate({"type":"workspaceWrite","writableRoots":[...],
  "networkAccess": network_access})` imported from `openai_codex.types` (keep the
  §H7 network toggle — do **not** hardcode it).
- `never_approval_mode()` → `ApprovalMode.deny_all` (unchanged).
- `CodexRunnerImpl.turn(*, instructions, server_config, sandbox_policy,
  approval_mode, env, run_log_path=None)` (the `run_log_path` param is the B7
  additive extension — the `CodexRunner` Protocol, the generator call-site, and
  the C9 fakes gain the same optional param): `codex =
  AsyncCodex(config=server_config)`; **if
  `run_log_path` is set, install the B7 stderr tee on
  `codex._client._sync._stderr_lines` _now_ — between construction and opening,
  before the `__aenter__` drain thread starts (see B7)**; open it (`__aenter__`);
  `thread = await codex.thread_start()`; `handle = await thread.turn(
  TextInput(instructions), cwd=getattr(server_config, "cwd", None),
  sandbox_policy=sandbox_policy, approval_mode=approval_mode)`; return a streaming
  session wrapping `handle`. `env` is already embedded in `server_config` (A5), so
  it is not re-passed.
- Streaming session adapts `handle.stream()` events into forge's
  `CodexEvent(kind=ev.method, payload=<dict>)` (model_dump pydantic payloads;
  `{}` otherwise), preserving the generator's `event.kind` / `payload.get("kind")`
  consumption.

**A6-lifecycle (correctness, not nicety).** `close_drivers` runs **only** on
cancel/timeout/failure (lifecycle.py); the **success path never closes drivers**
(engine `finally` releases only the lock). Therefore:
- The **session closes the `AsyncCodex` when its stream is exhausted** (the
  `finally` of the stream generator) — this is the success path's *only* cleanup
  hook; without it every successful generator turn leaks a codex subprocess.
- `runner.aclose()` (cancel/timeout/failure) also closes it.
- `turn()` closes the codex if thread/turn **setup raises** after the client is
  opened (no leak on a failed turn start).
- All three close paths are **idempotent** (a one-shot guard / cleared ref), so
  double-close is a no-op. *(Decision D3: keep the `turn()` seam and harden it;
  do NOT refactor to harness's `session()` context-manager — that deviates from
  the normative §10.2 seam.)*

### Group B — design-required forensic features that are currently dead

**B7 — Codex stderr → run.log tee** (design §641). Fix `_RunLogTeeingStderr` to
replace `codex._client._sync._stderr_lines` with a `deque(maxlen=400)` subclass
whose `append` tees `[codex-stderr] <line>` to `run.log`, installed **before**
`__aenter__` (the drain thread starts in `__aenter__`). Thread a `run_log_path`
through the Codex seam as an **additive optional param** on `turn()` (the
generator passes its `run.log` path) — extends the seam without breaking its
contract. Defensive `try/except AttributeError` → WARN + continue (private-attr
layout may shift).

**B8 — Claude stderr → run.log tee** (design §629). Wire
`build_options(run_log_path=…)` to `ClaudeAgentOptions(stderr=<callback>)` that
tees `[claude-stderr]` lines to `run.log`. Today `run_log_path` is accepted and
ignored.

### Group C — drift-proofing (so this cannot recur)

**C9 — Seam fakes use real SDK types.** Realign `tests/test_drivers_protocols.py`
fakes to the real APIs: Claude fakes drive `receive_response` and yield real
`ResultMessage`/`TextBlock`; Codex fakes model `AsyncCodex(config=)` /
`thread_start` / `thread.turn` → handle with `.stream()` of `.method`/`.payload`
events; sandbox test reads `policy.root.network_access`. Fakes import real SDK
symbols so they cannot silently drift from the installed SDK.

**C10 — Fast SDK-contract tests** (new `tests/test_sdk_contract.py`, runs in PR
CI, no auth/no subprocess). Assert the shapes forge depends on, so an SDK bump
that renames/moves any of them fails CI immediately:
- `hasattr(ClaudeSDKClient, "receive_response")`.
- `build_options(output_format=<bare>).output_format == {"type":"json_schema",
  "schema":<bare>}` and `"stderr" in ClaudeAgentOptions` fields.
- `"structured_output" in ResultMessage` fields; `TextBlock(text=…)` constructs;
  `ToolUseBlock` has `id/name/input`.
- `AppServerConfig(codex_bin=…, cwd=…)` round-trips `.codex_bin`.
- `AsyncCodex.__init__` accepts a single `config` param; `AsyncThread.turn`
  signature includes `sandbox_policy`, `approval_mode`, `cwd`.
- `SandboxPolicy.model_validate({...workspaceWrite...})` →
  `.root.network_access` reflects input; `ApprovalMode.deny_all` exists.
- `TransportClosedError` / `is_retryable_error` / `CLIConnectionError` /
  `HookMatcher` importable.
These are `importorskip("openai_codex")` / `importorskip("claude_agent_sdk")`
guarded so a SDK-less checkout skips rather than errors.

**C11 — e2e wired into the dev loop.** Add a `make e2e` target running
`uv run pytest -m slow` (the existing `test_e2e_real_clis.py`, which calls
`run_forge` via the in-memory MCP transport against the tiny example and asserts
`isError is False`). Document it in CLAUDE.md "Commands". CI cannot authenticate
the CLIs, so e2e stays a local/nightly gate; the C10 contract tests cover PR CI.
*(Optional, if org CI has CLI creds: a nightly GH Actions lane gated on secret
availability — left out of the default plan.)*

### Group D — bundled pre-existing correctness fix (NOT SDK-drift)

**D12 — `lockfile.py` lock-payload `target_dir` records the run target, not
`.harness`.** This is a *separate* forensic-correctness fix that pre-dated this
session and would otherwise be lost on revert; bundled here so it survives.
The lock lives at `<target_dir>/.harness/run.lock` (Invariant 5), so `self._path`
is the `run.lock`, `self._path.parent` is `.harness`, and the **grandparent**
`self._path.parent.parent` is the actual `target_dir`. The payload field is named
`target_dir`, so it must hold the grandparent:
```python
# src/forge_mcp/lockfile.py — TargetLock.acquire() payload
- "target_dir": str(self._path.parent),
+ "target_dir": str(self._path.parent.parent),   # §6.5 grandparent = target_dir, not .harness
```
Pin the corrected value in `tests/test_lockfile.py::test_acquire_mints_8hex_run_id_and_writes_payload`:
```python
- assert payload["target_dir"] == str(target_dir / ".harness")
+ assert payload["target_dir"] == str(target_dir)
```
- *Severity:* forensic only — no code consumes `payload["target_dir"]` (verified),
  and `state.json` already records the target correctly (`engine.py` uses
  `harness_dir.parent`); this fixes the lock payload's inconsistency. The other
  `test_lockfile.py` stale-payload fixtures pass a `target_dir` value that
  `_is_stale` never reads, so they need no change.
- *Scope note:* this is the one item in this brief that is **not** SDK-drift; it
  is included only because it was an in-flight fix at revert time. Drop it if you
  prefer to handle it as its own change.

---

## 4. Testing strategy

- **TDD per fix** (§18 discipline): write the failing test first, then the fix.
  Each Group-A defect gets a red-then-green pin; the pins use **real SDK message/
  option/config types** (not the stale shapes that hid the bugs).
- **Layering:** C10 contract tests (fast, CI) catch shape drift (#A3/A5/A6/B8
  class); C11 e2e (local) catches integration/iteration drift (#A1/A2 class).
- **Coverage to add/realign:** `receive_response` termination pin; probe
  init-data pin (fake init `SystemMessage` with/without the required skill);
  `output_format` envelope + `drain_text(structured_output/TextBlock)` pins;
  `check_claude_auth` Keychain (default + suffix) + fail pins; `codex_bin` pin;
  Codex `turn` lifecycle pins (id lifecycle, fail-soft, method→kind mapping,
  **codex closed on stream exhaustion**, codex closed on setup failure);
  stderr-tee pins (B7/B8 target the real private attr / `stderr` callback).
- **Full gate stays** `bash scripts/ci.sh` (ruff + ruff-format + pyright +
  docstrings + `pytest -m "not slow"`), now including `test_sdk_contract.py`.

## 5. Suggested execution order
1. C10 SDK-contract tests (encode the target shapes first — they double as the
   spec's executable acceptance criteria).
2. A1, A3 (Claude seam: `receive_response` + `output_format`/`drain_text`).
3. A2 (probe init-data + timeout).
4. A4 (auth).
5. A5, A6 + A6-lifecycle (Codex seam).
6. B7, B8 (stderr tees).
7. C9 (fake realignment — largely done alongside A1–A6 pins).
8. C11 (`make e2e` + docs), then run `make e2e` as the final acceptance gate.
9. D12 (lockfile `target_dir` fix) — independent of everything above; can land
   anytime, sequenced last only because it is the lone non-SDK item.

## 6. Non-goals
- No refactor of the Codex seam to a `session()` context-manager (D3).
- No new normative run behavior beyond the §276 probe supersession.
- No change to `RunForgeInput` / `RunResult` schemas (§18 pins unaffected).
- No commits to `examples/scratch-app/` output (gitignored; Rule 11).

## 7. Acceptance
- `bash scripts/ci.sh` green (incl. new contract tests).
- `make e2e` (with real `claude` + `codex` + auth) completes a tiny-feature run
  with `status="completed"` and the design's `pytest` passing.
- SDK-shape drift (e.g. a future SDK renaming `AppServerConfig.codex_bin`) fails a
  C10 contract test in PR CI; a forge-side regression (e.g. reverting A5) fails its
  own A5 unit pin. Both classes surface in CI — not only in the e2e.
