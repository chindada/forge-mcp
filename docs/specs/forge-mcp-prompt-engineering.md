# forge-mcp — Prompt Engineering

**What:** A normative enhancement brief that revises the five packaged
driver prompts in `src/forge_mcp/prompts/` so they (a) faithfully
describe what forge-mcp actually does — closing real drift between the
prompts/tests and the running system — and (b) follow established
prompt-engineering best practices, with each change traced to a named
principle (§P3). The rewritten prompt bodies in §P2 are themselves
normative: every divergence from those lines is a defect. Companion to
`forge-mcp-design.md`, `forge-mcp-long-run-hardening.md`,
`forge-mcp-long-run-continuity.md`, `forge-mcp-resource-surface.md`,
`forge-mcp-cross-run-learning.md`,
`forge-mcp-host-protocol-and-planner-extensions.md`, and
`forge-mcp-build-and-tooling.md`: the base doc wins on anything it
already specifies; each prior companion wins on anything in its
namespace; this brief only adds new behavior in its own `§P*`,
`P-Invariant N`, and `P-Decision N` namespaces so code comments and
tests can cite it unambiguously (e.g. `# §P2.2 generator budget clause`,
`# §P-Inv 2 real §15 guard`).

**Status:** Design complete. The five prompts ship today and are loaded
by the drivers via `importlib.resources`; `tests/test_prompts.py` pins a
set of load-bearing fragments. This brief rewrites the prompt bodies and
updates those pins in lockstep. The §18 schema-pin tests continue to
derive from `RunForgeInput.model_json_schema()` /
`RunResult.model_json_schema()` — **no shift for this brief** (prompts
touch no Pydantic schema).

**Audience:** The implementing agent. Precision over prose. The prompt
bodies in §P2 are the source of truth for the rewrite; the test changes
in §P4 move with them.

**Scope (chosen explicitly — "fidelity + best-practice"):** revise the
five existing prompt files in place and update `tests/test_prompts.py`
to match. **Explicitly out of scope:** any change to driver code, the
`PIVOT_DIRECTIVE` constant, the structured-output schemas
(`EVAL_RESULT_SCHEMA` / `TRIAGE_RESULT_SCHEMA`), the `RunForgeInput` /
`RunResult` shapes, or the prompt *set* (it stays exactly five files —
`P-Inv 1`). No new prompt files; no shared-preamble factoring
(`P-Decision 1`); the planner's use of `superpowers:writing-plans` is
unchanged.

---

## P0. Thesis, north star, and what does *not* change

### P0.1 The gap this brief closes

Two classes of problem coexist in the current prompts:

- **Fidelity drift — the prompts/tests do not describe the running
  system.** Three concrete instances:
  1. The §15 Playwright-excision guard in `tests/test_prompts.py` checks
     for **placeholder gibberish** (`removed_removed_tool_surface`,
     `removed_tool_`, a `removed_eval_probe.md` file) instead of the real
     forbidden surfaces (`playwright`, `browser_`-prefixed tool IDs, the
     legacy `evaluator_probe.md`). The invariant is effectively
     **unguarded** — a regression that reintroduced a browser tool ID
     would pass CI.
  2. `evaluator_remediation.md` carries planner-style skill-override
     text — an `OVERRIDE` block forbidding the
     `docs/superpowers/plans/<date>-<feature>.md` default save location,
     and a "Scrub or remove any 'Step 5: Commit'…" instruction — **but
     never invokes `superpowers:writing-plans`** and neither does its
     driver. The overrides guard a skill default that does not exist on
     this path.
  3. The in-run "non-progress → change strategy" rule is **duplicated**:
     the `PIVOT_DIRECTIVE` code constant (the real injection mechanism)
     *and* the remediation prompt's "If the run signals non-progress…"
     line, which implies a self-detection the agent never performs. (A
     third, *related but distinct* rule — cross-run anti-anchoring driven
     by `prior_attempts.md` — is correct and is kept; see `P-Decision 4`.)

- **Best-practice gaps.** Context-anxiety counter-prompting — the base
  design's #1 concern (§1) — appears in **none** of the prompts. The
  Generator, which does the heaviest lifting and whose system prompt is
  the only place for standing guidance (its per-iteration `contract.md`
  is appended at call time), is the thinnest. Examples are sparse,
  success criteria are vague, structure and depth are inconsistent
  across the five files.

### P0.2 The binding constraint (the north star is preserved)

The base brief's north star is **context anxiety** (§1): agents wrapping
work up prematurely under perceived context-window pressure. The
architecture denies *cross-phase* anxiety structurally (fresh SDK
session per phase, file handoff). But *within* a single phase — a
Generator implementing one contract over a long turn — an agent can
still wrap up early. The prompts are the only lever against in-phase
anxiety, and today they do not pull it.

`P-Invariant 0 (north star):` every prompt that drives an open-ended,
long-horizon turn (planner, generator) MUST carry an explicit budget /
anti-premature-wrap clause, and the generator MUST carry an
honest-non-convergence clause (implement what you can; record the
blocker; never fabricate completion). This reinforces north-star
mechanisms 1 and 4 at the prompt layer. It is a hard rule, not a
recommendation.

### P0.3 What does not change

The prompt **set is closed at five files** (`P-Inv 1`); the drivers load
them by exact basename via `importlib.resources`, so the filenames are
load-bearing. No driver code, no schema, no `PIVOT_DIRECTIVE` constant,
no `RunForgeInput`/`RunResult` shape changes. This keeps the brief a
low-risk, prompt-and-test-only change that the harness can implement
without touching the orchestrator.

`P-Invariant 1 (closed five-file set):` the packaged prompt set is
exactly `planner_system.md`, `generator_system.md`,
`evaluator_system.md`, `evaluator_triage.md`, `evaluator_remediation.md`.
Adding or removing a prompt requires driver + test changes and a brief
revision.

---

## P1. Prompt inventory and change contract

| Prompt | Driver / agent | Role in the loop | Change class |
|--------|----------------|------------------|--------------|
| `planner_system.md` | `PlannerDriver` (Claude, invokes `writing-plans`) | Author `plan/plan.md` from `inputs/design.md` | Light: + loop-context, + budget clause |
| `generator_system.md` | `GeneratorDriver` (Codex; system **+ appended `contract.md`**) | Implement the contract in `target_dir` | Heavy: + loop-context, + budget, + honesty, + done-criteria, + summary structure |
| `evaluator_system.md` | `EvaluatorDriver.evaluate` (Claude, `EVAL_RESULT_SCHEMA`) | Produce `EvalResult` | Medium: + loop-context, + example gap entry |
| `evaluator_triage.md` | `EvaluatorDriver.triage_design_flaws` (Claude, `TRIAGE_RESULT_SCHEMA`) | Classify gaps code-bug vs design-flaw | Light: + loop-context, + accept/demote example |
| `evaluator_remediation.md` | `EvaluatorDriver.write_remediation` (Claude, free-form) | Author next `contract.md` | Medium: + loop-context; drop skill-override framing → real content rule; de-dup PIVOT |

`P-Invariant 2 (real §15 guard):` no shipped prompt contains the
case-insensitive substring `playwright` or a `browser_`-prefixed tool
identifier, and `evaluator_probe.md` is not shipped. This is the genuine
§15 invariant the placeholder tests failed to enforce; §P4 makes it real.

---

## P2. The rewritten prompt bodies (normative)

Each fenced block is the **complete** new file body, to be written
verbatim. Every load-bearing fragment that `tests/test_prompts.py` pins
(after the §P4 update) appears here exactly. Notes after each block are
binding.

### P2.1 `planner_system.md`

```text
You are the forge-mcp Planner — the first phase of an autonomous Planner → Generator → Evaluator loop. Downstream phases never see your conversation; they read only the files you write, so a complete, unambiguous plan is the whole value you add.

Invoke `superpowers:writing-plans` to create a concrete implementation plan from `inputs/design.md`.

You have ample budget. Produce a thorough plan that covers every requirement in the design — do not abbreviate, defer sections, or wrap up early to save space. An underspecified plan fails downstream, and there is no penalty for length.

## Save location and write scope

OVERRIDE: Save the plan as `plan.md` in the current working directory using this exact relative path. Do not use an absolute path. Do not use the skill's default `docs/superpowers/plans/<date>-<feature>.md` save location; that location is forbidden for forge-mcp handoff.

Before saving, scrub or remove the skill's commit section, including any "Step 5: Commit", `git add`, or `git commit` instructions. Rule 11 forbids git mutations in the target repository.

You MUST NOT write to any path outside the `plan/` directory. Specifically: do not use Write/Edit on `../inputs/`, `../iteration-*/`, the `<run_dir>` top-level, or any absolute path. The orchestrator authoritatively overwrites `inputs/prior_attempts.md` on every run; your writes there are overwritten before the next run reads them, but they remain forensic evidence of disobedience. Stay inside `plan/`.

## Verification

Include test-first verification steps. Do not leak MCP tool identifiers (any `mcp__<server>__<tool>` token) into the plan; describe verification behaviorally instead — for example: GET `/healthz` and assert the JSON body `status` field equals `"healthy"`.

## Prior attempts

If `inputs/prior_attempts.md` exists, read it BEFORE writing the plan.

The file is a structured digest of prior `run_forge` invocations on this exact design doc (same SHA-256 fingerprint). Each prior run reached a terminal state (completed / incomplete / failed) without converging sufficiently for the operator to stop iterating.

Treat the contents as evidence about what does NOT work, not as a suggestion to refine:

- For each prior unresolved gap, your plan MUST propose a DIFFERENT implementation strategy than the documented attempt. Incremental refinement of an approach multiple prior runs failed at is forbidden.
- If a prior run reports a design-flaw gap citing the design doc, surface it in the plan's "open questions" section rather than pretending it isn't there.
- If a prior run's `verify_tail` is present, the verification command failed for the documented reason; your plan must explicitly address that reason, not work around it.

If `inputs/prior_attempts.md` does not exist, plan normally (cold start).

## Cross-design patterns (advisory only)

You may receive a `cross_design_patterns.md` file in your inputs. It summarizes patterns observed across **other** design documents in this workspace's history. These are **statistical priors**, not facts about the current design. They have not been validated against the design you are now planning.

You MUST NOT:
  - Treat cross-design patterns as constraints on the current design.
  - Add gaps or design flaws to your plan solely because a pattern was observed in unrelated designs.
  - Anchor your plan's structure on prior designs' shapes.

You MAY:
  - Mentally check whether each pattern applies to the current design's stated requirements.
  - Note in your plan that you considered and dismissed a pattern, with one sentence on why it doesn't apply.

The sibling-run summary in `prior_attempts.md`, when present, is a stronger signal than `cross_design_patterns.md`. Where they conflict, follow `prior_attempts.md`.
```

Notes: the new material is the opening loop-context line, the budget
clause (the third paragraph), and the `## Save location and write scope`
heading (renamed/expanded from the existing `## Write scope`) plus a new
`## Verification` heading. The `## Prior attempts` and `## Cross-design
patterns` headings and their bodies pre-exist. Every prior-attempts,
write-scope, anti-anchoring, MCP-tool-id, save-location, and cross-design
line is preserved verbatim. The planner *does* invoke `writing-plans`, so
its skill-override scrub stays meaningful and pinned.

### P2.2 `generator_system.md`

```text
You are the forge-mcp Generator — the implementation phase of an autonomous Planner → Generator → Evaluator loop. You receive a contract describing the changes for this iteration; an Evaluator checks your work against `inputs/design.md` after you finish. Your conversation is discarded — the code you write and the `summary.md` you author are the only things the next phase sees.

## Your task

Read the current `iteration-N/contract.md` supplied in your instructions and implement the requested changes. Make focused changes that satisfy the contract, prefer the simplest implementation that meets it, and run the relevant tests before you finish.

## Budget — do not wrap up early

You have a substantial time budget. Implement the contract completely. Do not stop early, summarize prematurely, or leave work unfinished to "save context" — there is no penalty for using your full budget, and an incomplete implementation fails evaluation and wastes the next iteration. Completeness beats brevity.

## When you cannot finish something

If you genuinely cannot complete part of the contract — a missing dependency, an ambiguous or contradictory requirement, an environment limitation — implement everything you can and record the blocker explicitly in `summary.md`. Never fabricate completion, fake a passing test, or paper over a failure: honest non-convergence is a designed outcome of this loop, and a documented blocker is more useful to the next phase than a false "done".

## Write scope

Implement changes in `target_dir` only, and write iteration artifacts into the current iteration directory only. Do not write anywhere else.

## You must not mutate git

You must not run or suggest git mutations: `git commit`, `git add`, `git push`, `git branch`, `git tag`, `git rebase`, `git reset --hard`, or `git worktree`. Reading git state is fine; changing it is not (Rule 11).

## Definition of done

You are done when the contract's changes exist in `target_dir`, the relevant tests have been run, and you have authored `iteration-N/summary.md` with three short sections:

- **Changes** — what you changed and why.
- **Verification** — which build/test commands you ran and their outcome.
- **Blockers** — anything you could not complete, or "none".

If verification could not run at all, explain why under Verification.
```

Notes: all eight git-mutation verbs appear verbatim (Rule 11,
`P-Inv 3`). The budget and honesty clauses are mandatory
(`P-Inv 0`). No `browser_`/`playwright`/`mcp_servers` references — §15
holds. The summary structure mirrors what the driver's backstop already
expects (`summary.md`).

### P2.3 `evaluator_system.md`

```text
You are the forge-mcp Evaluator — the checking phase of an autonomous Planner → Generator → Evaluator loop. Read `target_dir` and `inputs/design.md`, then produce an `EvalResult` object describing whether the implementation satisfies the design. Your judgment decides whether the loop stops or iterates again, so check every requirement in the design before you conclude.

## Output

Return only the structured object. The schema root is an object and must not use top-level oneOf, allOf, or anyOf. If no gaps remain, set `no_gaps` true and use an empty `gaps` array. If gaps remain, set `no_gaps` false and include concrete, actionable gap entries. Always set a brief `summary` of your overall assessment.

A good gap is specific: it points at the unmet design requirement, names where the code falls short, and tells the next iteration what to do. Cite the design section precisely — downstream triage may reclassify a gap as a design flaw based on your citation. For example:

> `POST /sessions` never expires idle sessions. The design (§4.2, "sessions expire after 30 minutes idle") requires a TTL sweep; none exists in `server.py`. Add idle-expiry plus a test asserting a session is gone after the TTL.

A weak gap — "auth seems incomplete", with no location, requirement, or remedy — is not actionable; do not emit it.

## Constraints

Do not edit files. Do not mutate git state.

If `verify.txt` is present in your working directory, its failures are authoritative — surface each failing build/test as a concrete gap citing the relevant design section.
```

Notes: the schema-root rule, the `no_gaps`/`gaps` discipline, and the
no-edit/no-git and `verify.txt` lines are preserved. The explicit
`summary` instruction is **newly added** — `EVAL_RESULT_SCHEMA` already
requires the field, so the sentence only surfaces it in prose (it is not
separately pinned for that reason). The "good gap" paragraph + worked
example are new (§P3 principle 4). The example's `§4.2` is illustrative
of an arbitrary target design, not a forge-mcp section.

### P2.4 `evaluator_triage.md`

```text
You are the forge-mcp Triage evaluator — a guard phase in the autonomous loop. Classify each EvalResult gap as either a code bug or a design flaw. The bar for "design flaw" is deliberately high: it is the only route by which a real bug gets waved away as the design's fault, so when in doubt, classify as a code bug.

A design-flaw classification is accepted only when every `cited_sections` entry is a 20-character-or-longer verbatim substring of `inputs/design.md` after whitespace canonicalization. If citation evidence is weak, missing, ambiguous, or only a title collision, classify conservatively as a code bug.

For example: a gap citing the exact sentence "the scheduler must never run two jobs concurrently" — a verbatim run of more than 20 characters present in the design — can be a design flaw. A gap citing only "Scheduler" because a heading by that name exists is a title collision; classify it as a code bug.

Return only the structured TriageResult object. Do not edit files. Do not mutate git state.
```

Notes: the 20-char verbatim rule, conservative demotion, and
structured-only / no-edit / no-git lines are preserved. The role line is
**extended** with loop context and a high-bar rationale, and the
accept-vs-demote example is new. This phase stays lean — it is a short,
well-scoped turn, so it gets no budget clause (`P-Decision 2`).

### P2.5 `evaluator_remediation.md`

```text
You are the forge-mcp Remediation writer — the bridge from one iteration to the next. Create the next generator contract from the current EvalResult: a focused, actionable directive telling the next Generator exactly what to fix.

## Save location

Save the contract as `contract.md` in the current working directory using this exact relative path. Do not use an absolute path.

## What the contract must and must not contain

Include focused, test-first verification steps for each fix. The contract must never instruct the generator to commit or otherwise mutate git state (`git commit`, `git add`, and the like) — Rule 11 forbids git mutations in the target repository, and the contract is the generator's instruction set.

Do not leak MCP tool identifiers (any `mcp__<server>__<tool>` token) into the contract; describe verification behaviorally instead — for example: GET `/healthz` and assert the JSON body `status` field equals `"healthy"`.

## Strategy

If your instructions include a directive to change strategy — the orchestrator prepends one after repeated non-progress (the same gaps recurring) — comply: propose a different implementation strategy and state it explicitly in the contract. Do not re-issue an approach that just failed.

## Cross-run learning

If `inputs/prior_attempts.md` exists, the same anti-anchoring rule applies to remediation: the new contract must propose a DIFFERENT implementation strategy than any documented-failed approach. Do not propose a remediation that maps onto a prior run's failed unresolved gap.
```

Notes: the opening line is extended with loop context (as in every
phase). The MCP-tool-id rule and the cross-run anti-anchoring sentence
are preserved verbatim. The skill-override framing and the
`docs/superpowers/plans` reference are **dropped** (`P-Decision 3`) and
replaced by a real content rule: relative `contract.md` path + "the
contract must never instruct the generator to … mutate git" (`P-Inv 3`).
The "Strategy" section now **acknowledges the orchestrator-injected
directive** rather than implying self-detection (`P-Decision 4`,
de-duplicating the in-run PIVOT messaging — the `PIVOT_DIRECTIVE`
code constant remains the injection mechanism, unchanged).

`P-Invariant 3 (Rule 11 in prompts):` `generator_system.md` enumerates
the forbidden git-mutation verbs verbatim; `evaluator_remediation.md`
forbids the contract from instructing any git mutation. Neither the
generator nor any contract it is handed may be told to mutate git.

---

## P3. Best-practice basis (each change traced to a principle)

The rewrite applies these established prompt-engineering principles.
Every change above maps to one; the brief "fits best practice" by
construction, not assertion.

1. **Clarity & directness.** Each prompt opens with an unambiguous
   role + task and (where open-ended) an explicit definition of done.
   *Applied in:* generator done-criteria, evaluator "check every
   requirement".
2. **Role / system prompting.** Every file states "You are the forge-mcp
   <Phase>" and where that phase sits in the loop and what reads its
   output. *Applied in:* the opening line of all five.
3. **Structure with delimiters.** Consistent `##` sections replace flat
   paragraphs so instructions are scannable and individually
   addressable. *Applied in:* generator, evaluator, remediation, planner.
4. **Examples (multishot).** Concrete examples are the single highest-
   leverage lever. *Applied in:* evaluator good-gap example, triage
   accept-vs-demote example, the behavioral-verification example kept in
   planner/remediation.
5. **Positive instruction framing.** State what to do alongside
   prohibitions. *Applied in:* generator (task + "you must not mutate
   git", not a bare prohibition list).
6. **Explicit success criteria & stop conditions.** *Applied in:*
   generator "Definition of done", evaluator `no_gaps` discipline.
7. **Budget / persistence framing (anti-context-anxiety).** Long-horizon
   agentic best practice — mirrors Anthropic's documented "long task
   context management" guidance (use the full output context; continue
   systematically until the task is complete). *Applied in:* planner +
   generator budget clauses (`P-Inv 0`).
8. **Honesty over fabrication.** Reliability best practice; ties to
   north-star mechanism 4. *Applied in:* generator
   honest-non-convergence clause.
9. **Calibration / token economy.** Match prompt heft to the turn —
   elaborate the open-ended phases, keep the short ones lean. *Applied
   in:* the evaluator, triage, and remediation stay lean, with no budget
   clause (`P-Decision 2`).
10. **Single source of truth (no drift).** Remove vestigial and
    duplicated instructions; make guards real. *Applied in:* dropped
    remediation skill-framing (`P-Decision 3`), de-duplicated PIVOT
    (`P-Decision 4`), real §15 test guard (`P-Inv 2`, §P4).

`P-Decision 1 (no shared-preamble factoring):` common rules (git-forbid,
loop context) are repeated per file rather than factored into a
code-composed preamble. Rationale: factoring would require driver
changes (out of scope) and the per-phase wording is calibrated, not
identical.

`P-Decision 2 (calibrated, not uniform, anxiety text):` the budget /
honesty clauses are strongest in the generator and present in the
planner — the two **open-ended, long-horizon** turns where an agent can
feel context pressure, and exactly the case Anthropic's prompting
guidance addresses with its "long task context management" advice. They
are deliberately omitted from the three **short, well-scoped** turns
(evaluator, triage, remediation), which finish quickly and carry at most
a light one-line thoroughness nudge (e.g. the evaluator's "check every
requirement before you conclude"). Uniform injection was rejected: it
dilutes the focused phases and inflates tokens for no behavioral gain.

`P-Decision 3 (remediation drops skill framing):` remediation authors
the contract directly and never invokes `superpowers:writing-plans`, so
the skill-override text is removed and replaced by a relative-path +
no-git-in-contract content rule. The planner keeps the skill (it
produces a real plan) and keeps its override text.

`P-Decision 4 (PIVOT de-duplicated):` the `PIVOT_DIRECTIVE` code
constant remains the authoritative non-progress injection. The
remediation prompt now *acknowledges* an injected directive instead of
claiming the agent self-detects non-progress. The cross-run
anti-anchoring rule (driven by `inputs/prior_attempts.md`) is a distinct
mechanism and is retained.

`P-Decision 5 (brief ships verbatim bodies):` §P2 carries the full new
prompt bodies, as `forge-mcp-build-and-tooling.md` §M2 shipped the full
Makefile — the implementer transcribes, not interprets.

---

## P4. Test changes (`tests/test_prompts.py`, lockstep)

The rewrite and these test edits land together. Every new or edited test
function MUST carry a Rule 21 three-section docstring (`Design:` /
`Implementation:` / `Example:`), or `scripts/check_docstrings.py` fails.

**Fix the §15 guards (the headline fidelity fix):**

- Replace `test_no_prompt_mentions_removed_removed_tool_surfaces` with a
  test (rename to e.g. `test_no_prompt_mentions_playwright_or_browser_tools`)
  asserting that, for every prompt, the lower-cased body contains neither
  `"playwright"` nor `"browser_"`. (`P-Inv 2`.)
- Replace `test_removed_eval_probe_md_does_not_ship` with a test
  (rename to e.g. `test_evaluator_probe_md_does_not_ship`) asserting the
  **real** legacy file `evaluator_probe.md` is not shipped.

**Split the shared planner/remediation tests** (remediation no longer
uses the skill):

Both combined tests below are **removed** and replaced by two named
single-prompt tests — do not leave the combined tests in place; they
would fail against the new remediation body:

- `test_plan_and_remediation_forbid_absolute_paths_and_default_save_location`
  → replace with `test_planner_forbids_absolute_path_and_default_save_location`
  (asserts both `"absolute path"` and `"docs/superpowers/plans"`) and
  `test_remediation_uses_relative_contract_path` (asserts `"absolute
  path"` only; **must not** require `docs/superpowers/plans`).
- `test_plan_and_remediation_scrub_commit_steps` → replace with
  `test_planner_scrubs_commit_steps` (planner keeps its `scrub`/`remove` +
  commit-step assertion) and `test_remediation_forbids_git_in_contract`
  (asserts the contract-level no-git rule: body contains `"git commit"`
  and the substring `"must never instruct"`).

**Add pins for the new load-bearing fragments** (canonicalized
substring, mirroring the existing `canonicalize_for_citation` pins):

- generator contains `"Completeness beats brevity."` (budget clause,
  `P-Inv 0`).
- generator contains `"honest non-convergence is a designed outcome"`
  (honesty clause, `P-Inv 0`).
- evaluator contains `"A good gap is specific"` (example/actionability).
- triage contains `"For example: a gap citing the exact sentence"` (the
  accept-vs-demote example).

(The evaluator's new `summary` sentence is intentionally **not** pinned —
`EVAL_RESULT_SCHEMA` already requires the field, so it is enforced
structurally; see the §P2.3 notes.)

**Preserve** every other existing pin: `test_all_five_prompts_ship`,
`test_generator_forbids_git_mutations`, the planner prior-attempts /
anti-anchoring / write-scope / MCP-tool-id pins, and the remediation
cross-run + MCP-tool-id pins. This **extends** §18's pinning list rather
than creating parallel coverage.

`P-Invariant 4 (load-bearing fragments stay pinned):` after the rewrite,
`tests/test_prompts.py` pins, at minimum: the five-file set; the real
§15 surfaces (`P-Inv 2`); the eight generator git verbs; the planner
prior-attempts, anti-anchoring, write-scope, and MCP-tool-id fragments;
the remediation cross-run and MCP-tool-id fragments; and the four new
best-practice fragments above (generator budget + honesty, the evaluator
good-gap lead-in, and the triage accept-vs-demote example).

---

## P5. Documentation update (in scope, same change)

Append a **seventh** companion paragraph to the `CLAUDE.md`
"Source of truth" section, mirroring the existing companion paragraphs.
It MUST: name this brief (`forge-mcp-prompt-engineering.md`); name the
`§P*` / `P-Invariant N` / `P-Decision N` namespace with citation
examples (`# §P2.2 generator budget clause`, `# §P-Inv 2 real §15
guard`); state the precedence chain (base + six prior companions still
win on what they specify; this brief only adds new behavior); and
re-affirm the §18 schema-pin posture (no shift — the prompts touch no
Pydantic schema). Do not rewrite the §15 paragraph; this brief realizes
its prompt-scrub guard rather than restating it.

`P-Invariant 5 (§18 posture unchanged):` this brief adds no field and
changes no schema; the §18 schema-pin tests derive from
`RunForgeInput.model_json_schema()` / `RunResult.model_json_schema()`
exactly as before.

---

## P6. Verification scenarios

The implementing agent MUST do all of these before declaring done:

1. `uv run pytest tests/test_prompts.py` — all prompt pins pass,
   including the two rewritten §15 guards and the four new fragment
   pins.
2. Grep confirms the real §15 invariant: no prompt body contains
   `playwright` or `browser_` (case-insensitive), and
   `evaluator_probe.md` is not present in `src/forge_mcp/prompts/`.
3. Each of the five bodies in `src/forge_mcp/prompts/` matches §P2
   verbatim.
4. `bash scripts/ci.sh` is green — ruff, ruff-format,
   pyright, `check_docstrings.py` (the new/edited tests carry
   three-section docstrings), and the full non-slow suite.
5. Read-through: every prompt opens with a role + loop-context line; the
   planner and generator carry the budget clause; the generator carries
   the honesty clause and definition-of-done; the evaluator and triage
   carry their examples; the remediation prompt contains no
   `docs/superpowers/plans` reference, and its "Strategy" section
   acknowledges an orchestrator-injected directive rather than carrying
   the old self-detection line "If the run signals non-progress…".
6. `CLAUDE.md` carries the new seventh-companion paragraph (§P5): it
   names this brief and the `§P*` / `P-Invariant N` / `P-Decision N`
   namespace, states the precedence chain, and re-affirms the §18
   no-shift posture.

---

## P7. Non-goals and forward-looking

**Non-goals:** no new or removed prompt files (`P-Inv 1`); no driver,
schema, or `PIVOT_DIRECTIVE` changes; the planner's `writing-plans`
usage is unchanged; no shared-preamble factoring (`P-Decision 1`); no
change to `RunForgeInput`/`RunResult`.

**Forward-looking (NOT this brief), catalogued so future PRs do not
re-derive them:** (a) if the generator system prompt and contract were
ever composed differently, a shared-preamble module could factor the
git-forbid + loop-context lines — but that is a driver change with its
own brief; (b) prompt-level few-shot libraries (multiple worked gap
examples) could be added if evaluation precision proves insufficient;
(c) if Codex gains MCP attachments again, the §15 guard's allowlist
posture would need revisiting.

---

End of brief.
