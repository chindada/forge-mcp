# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth

`docs/specs/forge-mcp-design.md` is the **normative implementation brief** (~900 lines). Read the relevant section before any non-trivial decision; code comments cite it by section (e.g. `# §8.5 cancellation ordering`). When this file and the design doc disagree, the design doc wins.

A companion normative brief, the **long-run hardening** doc, adds convergence
and durability behavior in its own `§H*`, `H-Invariant N`, and `H-Decision N`
namespaces (cite as e.g. `# §H1 verify gate`, `# §H-Inv 3 resume append-only`).
The base design doc still wins on anything it already specifies; the companion
only adds new behavior.

The repository implements the §5 layout: `src/forge_mcp/` (orchestrator + drivers + schemas + prompts), `tests/`, `scripts/ci.sh`, `pyproject.toml`, `uv.lock`. Keep this file in sync as the code evolves.

## What forge-mcp is

A stdio MCP server exposing **one** tool, `run_forge`, that orchestrates a **Planner → Generator → Evaluator** loop to autonomously implement a feature described in a design document, writing into a caller-specified `target_dir`. Clean-room re-architecture of `harness-mcp` **minus Playwright** (§4).

## North star: context anxiety

The single most important design concern (§1) is **context anxiety** — agents wrapping work up prematurely as they sense context-window pressure. Over a 10-hour autonomous budget this is fatal. Four mechanisms deny any single agent the chance to feel that pressure:

1. **Fresh SDK session per phase.** Planner / Generator / Evaluator each start clean.
2. **Structured handoff via files** — never live context. Each phase reads only the artifacts it needs.
3. **Hard agent boundaries** — three driver subprocesses, isolated by the SDKs.
4. **Honest non-convergence.** Cap-hit returns `status="incomplete"` with `unresolved_gaps` populated — no incentive to fake done.

Every structural decision serves this. If you find yourself thinking "could I just thread X across phases", you are eroding the north star.

## Architecture in one paragraph

`run_forge` → `os.umask(0o077)` (**first statement**, Invariant 7) → pydantic validation → `RunConfig.from_env()` → `prepare_run()` (fs + lock + env probes) → `Orchestrator.run()`. The orchestrator is a **thin conductor** (`engine.py`) over small collaborators: a `RunStateMachine` (sole writer of `state.json`, Invariant 1), an explicit `RunLedger` (mutable accumulator replacing scattered `self._*`), pure-policy modules (`triage`, `caps`, `result`, `retry`), an I/O `lifecycle` module (cancellation/timeout/failure ordering), and three drivers (`planner`, `generator`, `evaluator`) reaching their SDKs **only** through the `_claude` / `_codex` Protocol seams. Drivers receive a fresh `RunContext` per call (Invariant 3) and never mutate orchestrator state.

## Dependency-graph rules (§5.2 — never break)

- `drivers/*` must not import `doctor` or `orchestrator/*` (would cycle).
- Concrete drivers reach the SDKs only via `_claude.ClaudeRunner` / `_codex.CodexRunner` Protocols — single mocking seam.
- `triage` and `caps` do no I/O; they are pure and unit-testable without disk or SDKs.
- Only `orchestrator/*` writes `state.json` (Invariant 1).
- `gitguard` lives at the top level — imported only by `orchestrator/*`. The legacy `orchestrator → drivers.generator` edge for git helpers does **not** exist in forge-mcp (§11.3).

## Error taxonomy (§6.3)

Bright line: **terminal run states are normal returns** of `RunResult`, never raised. Only pre-run failures use `McpError`. Important wire quirk: FastMCP turns a body-raised `McpError` into an `isError=true` text result — **the numeric code (`-32602`/`-32000`) is not transmitted on the wire**. Treat the code as an internal intent marker; the §18 transport test pins the real shape.

Two timeout-shaped paths are **never conflated**: client-disconnect → `cancelling → failed` (forensic); runtime cap → `finalizing → incomplete` (real work).

`RunResult` construction validates `traceback_truncated` at `max_length=4096` — truncate **before** building the result on the failure path, or the truncation error itself becomes an `isError` instead of the intended `failed` return.

## Conventions (§16)

- **Rule 21 — three-section docstrings.** Every non-trivial `def`/`async def` carries a docstring with **`Design:`**, **`Implementation:`**, **`Example:`** labels (each ≥5 non-whitespace chars). Enforced by `scripts/check_docstrings.py`. `fixtures/` paths are exempt.
- **Rule 11 — no git mutations on the target.** Never `git commit/branch/tag/push/worktree/rebase/reset --hard` inside `target_dir`. `gitguard` only *reads*. Dual-layer prevention: system-prompt forbid + multi-ref post-iteration diff. A violation synthesizes a high-severity gap for the next iteration to undo.
- **Rule 8 — fail fast and loud.** Missing skill / CLI / auth / dir → raise `McpError(SERVER_ERROR)` immediately. No silent fallbacks, no degraded modes.
- **Rule 4 — non-obvious choices get a one-liner.** Explain why this option, not the easier one.
- **Comments cite this design doc by section** (`# §8.5 cancellation ordering`). **Strip every legacy `harness-mcp` citation** when porting — `spec §N line NNN`, dated `2026-05-XX-*.md`, legacy `Decision N` numbers. Re-point to the matching forge-mcp `§` or delete. A stale legacy citation is itself an audit defect.

## Working approach

Two behavioral rules on top of the system-prompt defaults (the rest of the Karpathy "reduce common LLM coding mistakes" set is already covered — *Simplicity First* by the system prompt's "don't add features beyond what's required", *Surgical Changes* by "a bug fix doesn't need surrounding cleanup", and *Think Before Coding*'s ask-when-uncertain by the exploratory-question rule):

- **Surface tradeoffs; never pick silently.** When a request has multiple plausible interpretations, name them and ask. When a simpler approach would meet the goal, say so *before* implementing the requested one — push back when warranted.
- **Pin behavior with tests first.** Translate the task into a verifiable goal before writing the code: "fix the bug" → write a failing test that reproduces it first; "add validation" → write the negative-input tests first; "refactor X" → tests pass *before and after*. §18 already enumerates the pinning tests forge-mcp owes against its own load-bearing behaviors (no top-level schema combinators, the verbatim `SCHEMA_RETRY_SUFFIX`, citation rules, cancellation-vs-timeout terminals, multi-ref violation synthesis) — extend that list rather than inventing parallel coverage.

## Triage citation strictness (§11.1)

A design-flaw classification is accepted **only** when every `cited_sections` entry is a ≥20-char verbatim (whitespace-canonicalized) substring of `inputs/design.md`. Title collisions, unmatched rows, or weak citations conservatively demote to code-bug. This is the gate against a model waving away a real bug as a "design flaw". `classify_gaps` is the **single owner** of coercion-drift surfacing — the iteration loop must not separately read `GapTriage._coercion_log`.

## Cancellation ordering (§8.5)

Five steps, exact order (forensic invariant):
1. `sm.transition("cancelling", reason=..., cancelled=True)` (records `failed_phase` from `sm.last_phase`).
2. `close_drivers(...)` (per-runner `aclose` with 5s grace, then `terminate`).
3. `lock.release()`; set `ledger.lock_released = True`.
4. `sm.transition("failed", ...)` — final state written **after** lock release.
5. Re-raise `CancelledError`.

An observer seeing `failed`/`cancelled=True` must be able to assume the lock is free. Never write the final state before releasing the lock.

## Playwright excision is definitional (§15)

Playwright removal is the defining scope cut versus `harness-mcp`. **Do not reintroduce** any of: `PlaywrightDecision`, `RunResult.playwright_decisions`, `IterationArtifacts.playwright_decision_path`, `EvaluatorDriver.probe_playwright_need`, `evaluate(playwright_needed=…)`, `PlaywrightMissingError`, `evaluator_probe.md`, `GeneratorDriver.implement`'s Playwright preflight + `mcp_servers`, `StateLiteral.iter_pw_probing` and its live phase, `build_app_server_config(mcp_servers=…)`, `_mcp_servers_to_config_overrides`, `_toml_value`, `_esc_ctrl`, `_auto_approve_codex_requests`, doctor's `check_playwright[_available]`, `PLAYWRIGHT_*` constants, the `eval.md` "Playwright used:" line, or the legacy README's Playwright notes. Also scrub the `mcp__…playwright…__browser_*` tool-ID example from kept prompts (use a generic placeholder).

Dropping `_auto_approve_codex_requests` is safe: the Codex SDK's default approval handler already accepts command/file approvals; only MCP-specific methods defaulted to `{}`, and those are unreachable with no MCP servers (§10.2).

## Commands

```sh
uv sync                            # install (creates .venv, resolves uv.lock)
uv run forge doctor                # environment preflight (claude/codex CLIs, skills, auth)
uv run forge serve                 # start the stdio MCP server
uv run pytest -m "not slow"        # fast tests (what CI runs)
uv run pytest -m slow              # the real-CLI e2e only (needs claude + codex)
bash scripts/ci.sh                 # full CI gate (ruff + pyright + docstrings + pytest)
```

Single test: `uv run pytest tests/test_<name>.py::<test>`.

## Tooling (§17, §18)

Python ≥3.11, `uv` (package mode) + `hatchling`. Lints: `ruff` (`E,F,I,B,UP,ASYNC`, line 100) + `ruff-format` + `pyright` (basic). Tests: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). Prompts are shipped in the wheel via `[tool.hatch.build.targets.wheel.force-include]`.

CI gate (`scripts/ci.sh`):

```sh
ruff check
ruff format --check
pyright
python scripts/check_docstrings.py src tests scripts
pytest -m "not slow"
```

Test markers: `slow` (one e2e against real `claude` + `codex`, excluded by default), `mcp` (FastMCP transport), `driver` (drivers with mocked runners).

CLI entry points: `forge serve` (stdio MCP server), `forge doctor` (environment subset of preflight + disk-space warn + resolved `CLAUDE_CONFIG_DIR` / `claude` CLI path / codex paths). Both call the same check functions as `prepare_run` — single source of truth (§14).

Pre-release dep: `openai-codex` tracks `@main` (Decision 7, §19 — no PyPI release). Reproducibility comes from a **committed `uv.lock`**; do not pin a SHA in `pyproject.toml`.

`claude-agent-sdk` floor is `>=0.1.20,<1` — earliest exposing `setting_sources`, the `tools` preset, and `output_format` (§20 note 7).

## Environment overrides (§6.5)

| var | purpose | default |
|---|---|---|
| `FORGE_CODEX_BIN` | codex binary path | `"codex"` on `PATH` |
| `CLAUDE_CONFIG_DIR` | Claude SDK config dir | inherit SDK default |
| `FORGE_CLAUDE_CLI_PATH` | `claude` CLI override — preflight **and** runtime | unset; `claude` on `PATH` |

`FORGE_CLAUDE_CLI_PATH` is threaded into `ClaudeAgentOptions(cli_path=…)` via `RunContext.claude_cli_path` — a true runtime hatch (one deliberate step beyond legacy, which consumed it only in the doctor/preflight check).

## Artifacts on disk (§13)

Root: `<target_dir>/.harness/<run-id>/` (umask 0077; dirs 0700, files 0600). `target_dir` is canonicalized via `os.path.abspath`, **not** `realpath` — symlinked aliases get distinct roots (intentional). `<target_dir>/.harness/.gitignore` contains `*\n`, dropped under `O_EXCL` (never overwritten). `run.log` is sensitive (0600) and **never** crosses the tool boundary into `RunResult`; `ArtifactIndex` deliberately omits its path.
