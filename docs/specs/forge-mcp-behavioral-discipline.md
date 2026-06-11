# forge-mcp — Behavioral Discipline (scope discipline for the Generator)

**What:** A normative brief that imports the four "reduce common LLM coding
mistakes" principles (`.local/BEST.md` — Think Before Coding, Simplicity
First, Surgical Changes, Goal-Driven Execution) into the packaged driver
prompts, **adapted to forge-mcp's autonomous-agent context** and **bounded by
the §1 context-anxiety north star**. Concretely it adds a `## Scope
discipline` section and an `Assumptions` summary field to
`generator_system.md`, and one balancing sentence to `planner_system.md`.
Companion to `forge-mcp-design.md` and the ten prior briefs; it **extends**
the §P prompt-engineering brief (which remains the prompts' content owner) the
way §G *completed* §11.4 — superseding nothing. This brief's namespaces are
`§B*`, `B-Invariant N`, and `B-Decision N` (`B` for **B**ehavioral
discipline), cited with the `§` prefix (e.g. `# §B2 scope discipline`,
`# §B-Inv 1 scope not effort`) — distinct from the SDK-realignment brief's
bare `B#` fix-group labels, which carry no `§`.

**Status:** Design complete. The four source principles are recorded in §B1
from `.local/BEST.md`. The prompt-engineering choices were verified against
Anthropic's official prompt-engineering tutorial via context7
(`/anthropics/prompt-eng-interactive-tutorial`): **be clear and direct**, a
**few-shot worked example** beats enumerated rules, **positive-first**
phrasing, and **resolve apparent contradictions explicitly** — all applied in
§B2. The §18 schema-pin tests **do not shift**: this brief touches only
prompts and docs; `RunForgeInput` / `RunResult` are untouched (B-Decision 4).

**Audience:** The implementing agent. The prompt insertions in §B2–§B3 are
normative text — paste them verbatim at the cited insertion points.

**Scope (chosen explicitly):** add Simplicity + Surgical + the
completeness-vs-simplicity reconciliation to the Generator (§B2); add the
Think-Before-Coding analog as a `summary.md` `Assumptions` field (§B2); add a
"plan only what's required / record the chosen interpretation" sentence to the
Planner (§B3); documentation (§B4) and prompt pins (§B5). **Explicitly out of
scope:** the Evaluator, Triage, and Remediation prompts (the rejected Scope B/C
— B-Decision 3); the §11.4 *prevention* OVERRIDEs (untouched); `RunForgeInput`
/ `RunResult` and any Pydantic schema; the committed `uv.lock`.

---

## B0. Thesis and relationship to §P and the north star

`.local/BEST.md` is the behavioral guideline set CLAUDE.md's *Working
approach* already name-checks. It was written for an **interactive, cautious**
coding session, where the failure mode is over-eagerness. The forge
**Generator runs unattended for hours**, where the failure mode is the
opposite — premature wrap-up, the §1 "context anxiety" north star, which is
exactly why `generator_system.md` already shouts "do not wrap up early /
Completeness beats brevity" (pinned as P-Inv 0).

So BEST.md cannot be pasted in verbatim: its **Simplicity First**, read
naively, fights the north star. The reconciliation — and the central idea of
this brief — is to separate **scope** from **effort**: *complete the contract
fully (never wrap up early) but implement nothing beyond it.* Simplicity and
Surgical Changes then constrain **what** to build; "do not wrap up early" still
governs **how completely** to build it. Framed that way they are
complementary, and the discipline generalizes per-phase: **complete within
scope, nothing beyond scope.**

This brief **extends** §P (the prompt-engineering brief that owns the prompts'
content) rather than rewriting it — the same relationship §G has to §11.4.
Every prior brief and the base doc still win in their namespaces; §B only adds
the behavioral-discipline layer. P-Inv 0 (the anti-premature-wrap budget
clause and the honest-non-convergence clause) is **load-bearing and
preserved** — see B-Inv 2.

---

## B1. Source principles (`.local/BEST.md`)

The four principles, and how each maps onto the autonomous loop:

1. **Think Before Coding** — state assumptions; present multiple
   interpretations rather than picking silently; ask when unclear. *Adaptation:*
   the Generator has no human to ask, so the analog is recording the
   interpretation it chose in `summary.md` (B-Decision 2), and the Planner
   records ambiguities in "open questions" (§B3).
2. **Simplicity First** — minimum code; nothing speculative; no unrequested
   abstractions/configuration/error-handling. *Maps directly* — bounded by the
   north star so it constrains scope, not effort (B-Inv 1).
3. **Surgical Changes** — touch only what you must; don't improve adjacent
   code; match existing style; note (don't delete) unrelated dead code; every
   change traces to the request. *Maps directly* — the strongest fit and the
   largest current gap; the Generator prompt has no surgical-changes discipline
   today.
4. **Goal-Driven Execution** — verifiable success criteria; test-first; loop
   until verified. *Already the loop's architecture* (Planner → contract →
   Generator → Evaluator, all test-first); §B adds nothing here.

---

## B2. Generator changes (`generator_system.md`)

### B2.1 New `## Scope discipline` section

Insert this section **immediately after the `## Budget — do not wrap up early`
section** (after the line ending "Completeness beats brevity.", currently
`generator_system.md:9`) and **before `## When you cannot finish something`**.
Verbatim:

```markdown
## Scope discipline

Complete the contract fully — never stop early (see Budget) — but implement
**nothing beyond it**. These do not conflict: "do not wrap up early" governs
how *completely* you build the contract; this section governs *what* you
build. Extra scope is not extra credit — it is unverified surface the
Evaluator must reconcile against `inputs/design.md`, and a frequent source of
wasted iterations.

- **Build only what the contract asks.** No speculative features, no
  abstractions for single-use code, no configuration or error handling for
  scenarios the contract does not raise. If a simpler implementation satisfies
  the contract, write that one.
- **Stay surgical.** Change only what the contract requires. Do not refactor,
  reformat, or "improve" adjacent code that already works, and match the
  target project's existing style and conventions even where you would write
  it differently. Every line you change should trace to a contract requirement.
- **Leave unrelated code alone.** A pre-existing bug or dead code *outside* the
  contract's scope is an observation for `summary.md`, not a fix — silent edits
  muddy the diff the Evaluator reads. (Exception: if you cannot complete the
  contract without fixing it, it is in scope — fix it. A design requirement the
  contract does not touch is the next iteration's job: record it and move on.)

Example: a contract that says "add idle-session expiry" wants exactly that
plus its test — not a reformatted session module or a configurable sweep
interval the design never asked for.
```

The action-first bullet leads ("Build only…", "Change only…"), the explicit
contradiction-resolution ("These do not conflict…"), the rationale ("Extra
scope is not extra credit…"), and the worked example each follow the
best-practice findings recorded in Status (B-Decision 1).

### B2.2 `summary.md` gains an `Assumptions` field

In the `## Definition of done` section, the `summary.md` is currently required
to have three sections (Changes / Verification / Blockers, currently
`generator_system.md:27-29`), introduced by a "with three short sections:"
lead-in (`generator_system.md:25`). **Append a fourth bullet** after
`Blockers`, **and update that lead-in's count word from "three" to "four"** so
the header matches the list:

```markdown
- **Assumptions** — any contract ambiguity you resolved by choosing an
  interpretation: state the interpretation and why, so the Evaluator can catch
  a wrong call. Or "none". Record an assumption only for an ambiguity you could
  reasonably resolve and then proceed; an ambiguity you genuinely cannot
  resolve is a Blocker (see "When you cannot finish"), not an assumption —
  never downgrade a real blocker to an assumption to declare done.
```

This is the Think-Before-Coding analog (B-Decision 2): the loop's substitute
for asking a human, on the same `summary.md` channel as the existing
blocker-recording.

`B-Invariant 1 (scope, not effort):` the Generator completes the contract
fully — never wrapping up early — **and** implements nothing beyond it.
Simplicity and Surgical Changes bound *what* is built; they never license
doing *less* or stopping sooner. Pinned by §B5 (the new scope-discipline pin
**plus** the retained P-Inv 0 budget pin).

`B-Invariant 2 (north star preserved):` `generator_system.md` MUST retain its
"do not wrap up early" Budget clause ("Completeness beats brevity.") and its
honest-non-convergence clause verbatim. §B adds the `## Scope discipline`
section; it does **not** edit, weaken, or remove the Budget or honesty
clauses. Enforced by the existing P-Inv 0 pins
(`test_generator_has_budget_clause`, `test_generator_has_honesty_clause`),
which §B keeps green.

`B-Decision 1 (markdown, not XML tags):` Anthropic's tutorial favors XML tags
for structuring Claude prompts, but §B keeps the prompts' existing markdown
headings/bullets. Two reasons: BEST.md #3 (match existing style — every other
forge prompt is markdown), and the Generator's runtime is **Codex**, whose
prompt/`AGENTS.md` convention is markdown, not Claude-style XML. Consistency
and the actual consumer win over the Claude-idiomatic default.

`B-Decision 2 (assumptions ledger, not an interactive ask):` BEST.md #1 says
"ask when unclear", but the autonomous Generator has no human to ask.
Recording the chosen interpretation in `summary.md` lets the Evaluator (or the
next iteration) catch a wrong assumption — the loop's structural substitute
for a clarifying question. It reuses the existing `summary.md` channel rather
than inventing a new artifact.

---

## B3. Planner change (`planner_system.md`)

The Planner is correctly told to be thorough ("ample budget… do not
abbreviate… no penalty for length", currently `planner_system.md:5`) because
underspecification fails downstream. That guidance is unbalanced toward
over-planning. **Append this sentence to the end of that paragraph** (after
`planner_system.md:5`):

```markdown
Plan only what the design requires — do not invent requirements or add
features the design does not ask for. Where the design is genuinely ambiguous
or admits more than one reading, record the interpretation you chose (and the
alternative) in "open questions" rather than silently picking one.
```

This adds Simplicity (#2 — don't invent requirements) and the Think-Before-
Coding analog (#1 — surface ambiguity in "open questions", a section the
prompt already references for design-flaw gaps) without touching the
thoroughness mandate. The Generator's `## Scope discipline` and the Planner's
"plan only what's required" are the same per-phase theme at two altitudes.

---

## B4. Documentation updates (in scope, same change)

### B4.1 `CLAUDE.md`

Append the eleventh-brief paragraph to the source-of-truth section, after the
§G (artifact-containment, tenth) paragraph. It MUST: name this brief
(`docs/specs/forge-mcp-behavioral-discipline.md`); name the `§B*`,
`B-Invariant N`, `B-Decision N` namespaces with citation examples
(`# §B2 scope discipline`, `# §B-Inv 1 scope not effort`) and note the `§`
prefix distinguishes them from the SDK-realignment `B#` fix groups; state that
it **extends §P** (the prompt-content owner) and is **bound by §1**,
superseding nothing; and state the §18 posture — **no shift**: prompts/docs
only, `RunForgeInput` / `RunResult` untouched. The environment-overrides table
and commands are unchanged.

### B4.2 §P brief — no edit required

§P remains the prompts' content owner; §B layers on top of it (it does not
rewrite §P, mirroring §G→§11.4). No edit to `forge-mcp-prompt-engineering.md`
is mandated; the relationship is recorded here and in CLAUDE.md (§B4.1).

`B-Invariant 3 (documented layering):` `CLAUDE.md` must record that §B extends
§P and is bound by §1. Describing §B as superseding or replacing §P, or
omitting the §1 binding, is a documentation defect.

---

## B5. Test plan & pins (the §18-extension list)

All pins live in `tests/test_prompts.py` and follow its established pattern —
`canonicalize_for_citation(fragment) in canonicalize_for_citation(text)`
against a load-bearing verbatim fragment — with Rule 21 three-section
docstrings.

1. **New — scope-discipline present.** Assert `generator_system.md` contains
   the reconciliation fragment `Extra scope is not extra credit` **and** the
   surgical fragment `Every line you change should trace to a contract
   requirement` (both markdown-free spans, so whitespace canonicalization is
   the only normalization needed).
2. **New — assumptions field present.** Assert `generator_system.md`'s
   Definition of done contains `any contract ambiguity you resolved by
   choosing an interpretation`.
3. **New — planner scope sentence present.** Assert `planner_system.md`
   contains `Plan only what the design requires`.
4. **Retained — north-star guard (B-Inv 2).** The existing
   `test_generator_has_budget_clause` (P-Inv 0, `Completeness beats brevity.`)
   and `test_generator_has_honesty_clause` MUST stay green — §B does not touch
   the Budget or honesty clauses. (No new test; these existing pins are the
   guard.)
5. **Retained — §15 and Rule 11.** `test_no_prompt_mentions_playwright_or_browser_tools`
   and `test_generator_forbids_git_mutations` stay green — the new section
   introduces no Playwright/browser token and no git-mutation verb.
6. **Full gate.** `bash scripts/ci.sh` (≡ `make ci`) green: ruff, format,
   pyright, docstring checker (the new test `def`s carry Rule 21 docstrings),
   fast pytest.

---

## B6. Non-goals (re-asserted)

- **No Evaluator / Triage / Remediation change.** Disciplining scope at the
  source (the Generator prompt) is sufficient; an Evaluator "over-implementation"
  gap type (the rejected Scope B) risks false positives and is not added
  (B-Decision 3).
- **No §P rewrite.** §B extends §P; it does not edit
  `forge-mcp-prompt-engineering.md` (B-Inv 3).
- **No erosion of P-Inv 0.** The Budget and honesty clauses stay verbatim
  (B-Inv 2).
- **No schema change.** Prompts/docs only; `RunForgeInput` / `RunResult` are
  untouched and the §18 pins do not shift (B-Decision 4).
- **No XML-tag restructuring.** B-Decision 1.

`B-Decision 3 (discipline at the source, not an Evaluator check):` scope creep
is prevented by instructing the Generator, not by a new Evaluator gap type.
The Evaluator checks the design is *satisfied*; making it also police
*over-implementation* would invite false positives (flagging legitimate
implementation detail as creep) for marginal gain. If a future deployment
needs it, that is a separate explicit brief.

`B-Decision 4 (no schema change):` `RunForgeInput` / `RunResult` are not
touched; the §18 schema-pin tests do not shift for this brief.

---

## B7. Verification scenarios

The implementing agent MUST run these before declaring done and report
PASS / FAIL for each:

1. `bash scripts/ci.sh` exits 0 (all five checks).
2. The three new §B5 prompt pins pass, and the retained P-Inv 0 / §15 /
   Rule 11 pins (§B5 items 4–5) stay green (run `tests/test_prompts.py`
   explicitly).
3. `generator_system.md` reads coherently: the `## Scope discipline` section
   sits between Budget and "When you cannot finish", the Budget clause is
   unchanged above it, and the Definition of done lists four sections
   (Changes / Verification / Blockers / Assumptions).
4. `RunForgeInput.model_json_schema()` / `RunResult.model_json_schema()` are
   byte-identical pre/post (no schema drift).

---

## B8. Risks and mitigations

1. **Simplicity erodes effort (the north-star risk).** A Generator reading
   "build only what's asked / simplest implementation" as license to do less
   and wrap up early. Mitigated three ways: the explicit scope-vs-effort
   framing in the section's opening line, leaving the Budget section's
   "Completeness beats brevity." intact directly above it, and B-Inv 2's
   retained P-Inv 0 pin (CI fails if the budget clause disappears).
2. **Under-fixing a needed bug.** "Leave unrelated code alone" could make a
   literal-minded Generator skip a fix a contract requirement actually needs.
   Mitigated by the explicit exception — an out-of-contract bug is in scope
   "if you cannot complete the contract without fixing it"; a design
   requirement the contract does not touch is deferred to the next iteration.
3. **Prompt bloat.** Adding a section risks diluting the prompt. Mitigated by
   keeping it to one tight section + one worked example (Simplicity applied to
   the prompt itself), and by placing it adjacent to the Budget section it
   reconciles with.
4. **Assumption used as a premature-done escape hatch.** The new `Assumptions`
   field sits beside "When you cannot finish"; a context-pressured Generator
   could downgrade a genuine blocker to a recorded assumption and declare done
   (a §1 north-star vector). Mitigated by the B2.2 boundary sentence — an
   assumption is only for an ambiguity you could resolve and proceed on; an
   unresolvable one stays a Blocker.

---

End of brief.
