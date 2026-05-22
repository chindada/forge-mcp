# forge-mcp — Cross-Run Learning

**What:** A normative enhancement brief that makes every `run_forge` invocation
*start smarter than the last* by feeding the planner a structured digest of
prior runs on the same design doc — without threading any live agent context
across the run boundary. Companion to `forge-mcp-design.md`,
`forge-mcp-long-run-hardening.md`, `forge-mcp-long-run-continuity.md`, and
`forge-mcp-resource-surface.md`: the base doc wins on anything it already
specifies; the hardening doc wins on anything it specifies; the continuity doc
wins on anything it specifies; the resource-surface doc wins on anything it
specifies; this brief only adds new behavior in its own `§L*`,
`L-Invariant N`, and `L-Decision N` namespaces so code comments can cite it
unambiguously (e.g. `# §L2 fingerprint`, `# §L-Inv 1 cold-start fallback`).

**Status:** Design complete. The base implementation, the H1–H10 hardening,
the C1–C2 continuity, and the R1 resource surface are all shipped and
CI-green (319 tests passing, 2 skipped). This brief is the implementation
contract for the next increment. It assumes the §C1.4 Path B low-level
`Server` is the registration venue and the §R1 resource surface is the
host-facing read path for `prior_attempts.md` / `design.fingerprint`.

**Audience:** The implementing agent. Precision over prose; every interface
sketch is normative and carries the Rule 21 three-section docstring
(`Design:` / `Implementation:` / `Example:`).

**Scope (1 enhancement, chosen explicitly):** L1 cross-run learning at the
planner cold-start, auto-detected by design-doc fingerprint, file-handoff
only (no live context across the run boundary). **Explicitly out of
scope:** explicit `lineage_run_ids` caller input (the auto-detect-only model
was chosen — see §L-Decision 1); a "lineage fingerprint override" for
edited-but-equivalent design docs (deferred — §L15 Forward-looking);
cross-`target_dir` lineage (privacy / multi-tenant concern, §L16 risk 2);
reading prior
attempts from a registry / DB rather than the filesystem (resource surface
is the host story, filesystem is the truth-of-record — §L11).

---

## L0. Thesis, north star, and what does *not* change

### L0.1 The gap this brief closes

The base architecture's discipline is "fresh SDK session per phase, file
handoff across phases" (§1). That works within a run. Between runs, the
discipline is "no shared state" (Decision 4): each `run_forge` invocation
starts cold even when prior runs on the same design doc are on disk under
`<target_dir>/.harness/`. Three places this becomes load-bearing:

- **Repeat-attempt friction.** An operator running successive attempts on
  the same design doc (the common iterate-on-the-design workflow described
  in the article principle 14, "iterate on the harness through experiments")
  pays a full planner-phase + first-iteration tax each time, even when a
  prior run already proved a particular implementation strategy doesn't
  converge. Over a 10-hour budget this is real time.
- **Plateau persistence across runs.** The §H3 pivot-then-break stops a
  *single* run from burning the budget on an oscillating gap set, but a
  fresh re-run repeats the same opening moves and re-discovers the same
  plateau from scratch. The §H3 break's `stop_reason` is forensic-only — no
  next planner reads it.
- **Design-flaw amnesia.** The §11.1 triage subloop carefully identifies
  *design flaws in the design doc* (`design_flaw_gaps` on `RunResult`) but
  surfaces them only on the terminal `RunResult`. A re-run on the same
  unedited design doc rediscovers the same flaws — wasted iterations on a
  problem the prior run already named.

The durable substrate to close all three already exists: machine-truth
`eval.json` (§9.2), `state.json` (§7), `verify.txt` (§H1.5), the per-run
artifact tree (§13), the §R1 resource surface (host-facing), and the §6.5
`<target_dir>/.harness/` directory structure. Every mechanism in this brief
is an **additive module or a localized wiring change** along those existing
seams. No SDK changes. No new transport. No new bootstrap.

### L0.2 The binding constraint (the north star is preserved)

The article's "fresh context per phase, no compaction, no transcript
carryover" rule (§1, principles 1+7) applies *within a run*. Cross-run
learning is at run-start, before any agent context exists — extending the
file-handoff discipline one notch further is *consistent with* the north
star, not in tension with it. Concretely:

- The fingerprint is computed by the orchestrator over a file (pure I/O,
  no SDK).
- Lineage discovery is a filesystem scan (orchestrator-owned, mirrors
  `result.py`'s read-the-run-dir pattern).
- The summarization step reads `eval.json` / `verify.txt` / `state.json`
  off disk and writes a structured markdown digest to `inputs/`. No live
  context.
- The planner reads the digest **through its existing `add_dirs=[inputs/]`
  surface** — same handoff pattern as `design.md`. The planner phase still
  starts with a fresh `ClaudeSDKClient` per the base doc.

`L-Invariant 0 (north star):` cross-run learning crosses the run boundary
**via files only**; no SDK session, transcript, or in-memory state persists
across runs. This is `H-Inv 0` / `C-Inv 0` / `R-Inv 0` re-asserted at the
run boundary.

### L0.3 Architectural stance: extend, do not restructure

> **Alternative considered & rejected.** Carrying cross-run state through a
> Claude SDK `session_store` (the SDK exposes `ClaudeAgentOptions(
> session_store=…, resume="<session_id>")` with S3/Postgres/Redis adapters
> — context7 `/anthropics/claude-agent-sdk-python`). Rejected: the SDK
> `session_store` is built for **in-conversation** continuity (resume a
> transcript by id), not cross-run knowledge persistence. Repurposing it
> would conflate two different concepts (transcript vs distilled
> learning), would require the next planner to *resume* a prior session
> (threading live context — violates `L-Inv 0`), and would couple
> cross-run learning to an SDK feature that ships per-vendor (Codex has no
> equivalent persistent-knowledge primitive, only `thread_resume` for
> transcript continuation).
>
> **Alternative considered & rejected.** Adding a separate "knowledge DB"
> (SQLite at `<target_dir>/.harness/lineage.db`, indexed by fingerprint).
> Rejected: the filesystem is already the truth-of-record (Invariant 1
> for `state.json`, R-Inv 1 for the active-run registry, §H9 for run-dir
> retention) — duplicating into a DB would (a) violate
> single-source-of-truth, (b) require a migration story when the schema
> evolves, (c) need its own concurrency control. A per-run `inputs/
> design.fingerprint` sidecar is the simpler primitive: globbing siblings
> is the lookup; the artifact tree is already pruned by §H9.

---

## L1. Architecture overview

### L1.1 Module map (new + changed)

**New top-level module:** none. The implementation lives entirely under
`orchestrator/` because (a) it is pure orchestration policy (fingerprint
compute, prior-run selection, digest render) and (b) the only I/O is reads
into sibling run dirs — already an orchestrator capability via `result.py`'s
`_artifact_index` glob pattern.

**New module:** `src/forge_mcp/orchestrator/lineage.py` — mirrors
`triage.py` (mostly pure) and `convergence.py` (pure) with one I/O helper
for the filesystem scan. Owns: `fingerprint_design`, `find_lineage_runs`,
`summarize_prior_run`, `render_prior_attempts`, `PriorRunSummary` dataclass.

**Changed modules:**

| File | Change | Section |
|---|---|---|
| `orchestrator/lineage.py` (new) | Fingerprint compute, sibling scan, per-run summary, digest render. | §L2 / §L3 / §L4 / §L5 |
| `artifacts.py` | New `write_design_fingerprint(inputs_dir, fp)` helper (atomic, 0600); new `write_prior_attempts(inputs_dir, text)` helper that caps at 32 KiB and overflows to `inputs/prior_attempts-overflow.md`. | §L2 / §L6 |
| `orchestrator/engine.py` | Inside the existing `try:` block, after the existing `register_active_run` (§R3.2) and `canonicalize_design` calls (the spec preserves their existing register-then-canonicalize order — see §L10 sketch), inline two adjacent additions: (a) the fingerprint compute + write (§L2.3), (b) the lineage discovery + digest write wrapped in `try/except Exception` for `L-Inv 1` best-effort. The block stays inlined (no `compute_and_persist_lineage` wrapper helper) — the body is small and the engine sketch in §L10 reads more obviously without an indirection. | §L10 |
| `orchestrator/ledger.py` | New `linked_prior_runs: list[str]` (which run_ids' digests this run was given) + `lineage_overflow_path: Path \| None`. | §L8 |
| `orchestrator/result.py` | `build_result` populates `RunResult.linked_prior_runs` from `ledger.linked_prior_runs`; `_artifact_index` populates `ArtifactIndex.prior_attempts_path` + `prior_attempts_uri` + `design_flaws_path` + `design_flaws_uri` when the corresponding files exist. | §L8 |
| `config.py` | `RunConfig.lineage_top_k: int = 4` (env `FORGE_LINEAGE_TOP_K`, validated `[0, 10]`; `0` disables). | §L3.1 / §L8 |
| `models.py` | `RunForgeInput.ignore_prior_attempts: bool = False`; `RunResult.linked_prior_runs: list[str] = []`; `ArtifactIndex.prior_attempts_path`, `prior_attempts_uri`, `design_flaws_path`, `design_flaws_uri` (all `str \| None = None`). | §L8 |
| `resources.py` | `_ALLOWED_ARTIFACTS` gains four exact-match entries — `inputs/design.fingerprint`, `inputs/prior_attempts.md`, `inputs/prior_attempts-overflow.md`, `design_flaws.json` — plus the matching `expand_scope_to_resources` walk-list updates (§L9.2) so a host can audit "what did the planner see?" via `forge://...`. | §L9 |
| `prompts/planner_system.md` | Append the anti-anchoring directive (§L7). | §L7 |
| `prompts/evaluator_remediation.md` | Mirror the anti-anchoring directive so remediation contracts also respect the "don't repeat what didn't work" rule. | §L7 |

### L1.2 Data flow (no live context, all on-disk)

```
engine.run()                                       # §8.1 + §H13 + §C5 + §R6
  ├─ canonicalize_design(inputs, run_dir, ledger)  # existing — resolves design.md
  ├─ write inputs/design.fingerprint                # NEW §L2: sha256 of canon text
  ├─ capture_state + capture_uncommitted            # existing (gitguard helpers)
  ├─ warn_if_missing_target_agents_md               # existing
  ├─ register_active_run(scope)                     # existing §R3.2
  ├─ if not inputs.ignore_prior_attempts            # NEW §L10
  │    AND config.lineage_top_k > 0:
  │    summaries = lineage.discover_and_summarize(
  │        harness_dir, fingerprint,
  │        current_run_id=run_id,
  │        top_k=config.lineage_top_k)
  │    if summaries:
  │      text = lineage.render_prior_attempts(summaries)
  │      write inputs/prior_attempts.md             # capped, overflows
  │      ledger.linked_prior_runs = [s.run_id for s in summaries]
  ├─ run_phases(deps, sm, ledger, base_git,        # existing
  │             task=self._task)
  │    ├─ run_plan_phase                            # existing — planner reads
  │    │                                            # inputs/prior_attempts.md
  │    │                                            # via add_dirs=[inputs/]
  │    └─ run_iteration_loop                        # existing
  └─ build_result(... linked_prior_runs=
                  ledger.linked_prior_runs)         # NEW §L8
```

The planner phase is **structurally unchanged** — it reads its existing
`add_dirs=[inputs/]` and discovers `prior_attempts.md` next to `design.md`.
The driver gets no new arg; the prompt gets one new paragraph (§L7).

`L-Invariant 1 (cold-start fallback):` any failure in fingerprint compute,
sibling scan, per-run summary read, render, or write → log to
`ledger.warnings` + non-fatal status update + proceed as cold-start. Lineage
is **best-effort**, never fail-fatal. The §6.3 error taxonomy is unchanged;
no lineage path raises into `handle_failure`.

---

## L2. Fingerprint

### L2.1 Canonicalization

The fingerprint is a SHA-256 hex digest over a *canonicalized* design-doc
text. Canonicalization is intentionally **minimal**:

1. Decode bytes as UTF-8 with `errors="strict"` — fail-soft to cold-start on
   `UnicodeDecodeError` (`L-Inv 1`).
2. Normalize line endings: `text.replace("\r\n", "\n").replace("\r", "\n")`
   — Windows checkouts shouldn't spuriously mismatch Unix ones.
3. Strip trailing whitespace per line: `"\n".join(line.rstrip() for line in
   text.split("\n"))` — editor-induced trailing space shouldn't matter.

That is the whole transform. No collapse of internal whitespace, no
case-normalize, no markdown reformatting. **Deliberately stricter** than
`triage.py`'s `canonicalize_for_citation` (which collapses ALL `\s+` to one
space). The rationale (Rule 4):

- A paragraph insertion *should* invalidate lineage because the planner's
  strategy may now be wrong.
- A heading rename *should* invalidate lineage because gap-section citations
  in prior `eval.json` no longer line up.
- A trailing-newline edit *should not* invalidate (so the rstrip).
- A CRLF/LF normalization *should not* invalidate (cross-platform parity).

> **Override-on-edit deferred.** An operator who edits the design doc for a
> typo fix and wants to keep the lineage cannot — the fingerprint changes,
> the new run is cold-start, the prior digests are inaccessible. Adding a
> `lineage_fingerprint_override: str | None` caller field is a reasonable
> future enhancement (§L15 Forward-looking); the simpler default holds in v1.

### L2.2 `fingerprint_design`

```python
def fingerprint_design(text: str) -> str:
    """Return the canonical SHA-256 hex digest of a design doc (§L2).

    Design: §L2.1 canonicalization is minimal so a meaningful edit
        invalidates lineage but cross-platform whitespace noise does not.
        The hex digest is what lands in inputs/design.fingerprint and what
        find_lineage_runs glob-matches siblings against.
    Implementation: normalize line endings (CRLF/CR -> LF), strip trailing
        whitespace per line; hashlib.sha256(canonical.encode("utf-8"))
        .hexdigest(). Pure, total, idempotent.
    Example: fingerprint_design("# heading\\r\\n\\nbody  \\n") ==
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08".
    """
```

The hash is a full 64-char hex string; no truncation. SHA-256's 256 bits
already give collision-safety far beyond any deployment scale.

### L2.3 Persistence: `inputs/design.fingerprint`

Written by a new `artifacts.write_design_fingerprint(inputs_dir, fp)`
helper, atomically via the existing `atomic_write_text` pattern, chmod
0600 (consistent with all §13 artifacts). One line, no trailing
metadata — just the hex string + a single newline. Glob-friendly,
human-readable, trivially diffable.

The fingerprint write happens in `engine.run` **immediately after**
`canonicalize_design` returns (the same `try:` block, the next two lines
— see §L10), and is wrapped in `try/except OSError` per §L-Inv 1
best-effort. An ENOSPC / EROFS at the fingerprint write does NOT
fail-fatal the run; the lineage block is short-circuited via `fp = None`
and a `ledger.warnings` entry is emitted. The fingerprint file's
existence implies `design.md` exists with content matching it — but the
inverse no longer holds: `design.md` present without `design.fingerprint`
means the fingerprint write hit an OSError and the run continued
cold-feed-forward. `find_lineage_runs` skips fingerprint-absent siblings
regardless of cause, so the relaxed invariant is safe. (On §H2 resume,
the fingerprint write runs again — see §L4.3 — but `canonicalize_design`
does NOT.)

> **Why not move the write inside `canonicalize_design` itself?** Two
> reasons. (a) `canonicalize_design`'s single responsibility today
> ("resolve `design_doc_path`-or-`design_doc_content` into
> `inputs/design.md`") stays clean; the fingerprint is a §L2 concern, not
> a canonicalization concern. (b) The lineage block (§L10) reads the
> fingerprint hex string back from local variable, so doing the write
> inline at the engine layer keeps the data flow obvious to a reader of
> `engine.run`. The §L-Inv 2 pairing invariant is enforced by code
> locality (the two writes are adjacent lines in `engine.run`), not by
> a wrapper function — equivalent strength, smaller blast radius.

`L-Invariant 2 (fingerprint best-effort, design.md ⊇ fingerprint):`
`inputs/design.fingerprint` is written in the same engine pre-loop
sequence as `inputs/design.md` (immediately adjacent lines in
`engine.run`), wrapped in `try/except OSError` so an ENOSPC at the
fingerprint write does NOT fail-fatal the run (per L-Inv 1). The
invariant is therefore **`design.md` ⊇ `fingerprint`**, NOT
bidirectional `iff`: a run with `design.md` but no fingerprint
indicates the fingerprint write hit an OSError; a `ledger.warnings`
entry was emitted and the run continued cold-feed-forward. A run with
fingerprint but no `design.md` IS the truly impossible state (the
fingerprint is computed FROM the read of `design.md` so the order
forecloses it). `find_lineage_runs` skips fingerprint-absent siblings
regardless of cause (treats them as cold-start-eligible, never
matchable).

---

## L3. Selection policy

### L3.1 Eligibility (terminal + fingerprint + not-cancelled)

A prior run under `<target_dir>/.harness/` is **lineage-eligible** for the
current run iff:

1. It is a **different** `run_id` than the current run (obvious).
2. Its `inputs/design.fingerprint` exists AND equals the current
   fingerprint exactly (case-sensitive hex string compare; see §L2).
3. Its `state.json.state` is one of `{"completed", "incomplete", "failed"}`
   (i.e., terminal — see `StateLiteral` in §7 + §H1 + §H2). Non-terminal
   states (`init`, `canonicalizing`, `planning`, `iter_*`, `finalizing`,
   `cancelling`) signal an in-flight or crashed run; in-flight runs are
   covered by the active-run registry (§R3) and have no useful learning
   yet; crashed runs may have inconsistent gap state.
4. Its `state.json.cancelled` is **false**. Client-disconnect cancellations
   (§8.5) have no useful learning — they don't represent "we tried this
   approach and it didn't work", they represent "the operator pulled the
   plug." A `cancelled=true` run is also forensic-noisy (the §8.5
   five-step ordering may have left partial artifacts).

> **Note on filter interaction.** A §8.5-cancelled run ends with
> `state.json.state == "failed"` AND `cancelled == True` (the cancellation
> ordering transitions to `failed` in step 4, with `cancelled=True`). The
> rule-3 state filter alone does NOT exclude it (it lands in
> `{completed, incomplete, failed}`); the rule-4 `cancelled == False`
> filter is the load-bearing discriminator for cancelled runs. A
> process that crashed *during* cancellation (between transitions to
> `cancelling` and `failed`) would have `state == "cancelling"` AND
> `cancelled == True`; rule-3 rejects it by state. Both filters MUST be
> applied — rule-3 catches in-flight and crashed-mid-cancellation,
> rule-4 catches clean cancellations. Document inline at
> `find_lineage_runs` so a future contributor refactoring §8.5 doesn't
> silently bypass either.

`L-Invariant 3 (terminal-only):` lineage feeds **only** from terminal,
non-cancelled prior runs. An in-flight run (whose state is non-terminal)
never feeds another; a cancelled run never feeds another. Both filters
(rule 3 state + rule 4 cancelled) MUST be applied — neither alone is
sufficient.

### L3.2 Selection: top-K by recency

When ≥1 prior runs are eligible, select the **top-K** by `state.json.
last_updated_at` descending (most-recent first). `K` is
`RunConfig.lineage_top_k`, default **4**, configurable via
`FORGE_LINEAGE_TOP_K`, validated to `[0, 10]`. `K=0` disables the feature
entirely (per-deployment kill switch); `K>10` is rejected by `RunConfig.
from_env` (fail-fast, Rule 8).

> **Why recency, not "best progress"?** Recency reflects the most-recent
> target-dir code state most accurately. A run from three weeks ago that
> reached `completed` might be irrelevant because the codebase has moved on
> since (other commits, unrelated changes). The most-recent few runs are
> more likely to share the same code baseline. "Best progress" sounds
> attractive (carry the run that closed the most gaps) but invites
> survivorship bias — the planner anchors on whatever the most-progressed
> attempt did, even if a more-recent attempt deliberately tried a different
> approach.

> **Why K=4 (default), not 3?** Empirically (the article's principle 15
> framing on plateaus), 2 stuck runs may be coincidence, 3 is a pattern,
> 4 is "this approach is definitively wrong, pick something else." K=4
> surfaces the "we've failed this 4x in a row" signal that K=3 sometimes
> misses while staying within the *typical-case* 32 KiB digest budget
> (4 × ~5 KiB digest = ~20 KiB, headroom for the planner's other
> context). The *worst-case* per-summary footprint is ~19.5 KiB
> (§L5.2); a pathological prior run triggers the §L6.2 overflow plus the
> per-summary 8 KiB hard cap (L-Decision 5) so the K=4 budget remains
> bounded under all inputs.

> **K=0 deployment.** Multi-tenant operators sharing a target_dir, or
> operators with sensitive workloads who can't accept any cross-run text
> leakage, set `FORGE_LINEAGE_TOP_K=0`. The feature becomes inert: no
> fingerprint compare, no digest write, no `linked_prior_runs` populated,
> no `prior_attempts.md` artifact. Behavior is byte-identical to today.

### L3.3 Exclusions called out

- The **current** `run_id` is excluded explicitly. The current run's
  `inputs/design.fingerprint` is written *before* lineage discovery (§L10),
  so without the exclusion the run would match itself.
- A run dir whose `state.json` is missing or unreadable is **skipped
  silently** (`L-Inv 1` cold-start fallback). A partially-initialized run
  dir (crashed before `state.json` was first written) is not eligible.
- A run dir whose `inputs/design.fingerprint` is missing or unreadable is
  skipped. The pre-§L runs in a harness will never have a fingerprint —
  they cannot retroactively become lineage-eligible. This is intentional:
  retrofitting a fingerprint to a legacy run would require canonicalizing
  its `inputs/design.md` *now* and hoping it hasn't drifted; cleaner to
  treat lineage as forward-only.

---

## L4. Lineage discovery

### L4.1 `find_lineage_runs`

```python
@dataclass(frozen=True)
class LineageCandidate:
    """One eligible prior run located by the sibling scan (§L4).

    Design: §L3 eligibility rules are checked as the scan reads each prior
        run dir; LineageCandidate is the small record the selector ranks by
        last_updated_at. Frozen so it is hashable and accidentally
        immutable across the selection pass.
    Implementation: dataclass with run_id, run_dir, last_updated_at; the
        scan returns list[LineageCandidate] pre-filtered, the selector then
        sorts and truncates to top-K.
    Example: LineageCandidate(run_id="abcd1234",
        run_dir=Path("/r/.harness/abcd1234"),
        last_updated_at=datetime(2026, 5, 20, 14, 30, tzinfo=UTC)).
    """
    run_id: str
    run_dir: Path
    last_updated_at: datetime


def find_lineage_runs(
    harness_dir: Path,
    fingerprint: str,
    *,
    current_run_id: str,
    top_k: int,
) -> list[LineageCandidate]:
    """Scan harness_dir for eligible prior runs, return top-K by recency (§L4).

    Design: §L3 eligibility (different run_id, fingerprint match, terminal
        state, not cancelled, not a symlink) is enforced in this single
        function so the rest of the lineage pipeline can treat its output
        as the authoritative selection. Cold-start fallback (L-Inv 1) is
        applied per-candidate — one unreadable state.json silently drops
        that candidate, never poisons the whole scan.
    Implementation: iterate `harness_dir.iterdir()` (matches the
        `prune_old_runs` / §R3 active-run idiom; the codebase uses
        iterdir+regex, not a glob char class), skip non-directories, skip
        names not matching `_RUN_ID_RE = re.compile(r"^[0-9a-f]{8}$")`
        (the §R3 regex), **skip `is_symlink()` directories outright**
        (security pin §L-Inv 5 — see §L16 risk 11; a planted symlink at a
        run_id-shaped name otherwise lets an attacker with write access
        to harness_dir inject planner-prompt content via forged
        state.json + fingerprint), skip the current_run_id, read each
        `inputs/design.fingerprint` (skip on missing/unreadable), compare
        to fingerprint (skip on mismatch), read each `state.json` via
        state.read_state (skip on missing/malformed), filter by terminal
        state AND `cancelled == False` (§L3.1 — both filters required;
        cite §L-Inv 3 in an inline comment), build LineageCandidate from
        run_id + run_dir + state.last_updated_at, sort by
        last_updated_at descending, return the first top_k. top_k=0
        → return [].
    Example: candidates = find_lineage_runs(harness_dir, fp,
        current_run_id="abcd1234", top_k=4).
    """
```

The iterdir + `_RUN_ID_RE` filter matches the §6.5 `^[0-9a-f]{8}$`
`run_id` shape so non-run sibling files (e.g., `run.lock`, `.gitignore`)
are filtered out before any IO into a per-candidate dir — same idiom
`prune_old_runs` (`artifacts.py`) and §R3 already use. The `is_symlink()`
rejection is **mandatory**: see §L16 risk 11 (symlink poisoning is a real
prompt-injection attack vector via the digest body).

### L4.2 Failure tolerance

Every per-candidate I/O is wrapped in `try/except (OSError,
pydantic.ValidationError)`: a single broken run dir (missing
`state.json`, malformed JSON, fingerprint file truncated mid-write,
permission-denied) **silently drops that candidate** and the scan
continues. This mirrors §R6's per-scope `try/except OSError`
discipline. (`pydantic.ValidationError` IS a subclass of `ValueError`
in pydantic v2, so the tuple covers both — listing `ValueError`
separately would be redundant and misleading.)

A whole-scan failure (e.g., `harness_dir.iterdir()` itself raises) is
caught by the engine-side wrapper and converted to a `ledger.warnings`
entry + cold-start (`L-Inv 1`). Lineage is best-effort; the run still
proceeds.

### L4.3 Resume interplay (§H2)

The §H2 resume path re-enters at `last_completed_iteration + 1`. Two
considerations:

1. **Resume re-canonicalizes the design doc** (§8.1's `canonicalize_design`
   runs on the resume path too — it is idempotent w.r.t. the `inputs/
   design.md` write because the design doc text hasn't changed). So
   `inputs/design.fingerprint` is rewritten with the same hash.
2. **Resume re-runs `find_lineage_runs`** because the resumed engine.run
   call goes through the same pre-loop sequence. The selection is the
   same set of prior runs (the harness state hasn't changed), so the
   resumed run sees the same `linked_prior_runs` — but the planner phase
   is **skipped** on a resume (§H2.5 "skip `run_plan_phase`"), so the
   `prior_attempts.md` written on resume is *unused*. That's fine — it's
   forensic-only on a resumed run, exactly mirroring the `plan/sessions.
   json` situation on resume.

> **Correction on `canonicalize_design` re-run.** The current
> `engine.py` does NOT re-run `canonicalize_design` on a resume —
> resume gates the canonicalize block on `if resume_point is None:`
> and the design.md from the original run persists on disk. The
> fingerprint compute in §L10 lands OUTSIDE that gate, so it runs
> on resume too: it re-reads the existing `inputs/design.md` and
> re-writes `inputs/design.fingerprint`. The behavioral outcome
> matches "fingerprint rewritten with the same hash", but the
> mechanism is "fingerprint compute re-runs on its own; canonicalize
> does NOT" — earlier prose called canonicalize_design itself
> idempotent on resume, which was imprecise.

`L-Invariant 4 (resume preserves identity):` on a §H2 resume, the
fingerprint, lineage discovery, and `prior_attempts.md` write all re-run
deterministically; the result is identical to what the pre-crash run
wrote, modulo any new prior runs that completed *after* the pre-crash
discovery and *before* the resume. (This is benign — the resumed run skips
planning anyway.)

---

## L5. Per-prior-run summarization

### L5.1 `PriorRunSummary`

```python
@dataclass(frozen=True)
class PriorRunSummary:
    """One prior run, distilled into the fields the planner will read (§L5).

    Design: §L5 selects the highest-signal-per-token fields from a prior
        run's artifacts. Bounded sizes (10 unresolved gaps, 5 design
        flaws, 2000-char verify tail) target a ~3-5 KiB typical summary
        so K=4 totals ~20 KiB under nominal conditions and triggers the
        §L6.2 overflow only in pathological cases (worst case ~19.5 KiB
        per summary, §L5.2; per-summary hard cap of 8 KiB applied in
        render — no single run consumes >25% of the digest budget).
        Full eval.json / summary.md / contract.md are deliberately
        excluded (§L5.3).
    Implementation: frozen dataclass; fields are pre-truncated by
        summarize_prior_run; render_prior_attempts (§L6) interpolates them
        into the markdown template with no further mutation. `reason`
        mirrors `state.json.reason` (the durable field name — NOT
        `ledger.stop_reason`, which is in-memory only; see §L5.2 for the
        cross-walk and the small engine extension required to persist
        §H3 break reasons).
    Example: PriorRunSummary(run_id="abcd1234", status="incomplete",
        reason="non-progress: gap-set unchanged for 4 iters",
        iterations_used=10, runtime_seconds=2820,
        unresolved_gaps=[...], design_flaw_gaps=[...],
        verify_tail="... 14 tests failed, 0 passed",
        eval_summary="missing /healthz; missing tests").
    """
    run_id: str
    status: Literal["completed", "incomplete", "failed"]
    reason: str | None                   # from state.json.reason; see §L5.2
    iterations_used: int                 # derived from state.last_completed_iteration
    runtime_seconds: int                 # derived from (last_updated_at - started_at)
    unresolved_gaps: list[EvalGap]       # capped to top 10, title-truncated
    design_flaw_gaps: list[DesignFlawGap]  # capped to top 5
    verify_tail: str | None              # last 2000 chars of last iteration's verify.txt
    eval_summary: str | None             # last iteration's eval.json.summary
```

### L5.2 `summarize_prior_run`

```python
def summarize_prior_run(run_dir: Path) -> PriorRunSummary | None:
    """Read a prior run's terminal artifacts into a PriorRunSummary (§L5).

    Design: §L4's eligibility filter already guarantees state.json
        is readable and terminal; summarize_prior_run extracts the
        gap/verify/summary slice the planner will read. Any per-artifact
        read failure (missing eval.json, malformed verify.txt, missing
        design_flaws.json) degrades the corresponding field to None /
        empty list rather than failing the whole summary — L-Inv 1
        cold-start applies per-field, not per-run.
    Implementation: read state.json via state.read_state; derive
        status = state.state (already terminal per §L3.1); reason =
        state.reason (may be None — see "reason persistence" below);
        iterations_used = state.last_completed_iteration (NOT
        state.iteration — the latter is current-iteration count);
        runtime_seconds = int((state.last_updated_at -
        state.started_at).total_seconds()). Glob iteration-N/ subdirs
        by numeric sort, take the LAST (highest N); read
        iteration-N/eval.json -> EvalResult, extract unresolved_gaps
        (top 10 by severity, truncate title to 200 chars,
        current/expected/fix to 500 chars each); read
        run_dir/design_flaws.json (new sidecar — see §L8.4 / §L9.2)
        -> list[DesignFlawGap] top 5 by severity-then-iteration_n
        ascending; read iteration-N/verify.txt tail (last 2000 chars,
        replace UTF-8 errors); set eval_summary = eval.summary.
        Returns None only if state.json itself could not be read after
        find_lineage_runs already accepted the candidate (a TOCTOU race
        — silently treat as ineligible per L-Inv 1).
    Example: summary = summarize_prior_run(Path("/r/.harness/abcd1234")).
    """
```

> **`reason` persistence and the §H3 break.** `state.json.reason` is set
> on a handful of paths today: `handle_cancellation` writes
> `reason="client-disconnected"`, `handle_failure` writes
> `reason=str(exc)`. The §H3 non-progress break that sets
> `ledger.stop_reason = signal.reason` currently surfaces ONLY in
> `RunResult.message` (`result.py`), never reaching `state.json`. For
> lineage to carry the §H3 break reason forward to the next planner,
> the engine MUST pass `reason=` on the incomplete transition when
> `ledger.stop_reason` is set: change `sm.transition("incomplete", ...)`
> in `engine.run` to `sm.transition("incomplete", reason=ledger.stop_reason)`
> (a one-line addition — `RunState.reason` is already an optional field
> and `RunStateMachine.transition`'s `**fields` already accepts it).
> Without this, `summarize_prior_run` sees `reason=None` on
> §H3-broken prior runs; the next planner loses the "pick a different
> approach" signal that L-Decision 1 makes load-bearing.

> **`design_flaws.json` sidecar (new artifact).** `RunResult.design_flaw_
> gaps` lives in-memory only; `design-flaw-gaps-overflow.md` stores
> titles-only and is title-capped. Neither carries the structured
> `DesignFlawGap` fields (`fault_kind`, `cited_sections`, `explanation`,
> `iteration_n`) lineage needs. The smallest fix: add a
> `run_dir/design_flaws.json` sidecar write in `result.build_result`
> (or `lifecycle.handle_*` paths) that serializes
> `ledger.design_flaw_gaps` to disk during finalizing. Schema is the
> existing `DesignFlawGap` pydantic shape — one `model_dump_json` of a
> `{"gaps": [...]}` envelope, atomic_write_text, 0600. New allowlist
> entry in §L9.2; new artifact path in §L8.3; tests in §L13.2. Without
> it `summarize_prior_run` cannot populate `design_flaw_gaps`.

**Top-K gap selection within a run:**

- `unresolved_gaps` are ranked by `severity` (a free-form string, but
  conventionally `"high" > "medium" > "low"` — see §7's `EvalGap.severity`
  note). Sort: `"high"` first, then `"medium"`, then `"low"`, then
  alphabetical. Take top 10. Each `title` truncated to 200 chars with
  `"…"` appended on overflow. `current_state` / `expected_state` /
  `suggested_fix` are also truncated (to 500 chars each) to keep one gap
  ≈ 1.2 KiB.
- `design_flaw_gaps` are higher-signal (citations into the design doc).
  Take top 5 by severity-then-`iteration_n` ascending (earlier iteration
  flaws are more foundational). No truncation of citations (they are
  whitespace-canonicalized substrings of the design doc per §11.1, so
  bounded by the design doc itself).

**Why these caps?** The worst case at full per-field caps is 10
unresolved × 1.2 KiB + 5 design flaws × 1 KiB + 2 KiB verify tail +
~500 B state metadata ≈ 19.5 KiB per prior run. The *typical* case
(few unresolved gaps, short titles, no verify_command) is closer to 3-5
KiB. **K=4 × typical ≈ 20 KiB** fits comfortably under the 32 KiB
digest cap (§L6.2); **K=4 × worst-case ≈ 78 KiB** triggers the §L6.2
overflow — kept head goes to `prior_attempts.md`, dropped tail to
`prior_attempts-overflow.md`. To prevent a single pathological prior
run from starving the others (e.g., summary #1 at 30 KiB leaves nothing
for #2-#4), `summarize_prior_run` ALSO applies a **per-summary hard cap
of 8 KiB**: any field set whose rendered size exceeds 8 KiB has its
verify_tail truncated further (to 1000, then 500 chars), then its
current/expected/fix prose fields truncated (to 300 chars each), and
finally — if still over cap — **trailing gaps are dropped from the
sorted top-10 list (lowest-severity-then-alphabetic-tail dropped first
so highest-severity preserved)** until the rendered summary fits. The
drop-from-tail step handles the title-heavy pathology where 10 ×
200-char titles dominate and no prose remains to truncate; without it,
the cap is unreachable on such inputs and `render` would either
silently violate it or raise (defeating `L-Inv 1`). Implementation may
either invoke `render_prior_attempts([candidate])` iteratively after
each truncation pass to measure (≤4 render calls worst-case, ~5 KiB
each ≈ ~20 KiB overhead) OR compute the cap structurally via
`sum(len(s.encode("utf-8")) for s in interpolated_fields)`; both
satisfy `L-Decision 5`. Pin in §L13.1 with a single test asserting
the post-truncation rendered output of one summary is ≤ 8192 bytes. The 8 KiB per-summary cap guarantees the
most-recent run never consumes >25% of the digest budget (`L-Decision
5`; pin in §L13). The 32 KiB total cap is justified on its own merits
(planner context-economy budget; matches the §11.2 "warnings/gaps
truncation" discipline of bounding all on-disk-to-prompt handoffs).

### L5.3 What is deliberately excluded

The summary intentionally **does not** include:

- The full `eval.json` blob (verbose; the structured `unresolved_gaps`
  carry the actionable signal).
- The `summary.md` generator narration (token-heavy free prose).
- The `iteration-N/contract.md` text (the prior planner's strategy — would
  anchor the new planner to a documented-failed approach; the *gaps* are
  the right signal, not the strategy that produced them).
- The `triage.json` blob (the structured `design_flaw_gaps` already carry
  the triage's promoted output; raw triage rows include the code-bug
  fallback path that is noise for the planner).
- `iteration-N/sessions.json` (§C2 forensic-only — `C-Inv 3`).
- `iteration-N/git-violation.txt` (rare; if it happened, the next planner
  doesn't need to know — that's a generator-behavior concern, not a
  strategy concern; the §11.3 layered prevention already handles re-runs).
- Any `run.log` content (sensitive per §13; never crosses the tool
  boundary — see §R-Inv 3).

> **`L-Decision 1`:** the lineage digest is **lossy by design**. We feed
> the planner *what didn't work* (gaps + verify failures) and *what the
> design doc itself got wrong* (design flaws), NOT *what the prior
> generator tried* (contract.md, summary.md). Anchoring on the prior
> strategy would defeat the purpose of cross-run learning (Rule 4); the
> goal is "next planner picks a different approach", not "next planner
> incrementally improves the prior approach."

---

## L6. Render and write the digest

### L6.1 `render_prior_attempts`

```python
_MAX_DIGEST_BYTES = 32_768          # §L6 — planner context-economy budget.
_MAX_PER_SUMMARY_BYTES = 8_192      # §L6 — no single prior run >25% of budget (L-Decision 5).


def render_prior_attempts(summaries: list[PriorRunSummary]) -> str:
    """Render a list of prior-run summaries as one markdown digest (§L6).

    Design: §L6 produces a single self-contained markdown file the planner
        reads through its existing add_dirs=[inputs/] surface. The
        anti-anchoring boilerplate (§L7) is the first heading so it lands
        in the planner's working context BEFORE any gap text — readers
        front-load the "treat this as evidence about what didn't work"
        framing. Caps are enforced by the writer (§L6.2), not the
        renderer — render produces the natural text; the writer truncates
        if it exceeds _MAX_DIGEST_BYTES.
    Implementation: pure templating; one '## Run <run_id>' section per
        summary with sub-sections for status/reason/iterations,
        unresolved gaps (severity-grouped), design-flaw gaps (with
        fault_kind + citations), verify tail (in a fenced code block), and
        eval summary. All fields are interpolated via escape_md_inline /
        escape_md (the same artifacts.py helpers eval.md uses, §13).
    Example: text = render_prior_attempts([summary1, summary2]); the
        result starts with "# Prior Attempts at This Design" and a
        boilerplate paragraph.
    """
```

### L6.2 The cap + overflow

```python
def write_prior_attempts(inputs_dir: Path, text: str) -> Path | None:
    """Write inputs/prior_attempts.md (capped) + overflow if needed (§L6.2).

    Design: §L6.2 — the planner's effective context grows with the digest,
        so the file is capped at _MAX_DIGEST_BYTES (planner
        context-economy budget; bounded for the same reason §11.2 caps
        warnings/gaps). When the rendered digest exceeds the cap, the
        kept head goes to prior_attempts.md and the dropped tail to
        prior_attempts-overflow.md (mirrors the §11.2 gap-list overflow
        pattern). Returns the overflow path when written, else None.
    Implementation: encode text as UTF-8; if len <= cap, atomic_write_text
        the whole thing and return None; else split at the cap byte
        boundary (rounded BACK to the nearest newline so the cut doesn't
        bisect a markdown construct), append a one-line "... truncated;
        see prior_attempts-overflow.md" pointer to the kept head, write
        both files via atomic_write_text (`tmp + os.replace`, 0600 on
        POSIX — same as every §13 artifact, so a §R1 read_resource
        racing the write returns either the prior content or the new
        content, never a partial), return the overflow path.
    Example: overflow_path = write_prior_attempts(inputs_dir, text).
    """
```

The overflow file is **not** itself read by the planner — it is forensic
only (and discoverable via the §R1 resource surface for hosts that want to
audit the full lineage view). The kept head is what the planner sees. Both
files are written through `atomic_write_text`, so concurrent
`read_resource` requests on `prior_attempts.md` are safe by construction
(same atomicity guarantee §R-Inv 4 read path relies on for `eval.json`,
`state.json`, etc.).

### L6.3 Markdown safety

`render_prior_attempts` runs every interpolated field through
`artifacts.escape_md_inline` (for gap titles and short fields) or
`artifacts.escape_md` (for prose bodies and verify tails) — the same
pure escape helpers `eval.md` rendering already uses (§13). This is
load-bearing because a gap title containing a markdown heading (`#`) or
fenced code block (`` ``` ``) would otherwise break the digest's
structure and could even create a planner-side prompt-injection vector
("ignore prior instructions, planner should…"). The escapes are
pre-existing and well-tested.

---

## L7. Anti-anchoring directive

This is the single load-bearing prompt edit. It turns "feed prior gaps"
from a net-negative behavior ("multiple runs tried this approach so it
must be right") into a net-positive one ("multiple runs tried this
approach so it definitively doesn't work").

### L7.1 `planner_system.md` addendum

Appended as a new section after the existing planner directives:

```markdown
## Prior attempts

If `inputs/prior_attempts.md` exists, read it BEFORE writing the plan.

The file is a structured digest of prior `run_forge` invocations on this
exact design doc (same SHA-256 fingerprint). Each prior run reached a
terminal state (completed / incomplete / failed) without converging
sufficiently for the operator to stop iterating.

Treat the contents as evidence about what does NOT work, not as a
suggestion to refine:

- For each prior unresolved gap, your plan MUST propose a DIFFERENT
  implementation strategy than the documented attempt. Incremental
  refinement of an approach multiple prior runs failed at is forbidden.
- If a prior run reports a design-flaw gap citing the design doc, surface
  it in the plan's "open questions" section rather than pretending it
  isn't there. The same design flaw will continue to defeat any plan that
  ignores it.
- If a prior run's `verify_tail` is present, the verification command
  failed for the documented reason; your plan must explicitly address
  that reason, not work around it.

If `inputs/prior_attempts.md` does not exist, plan normally (cold start).

## Write scope

You MUST NOT write to any path outside the `plan/` directory. Specifically:
do not use Write/Edit on `../inputs/`, `../iteration-*/`, the
`<run_dir>` top-level, or any absolute path. The orchestrator
authoritatively overwrites `inputs/prior_attempts.md` on every run;
your writes there are overwritten before the next run reads them, but
they remain forensic evidence of disobedience. Stay inside `plan/`.
```

### L7.2 `evaluator_remediation.md` mirror

The remediation prompt (which authors the next iteration's contract from
the prior iteration's eval results) gets a shorter mirror paragraph:

```markdown
## Cross-run learning

If `inputs/prior_attempts.md` exists, the same anti-anchoring rule applies
to remediation: the new contract must propose a DIFFERENT implementation
strategy than any documented-failed approach. Do not propose a remediation
that maps onto a prior run's failed unresolved gap.
```

This is shorter because remediation already has the current run's eval
context as primary signal — `prior_attempts.md` is supplementary.

### L7.3 Why a prompt, not a structured input

> **Alternative considered & rejected.** Adding a `prior_unresolved_gaps:
> list[EvalGap]` field on the planner's `output_format` schema. Rejected:
> (a) the planner's output is the plan itself (free-form markdown), not
> structured data; (b) the planner doesn't *return* the prior gaps, it
> reasons with them as input; (c) the file-handoff pattern matches the
> existing `inputs/design.md` access shape exactly. Schema-shaping the
> output for an input-only concern would be wrong.

The prompt addition is testable: a §L13 prompt-pin test asserts the exact
addendum text is present (mirrors the §18 `SCHEMA_RETRY_SUFFIX` pin).

---

## L8. Public API & model changes (consolidated)

All additions are **optional fields** so the §18 / §H16 / §C8 / §R9 input
and output schema pins (object-root, no top-level combinators; cap fields
expose `minimum`/`maximum`) hold and existing callers are unaffected.

### L8.1 `RunForgeInput`

```python
ignore_prior_attempts: bool = False     # §L8 — explicit opt-out; default
                                        # uses lineage when present. Set True
                                        # to start with a pristine planner
                                        # even when prior matching runs exist
                                        # (e.g., prior attempts were on a
                                        # known-buggy version of the code and
                                        # would mis-anchor the new planner).
```

### L8.2 `RunResult`

```python
linked_prior_runs: list[str] = []       # §L8 — run_ids whose digest
                                        # informed this run's planner;
                                        # empty when cold-start (no lineage)
                                        # or when ignore_prior_attempts was
                                        # True or when lineage_top_k == 0.
                                        # Stable order: most-recent first
                                        # (same order rendered into the
                                        # digest).
```

### L8.3 `ArtifactIndex`

```python
prior_attempts_path: str | None = None      # §L8 — inputs/prior_attempts.md
                                            # (absent on cold-start runs)
prior_attempts_uri: str | None = None       # §L8 — §R1 forge:// URI companion
                                            # to prior_attempts_path; same
                                            # encode_uri call pattern as the
                                            # other *_uri fields (§R4.2)
design_flaws_path: str | None = None        # §L8.4 — <run_dir>/design_flaws.json
                                            # sidecar (new artifact §L5.2 needs)
design_flaws_uri: str | None = None         # §L8.4 — §R1 forge:// URI companion
```

### L8.4 `design_flaws.json` sidecar (new artifact)

A new run-dir-level artifact serializing `ledger.design_flaw_gaps` to disk
so prior-run summarization (§L5.2) can read structured `DesignFlawGap`
records. Written during finalizing (the result-building path in
`result.py` / `lifecycle.handle_*`) alongside the existing artifacts.

- Path: `<run_dir>/design_flaws.json`
- Mode: 0600 via `atomic_write_text` (POSIX) — same discipline as `state.
  json` and `eval.json`.
- Shape: `{"gaps": [DesignFlawGap.model_dump(mode="json"), …]}`. Empty
  list when no design-flaws triggered (we still write the artifact so its
  absence reliably means "this run predates §L8.4", not "had no flaws").
- Allowlist entry: `_ArtifactPattern("design_flaws.json", None,
  "application/json")` in `_ALLOWED_ARTIFACTS` (§L9.2).
- `_artifact_index` adds the `.exists()` check + `encode_uri`
  population in the same pattern `state_json_path` / `state_json_uri`
  uses.

The artifact is forensic + lineage-only; no current code path in the
*same* run reads it. A future cross-run feature beyond lineage could
read it, but `R-Inv 1` / `R-Inv 2` / `L-Inv 0` continue to hold.

### L8.5 `RunLedger`

```python
linked_prior_runs: list[str] = field(default_factory=list)   # §L8
lineage_overflow_path: Path | None = None                     # §L8 — set by
                                                              # write_prior_
                                                              # attempts when
                                                              # the digest
                                                              # exceeded the
                                                              # cap; paired
                                                              # with a
                                                              # ledger.warnings.
                                                              # append() at the
                                                              # same call site
                                                              # (§L10).
```

### L8.6 `RunConfig`

```python
lineage_top_k: int = 4                  # §L8 — env FORGE_LINEAGE_TOP_K,
                                        # validated [0, 10]. K=0 disables
                                        # the feature entirely (per-deployment
                                        # kill switch — multi-tenant ops or
                                        # sensitive-workload ops set this
                                        # to opt out wholesale).
```

`RunConfig.from_env`'s validation: parse `os.environ.get(
"FORGE_LINEAGE_TOP_K", "4")` as int; on `ValueError` re-raise the
`ValueError` with a clear message; on out-of-range raise `ValueError`
similarly. This matches the existing `FORGE_HARNESS_ROOTS` validation
discipline in `config.py` (which already raises `ValueError`, not
`McpError`) — `config.py` MUST stay free of `mcp.*` imports. The
call-site (`server.py`'s `_RESOURCE_CONFIG = RunConfig.from_env()` at
module scope, **and** the per-call `RunConfig.from_env()` inside
`run_forge_handler` — §R6) is responsible for translating to
`McpError(SERVER_ERROR)` if it wants client-visible signal.

> **Validation timing (operator UX).** A typo'd `FORGE_LINEAGE_TOP_K`
> raises at server.py **import time** via the module-scope
> `_RESOURCE_CONFIG = RunConfig.from_env()` line, BEFORE the MCP
> transport is up. The host sees the server fail to spawn; no JSON-RPC
> error is surfaced. This is correct (fail-fast, Rule 8) but
> opaque — operators discover bad env via `forge serve` exit code +
> stderr, not via a graceful protocol response. Mitigation: **`forge
> doctor` must also call `RunConfig.from_env()`** so the operator sees
> the validation error in the doctor's normal `[FAIL]` line shape
> before attempting `serve`. Add this to §L14 sequencing.

### L8.7 Schema-pin discipline

The §18 / §H16 / §C8 / §R9 pins all derive from `RunForgeInput.
model_json_schema()` / `RunResult.model_json_schema()`. The new fields
are all `bool` / `list[str]` / `str | None`, none of which introduce
top-level combinators or non-object roots. The pins hold without
modification.

`ArtifactIndex` and `RunResult` post-additions still produce object-root
output schemas. `RunForgeInput` post-addition still produces an object-root
input schema (the existing `model_validator` xor for `design_doc_path`
vs `design_doc_content` is unchanged; `ignore_prior_attempts` is
independent).

---

## L9. Module map & dependency-graph compliance

### L9.1 New imports / edges

**Allowed new edges:**
- `orchestrator.engine → orchestrator.lineage` (pre-loop lineage compute)
- `orchestrator.lineage → state` (read prior runs' `state.json` via
  `state.read_state`)
- `orchestrator.lineage → artifacts` (write `design.fingerprint`,
  `prior_attempts.md`, `prior_attempts-overflow.md`)
- `orchestrator.lineage → models` (read prior runs' `EvalGap` /
  `DesignFlawGap` shapes — but only by reading on-disk JSON, not by
  importing run-state)
- `orchestrator.lineage → (stdlib only otherwise)` — `hashlib`, `pathlib`,
  `dataclasses`, `re`, `datetime`, `typing`
- `orchestrator.result → (no change)` — `_artifact_index` uses
  `.exists()` checks (NOT a glob) for top-level artifacts; adding
  `inputs/prior_attempts.md`, `inputs/design.fingerprint`,
  `design_flaws.json` is three new `.exists()` checks alongside the
  existing `git_state_exists` / `plan_sessions_exists` pattern. The
  `_uri` companions are populated via `encode_uri` when the token is
  available (same conditional pattern §R4.2 / §R5.2 already uses).
- `resources.py → (no change in edges)` — adding entries to
  `_ALLOWED_ARTIFACTS` is a data change; the §L9.2 list_resources walk
  update is also a data change.

**Forbidden edges** (still forbidden, no new violations):
- `lineage → drivers/*` (lineage is orchestration policy; never reaches
  into the SDK seams)
- `lineage → orchestrator.engine` (would cycle)
- `drivers/* → lineage` (drivers don't know about lineage; the planner
  reads `prior_attempts.md` through its existing `add_dirs=[inputs/]`,
  not through a driver method)
- `resources → lineage` (resources is a stdlib-only leaf; lineage is its
  consumer, not vice versa)

**Invariant preservation:**
- **Invariant 1 (single state writer)** — `lineage.py` only *reads*
  `state.json`. Only `orchestrator/*` (via `RunStateMachine`) writes it.
- **Invariant 4 (append-only artifacts within a phase)** — `lineage.py`
  writes `inputs/design.fingerprint` once (during `canonicalize_design`)
  and `inputs/prior_attempts.md` once (immediately after lineage
  discovery, before `run_phases`). Neither is rewritten within a run.
- **Invariant 6 (per-call orchestrator)** — lineage discovery is per-call
  (the candidate scan and digest write happen inside `Orchestrator.run`);
  no module-level lineage cache.
- **L-Inv 0 (north star)** — preserved; see §L0.2 / §L1.2.
- **R-Inv 1 (registry is forensic-only)** — preserved; lineage does not
  consult the active-run registry (it reads the on-disk run dirs
  directly, so an in-flight active run that is not yet terminal is also
  not lineage-eligible by §L3.1).

### L9.2 `resources.py` allowlist + list-walk additions

```python
# §L9.2 — append to _ALLOWED_ARTIFACTS (§R2.3):
_ArtifactPattern("inputs/design.fingerprint", None, "text/plain"),
_ArtifactPattern("inputs/prior_attempts.md", None, "text/markdown"),
_ArtifactPattern("inputs/prior_attempts-overflow.md", None, "text/markdown"),
_ArtifactPattern("design_flaws.json", None, "application/json"),  # §L8.4 sidecar
```

**Also update `resources.expand_scope_to_resources`'s walk lists**
(`resources.py`, the `inputs/` and run-dir top-level enumerations) to
include the new filenames — otherwise the artifacts are
`match_artifact`-eligible (so `read_resource` serves them) but
`list_resources` does NOT emit them. The existing inputs/ enumeration
hardcodes `("design.md", "git-state.txt", "git-uncommitted.txt")`; add
`"design.fingerprint", "prior_attempts.md", "prior_attempts-overflow.md"`.
Top-level enumeration adds `"design_flaws.json"`.

`run.log` exclusion (§R-Inv 3) is unaffected — the new entries are
benign artifacts. The §R9.1 negative pins (`"run.log" not in
{p.subpath for p in _ALLOWED_ARTIFACTS}`) still hold.

The §R9.3 list_resources composition tests need a fixture update to
include `inputs/prior_attempts.md`, `inputs/design.fingerprint`, and
`design_flaws.json` so the "one Resource per existing allowlisted
artifact" pin covers them.

---

## L10. Enhanced control flow (integrated)

Augments §C5 / §H13 / §R6 control flow in **one place** — the engine
pre-loop sequence. Everything else (the iteration loop, terminal handlers,
finally block, registry deregister) is unchanged.

```
server.py (Path B handler — unchanged):
  os.umask(0o077); RunForgeInput.validate(...); config = RunConfig.from_env()
  prepared = await prepare_run(inputs, config)
  harness_token = compute_harness_token(prepared.harness_dir)
  if _client_requested_task_mode(ctx):
    async def work(task):
      return _result_to_call_tool_result(
          await _orchestrator_entry(prepared, inputs, config, ctx,
                                    task=task, harness_token=harness_token))
    return await ctx.experimental.run_task(work)
  return _result_to_call_tool_result(
      await _orchestrator_entry(prepared, inputs, config, ctx,
                                task=None, harness_token=harness_token))


Orchestrator.run():
  ... existing pre-try setup (§8.1, §C5, §R6) — Status construction,
      RunStateMachine, RunLedger, PhaseDeps build, base_git is None ...
  inputs_dir = run_dir / "inputs"                                # NEW (§L10) —
                                                                  # explicit local
                                                                  # so §L2/§L6
                                                                  # helpers don't
                                                                  # have to derive
  scope = _ResourceScope(...)                                    # §R3.2
  try:
    register_active_run(scope)                                   # §R3.2
    sm.transition("canonicalizing")
    canonicalize_design(inputs, run_dir, ledger)                 # §8.1 — writes design.md
    # §L2 fingerprint compute + write. Wrapped in try/except OSError so
    # an ENOSPC/EROFS at this exact byte does not fail-fatal a run that
    # could still proceed cold-start — L-Inv 1 best-effort extended to
    # the fingerprint write per §L16 risk 2.
    try:
      design_text = (inputs_dir / "design.md").read_text(encoding="utf-8")
      fp = lineage.fingerprint_design(design_text)
      artifacts.write_design_fingerprint(inputs_dir, fp)
    except OSError as exc:
      fp = None  # disables lineage block below (find_lineage_runs needs fp)
      ledger.warnings.append(
          f"design.fingerprint write failed (cold start): {type(exc).__name__}: {exc}")
      logger.warning("design.fingerprint write failed", exc_info=True)
    base_git = capture_state(deps.target_dir)                    # existing (gitguard)
    capture_uncommitted(deps.target_dir)                          # existing (gitguard)
    await warn_if_missing_target_agents_md(...)                  # existing

    # §L10 — lineage compute + digest write. Best-effort (L-Inv 1).
    # PRECONDITION (§L16 risk 10): bound by §H9 prune_old_runs already
    # capping `<harness>/<run-id>/` count; deployments with prune disabled
    # SHOULD set FORGE_LINEAGE_TOP_K=0 or accept that find_lineage_runs is
    # O(N_dirs) on the prune-disabled population. The lineage block runs
    # OUTSIDE the asyncio.wait_for(run_phases) runtime cap, so a
    # pathological harness can burn wall-clock here; this is acceptable
    # because the bound is set by §H9 not by max_runtime_minutes.
    if fp is not None and not inputs.ignore_prior_attempts \
       and config.lineage_top_k > 0:
      try:
        candidates = lineage.find_lineage_runs(
            prepared.harness_dir, fp,
            current_run_id=run_id, top_k=config.lineage_top_k)
        if candidates:
          summaries = [s for s in (lineage.summarize_prior_run(c.run_dir)
                                   for c in candidates) if s is not None]
          if summaries:
            text = lineage.render_prior_attempts(summaries)
            overflow_path = artifacts.write_prior_attempts(inputs_dir, text)
            ledger.linked_prior_runs = [s.run_id for s in summaries]
            ledger.lineage_overflow_path = overflow_path
            if overflow_path is not None:
              # §L8.5 warnings tee — the overflow path is set AND the
              # warning surfaced; reading either without the other is a
              # documentation defect.
              ledger.warnings.append(
                  f"lineage digest exceeded {_MAX_DIGEST_BYTES} bytes; "
                  f"overflow at {overflow_path.name}")
            await status.update(phase="canonicalizing", agent="orchestrator",
                                message=f"lineage: {len(summaries)} prior "
                                        f"runs feeding planner")
      except Exception as exc:
        # L-Inv 1: lineage is best-effort, never fail-fatal.
        ledger.warnings.append(
            f"lineage discovery failed (cold start): {type(exc).__name__}: {exc}")
        logger.warning("lineage discovery failed", exc_info=True)

    terminal_status, iters = await asyncio.wait_for(
        run_phases(deps, sm, ledger, base_git, task=self._task),
        timeout=inputs.max_runtime_minutes * 60)
    # §H3 break reason persistence — required for §L5.2 to read
    # state.reason on lineage-resumed planners. Pass reason=ledger.stop_reason
    # on the incomplete transition iff set. (RunState.reason is already
    # optional; RunStateMachine.transition's **fields accepts it.)
    if terminal_status == "incomplete" and ledger.stop_reason:
      sm.transition("incomplete", reason=ledger.stop_reason)
    else:
      sm.transition(terminal_status)
    # §L8.4 design_flaws.json write — serializes ledger.design_flaw_gaps
    # for prior-run summarization (§L5.2). MUST snapshot BEFORE
    # apply_caps_and_overflow runs (apply_caps_and_overflow truncates
    # ledger.design_flaw_gaps[:GAP_LIST_CAP] in place — without the
    # snapshot the sidecar would silently lose the truncated tail,
    # defeating its entire purpose of preserving the structured records
    # the title-only overflow.md can't carry). Wrapped in try/except
    # OSError for L-Inv 1 best-effort consistency with the fingerprint
    # write (§L16 risk 1 — both pre-loop and post-loop §L writes are
    # best-effort; failure here disables lineage for THIS run feeding
    # forward but doesn't fail the run itself).
    design_flaws_full = [g.model_copy(deep=True)              # §L16 risk 1:
                         for g in ledger.design_flaw_gaps]    # deep-copy so
                                                              # downstream
                                                              # in-place
                                                              # mutation of
                                                              # title/cited_
                                                              # sections by
                                                              # future
                                                              # cap helpers
                                                              # doesn't
                                                              # contaminate
                                                              # the sidecar.
    try:
      artifacts.write_design_flaws(run_dir, design_flaws_full)
    except OSError as exc:
      ledger.warnings.append(
          f"design_flaws.json write failed (lineage feed-forward "
          f"disabled): {type(exc).__name__}: {exc}")
      logger.warning("design_flaws.json write failed", exc_info=True)
    ... existing rest of engine.run (apply_caps_and_overflow + build_result) ...
  except ...                                                     # unchanged
  finally:                                                       # unchanged
    ...
  return build_result(..., harness_token=self._harness_token,
                     linked_prior_runs=ledger.linked_prior_runs)  # NEW kwarg
```

The lineage block lands **after** `canonicalize_design` (so the
fingerprint reflects the canonical design.md text) and **before**
`run_phases` (so `prior_attempts.md` exists when the planner phase starts).
It lives inside the existing outer `try:` so an unexpected `Exception`
from the lineage code path falls through to the `except` ladder — but the
explicit `try/except Exception` around the lineage block converts every
lineage error to a warning, so the ladder is **never** reached via a
lineage failure under normal operation. The `try/except Exception` is the
load-bearing `L-Inv 1` enforcement; without it, a malformed
`design.fingerprint` from a corrupted sibling run could fail-fatal a
healthy new run. The fingerprint write itself is wrapped in
`try/except OSError` for the same reason — see §L16 risk 2.

The `design_flaws.json` write lands at the same scope as the existing
finalizing writes (after the loop's terminal transition, before
`apply_caps_and_overflow` + `build_result`). It is **best-effort**
(`try/except OSError` → warning + cold-feed-forward) for consistency
with the §L10 fingerprint write — both are §L additions, both write
small files into `.harness/<run_id>/`, both should fail symmetrically
under disk pressure. The pre-cap snapshot
(`design_flaws_full = list(ledger.design_flaw_gaps)`) is load-bearing:
without it, the sidecar serializes the *capped* list after
`apply_caps_and_overflow` truncates `ledger.design_flaw_gaps` to
`GAP_LIST_CAP`, defeating the sidecar's entire purpose of preserving
the structured tail. The existing title-only
`design-flaw-gaps-overflow.md` complements the sidecar — the latter
carries the full structured records for the head AND the tail; the
former is the title-only readable summary of the dropped tail.

**Per-terminal-path consistency.** The handle_timeout / handle_failure
paths in `lifecycle.py` also call `apply_caps_and_overflow` internally
(see §H1.5 / §11.2). To preserve the pre-cap snapshot semantics on
those paths, the sidecar write MUST also land inside those handlers
BEFORE their cap call — equivalently, all three terminal paths
(success-inline, timeout, failure) must snapshot
`ledger.design_flaw_gaps` and write the sidecar before any code
mutates the list. Add the same snapshot-then-write block to
`handle_timeout` and `handle_failure` in step 8 of §L14.

**Resume path:** §H2 resume runs `canonicalize_design` (idempotent) but
*skips* `run_plan_phase`. The lineage block runs on resume too (it's part
of the engine pre-loop sequence), writing a fresh fingerprint and
re-discovering lineage; the resulting `prior_attempts.md` is unused (the
planner doesn't run on resume) but is forensically consistent. The
`ledger.linked_prior_runs` populated on resume reflects the resume-time
lineage view, which matches what `RunResult.linked_prior_runs` reports for
the resumed run — the field is honest about what the planner *would have*
read had it run.

---

## L11. Invariant & taxonomy preservation (summary)

The §6.3 bright line, §8.5 ordering, §H-Inv chain, §C-Inv chain, and §R-Inv
chain are **all unchanged**. Every new path slots into an existing terminal:

- **Lineage compute failure** → caught by the §L10 explicit
  `try/except Exception` → warning + cold-start. Never raises into
  `handle_failure` / `handle_timeout` / `handle_cancellation`.
- **Fingerprint mismatch / no prior runs** → cold-start; behavior is
  byte-identical to today.
- **Digest cap exceeded** → overflow file written; the head digest is
  still the planner's input; `ledger.warnings` entry surfaced.
- **`ignore_prior_attempts=True`** → lineage block skipped entirely;
  `linked_prior_runs=[]`; behavior is byte-identical to today.
- **`lineage_top_k=0`** → lineage block skipped entirely (per-deployment
  kill switch).
- **Resume** → lineage re-runs deterministically; `prior_attempts.md` is
  written but unused by the skipped planner.
- **Cancellation during the lineage block** → `asyncio.CancelledError`
  propagates (the `try/except Exception` does NOT catch `BaseException`
  subclasses; `CancelledError` is `BaseException` (since Python 3.8) so it's
  unaffected and reaches the engine's `except asyncio.CancelledError`
  handler — §8.5 ordering preserved).

**New invariants summary:**

- **`L-Inv 0`** — cross-run learning crosses the run boundary via files
  only; no SDK session, transcript, or in-memory state persists across
  runs (north-star re-assertion).
- **`L-Inv 1`** — lineage is best-effort; every per-step failure
  degrades to cold-start with a warning, never raises into the §6.3
  taxonomy.
- **`L-Inv 2`** — `inputs/design.fingerprint` is written iff
  `inputs/design.md` is written in the same canonicalize step; a run
  with one but not the other is a broken on-disk state and
  `find_lineage_runs` treats it as ineligible.
- **`L-Inv 3`** — lineage feeds only from terminal, non-cancelled prior
  runs (`state` in `{completed, incomplete, failed}` AND
  `cancelled=False`). Both filters MUST be applied (a §8.5 cancellation
  ends with `state=failed` AND `cancelled=True`, so the state filter
  alone misses it; a process that crashed mid-cancellation has
  `state=cancelling`, so the cancelled filter alone misses it). In-flight
  and cancelled runs never feed.
- **`L-Inv 4`** — on §H2 resume, lineage compute re-runs
  deterministically; the resumed-run `RunResult.linked_prior_runs`
  reflects the resume-time discovery (which the planner would have read
  if it ran, but on resume it doesn't). The §H9 `prune_old_runs` race
  (a lineage-eligible prior run pruned between discovery and read) is
  handled by `summarize_prior_run` returning None on `FileNotFoundError`
  (per L-Inv 1 cold-start). §H9 uses `shutil.rmtree(path,
  ignore_errors=True)` (artifacts.py), which is a Python-level
  recursive unlink, NOT a single kernel atomic operation; a permission
  error or open file handle during prune CAN leave partial residue
  (e.g., a run dir missing `state.json` but with `design_flaws.json`
  still present, or vice versa). Such residue is tolerated by L-Inv 1:
  a candidate with missing/unreadable `state.json` or
  `design.fingerprint` is silently skipped by `find_lineage_runs`
  before any other read. On an *otherwise-eligible* post-§L8.4 run,
  `design_flaws.json` absence has THREE indistinguishable causes:
  (i) pre-§L8.4 legacy run that never wrote the file;
  (ii) post-§L8.4 run whose §L10 sidecar-write hit an OSError (a
  warning was emitted into `ledger.warnings`);
  (iii) partial-rmtree residue where `design_flaws.json` was
  unlinked but `state.json` survived (rare).
  All three reduce to `design_flaw_gaps=[]` in the new summary —
  semantically equivalent for the planner (which sees no design-flaw
  signal from this prior run and falls back to its own reasoning).
  Forensic disambiguation requires reading the prior run's `state.json`
  / `RunResult` for the warning; `summarize_prior_run` does NOT
  attempt it.
- **`L-Inv 5`** — lineage candidates MUST be real directories under
  `harness_dir`, never symlinks. `find_lineage_runs` rejects
  `is_symlink()` candidates outright (§L16 risk 11). Without this,
  symlink poisoning lets an actor with write access to `harness_dir`
  inject planner-prompt content via the digest body — the §L6.3 markdown
  escapes reduce but do not eliminate adversarial-input risk.
- **`L-Inv 6`** — **defense-in-depth against prompt-injection self-loop.**
  The planner's `disallowed_tools=("Edit",)` (per `drivers/planner.py`)
  does NOT block Write — `cwd=plan/` is a working-directory hint, not a
  chroot, and Claude SDK Write tools accept absolute paths and `../`
  traversal. Three layers prevent a planner from poisoning
  `inputs/prior_attempts.md`:
  (a) **Prompt directive** in `planner_system.md` forbids writes
  outside `plan/` (added by §L7.1 + a new dedicated "Write scope"
  paragraph the implementer MUST add — pinned by §L13.4);
  (b) **Orchestrator authoritatively overwrites** `inputs/prior_attempts.md`
  on EVERY new run (§L10's `write_prior_attempts` call uses
  `atomic_write_text` which replaces, not appends) — even a disobedient
  planner's write is overwritten before the next run reads it;
  (c) **Forensic detection**: `inputs/prior_attempts.md`'s mtime
  immediately after the planner phase should equal the orchestrator
  write's mtime; a later mtime indicates planner disobedience (a §L13
  observability test could pin this, but not required for v1).
  No single layer is sufficient on its own; the combination eliminates
  the self-loop in practice.

---

## L12. Best-practice grounding (article + context7)

| Surface | Article principle | context7 evidence |
|---|---|---|
| Cross-run digest as file handoff | 1 (context reset via durable files), 7 (file handoff), 14 (iterate on the harness through experiments) | The right primitive is file handoff over forge-mcp's existing artifact tree. Claude SDK's `session_store` is for in-conversation transcript persistence (S3/Postgres/Redis adapters at `/anthropics/claude-agent-sdk-python` — `examples/session_stores/README.md`), NOT cross-run knowledge persistence; repurposing it would conflate transcript continuity with distilled learning and require threading live context across runs (violates L-Inv 0). MCP's `lifespan_context` (`/modelcontextprotocol/python-sdk` README) is server-startup shared state, not cross-call learning. |
| Anti-anchoring directive | 15 (allow strategic pivoting between iterations — extended to "between runs"); article's plateau-recognition framing | Pure prompt policy; no SDK angle. Mirrors the §H3.3 pivot directive at a different time horizon (cross-run vs cross-iteration within a run). |
| Fingerprint canonicalization | "components encode assumptions; stress-test them" | sha256 is unambiguously the right hash (collision-safe at any deployment scale); the canonicalization choice is the load-bearing decision (§L2.1) — minimal canonicalization deliberately invalidates lineage on meaningful edits. |
| Top-K + cap discipline | 16 (don't over-structure); the article's discipline around context economy | Bounded summaries keep the planner's effective context under control. Mirrors the §11.2 caps + overflow pattern that already governs `RunResult.warnings` and `unresolved_gaps`. |
| Allowlist artifact discovery via §R1 | 11 (instrument for observability over long runs) | The new artifacts (`design.fingerprint`, `prior_attempts.md`, `prior_attempts-overflow.md`) join the §R2.1 allowlist so hosts can audit "what did the planner see?" without filesystem access. |

The base architecture's "fresh context per phase, file handoff across
phases" discipline already implements the article's core. The H/C/R bricks
extended that to verification, durability, continuity, and host
observability. This brief closes the **plateau-across-runs** corner: a
harness that learns at the *run* boundary, not just the iteration
boundary, by feeding the planner what didn't work — without threading any
live context.

---

## L13. Testing strategy (extends §18, §H16, §C8, §R9)

TDD discipline matches §H16 / §C8 / §R9: write the failing test first;
all existing tests stay green; Rule 21 docstrings on every new `def`;
`scripts/ci.sh` green at the end.

### L13.1 Pure / unit (no SDK / no disk)

- **`fingerprint_design`**:
  - Identical bytes → identical digest.
  - CRLF/LF normalization: `"a\r\nb"` and `"a\nb"` → same digest.
  - Trailing whitespace strip: `"a  \nb"` and `"a\nb"` → same digest.
  - Heading rename invalidates: `"# A\n"` vs `"# B\n"` → different digests.
  - Empty string and whitespace-only have stable, distinct digests.
  - UTF-8 with multi-byte chars: round-trips via .encode("utf-8").
  - Digest matches `^[0-9a-f]{64}$`.
- **`render_prior_attempts`**:
  - Empty list → empty string (caller treats empty as "do not write").
  - One summary → markdown with the anti-anchoring boilerplate first,
    then one `## Run <id>` section.
  - Multiple summaries → all preserved in the same order, most-recent first.
  - A gap title with a `#` or fenced ``` ``` `` is escaped (no broken
    structure; no prompt-injection vector).
  - A summary with `verify_tail=None` omits the verify section.

### L13.2 I/O units (mocked filesystem)

- **`artifacts.write_design_fingerprint`**: atomic write; chmod 0600 on
  POSIX; idempotent across re-writes (same content); content is exactly
  the hex string + "\n".
- **`artifacts.write_prior_attempts`**: under-cap text written whole;
  over-cap text writes head + overflow files; overflow path returned;
  cut respects newline boundaries (no markdown bisection); cap is
  enforced in bytes (UTF-8 length).
- **`find_lineage_runs`**:
  - Empty harness dir → `[]`.
  - One eligible prior run (fingerprint match, terminal, not cancelled) →
    one candidate.
  - Multiple eligible → sorted most-recent-first, truncated to top_k.
  - **Fingerprint mismatch → skipped**.
  - **Non-terminal state → skipped** (test each of init, canonicalizing,
    planning, planned, iter_generating, iter_verifying, iter_evaluating,
    iter_triaging, iter_done, iter_remediating, finalizing, cancelling
    — every non-terminal `StateLiteral` value must skip; enumerate them
    explicitly to catch a future state-machine literal addition that
    forgets to update the lineage filter).
  - **cancelled=True → skipped**.
  - Missing `design.fingerprint` → skipped silently.
  - Malformed `state.json` → skipped silently.
  - Current `run_id` → excluded even if eligible.
  - Permission-denied per-candidate → skipped silently; scan continues.
  - top_k=0 → returns [] regardless of candidates.
  - top_k > available candidates → returns all available.
- **`summarize_prior_run`**:
  - Full happy path (state.json + eval.json + verify.txt + design-flaw
    overflow): all fields populated, caps applied, severity ordering
    correct.
  - Missing `verify.txt` → `verify_tail=None`, other fields populated.
  - Missing `eval.json` (no iterations completed) → `eval_summary=None`,
    `unresolved_gaps=[]`.
  - Iteration numeric sort: `iteration-10/` selected over `iteration-2/`
    (the same numeric-sort discipline §11.5 enforces).
  - Truncation: a gap title >200 chars is truncated with `"…"`; a
    `current_state` >500 chars likewise.
  - Top-10 gap selection by severity (high > medium > low; ties broken
    alphabetically).
  - Top-5 design-flaw selection by severity then `iteration_n` ascending.

### L13.3 Loop / driver units (mocked filesystem, no SDK)

- **Cold-start (no prior runs)**: `linked_prior_runs=[]`,
  `prior_attempts.md` absent, `RunResult.artifacts.prior_attempts_path`
  is None, `prior_attempts_uri` is None, planner phase unchanged.
- **Lineage active (1 prior run)**: `inputs/prior_attempts.md` exists
  with the rendered digest; `RunResult.linked_prior_runs ==
  [prior_run_id]`; `RunResult.artifacts.prior_attempts_path` is set;
  `RunResult.artifacts.prior_attempts_uri` is the matching
  `forge://...` string (parity with the §R4.2 `*_uri` companions);
  **`RunResult.artifacts.design_flaws_path` and `design_flaws_uri`
  are also populated** (the §L8.4 sidecar was written and indexed —
  pin both companions to catch a future `_artifact_index` refactor
  that drops one); the prior_attempts file is in the planner's
  `add_dirs=[inputs/]` working set.
- **Lineage active (>top_k prior runs)**: top_k selected by recency;
  older eligibles not in `linked_prior_runs`.
- **`ignore_prior_attempts=True`**: even with eligible prior runs, the
  lineage block is skipped; `linked_prior_runs=[]`; no
  `prior_attempts.md` written. Verify ALSO that the fingerprint IS
  still written (the file pair invariant L-Inv 2 holds independent of
  ignore_prior_attempts).
- **`lineage_top_k=0`** (env): kill switch — the fingerprint *is* always
  written (so the next run could match it). Only the lineage discovery
  + digest write are skipped. Verify both: fingerprint exists,
  prior_attempts.md does not.
- **Cap exceeded** (lineage_top_k=4 with prior runs each pushing the
  cap): `prior_attempts.md` is head; `prior_attempts-overflow.md` is
  tail; `ledger.lineage_overflow_path` populated AND a paired
  `ledger.warnings` entry emitted (verify both — the §L10 sketch
  requires them to land together; a contributor refactoring the
  overflow-write must not drop the warning).
- **Per-summary cap** (one prior run with pathological content
  exceeding 8 KiB): `summarize_prior_run`'s verify_tail / current /
  expected / fix truncations fire; rendered summary fits 8 KiB; other
  prior runs are not starved. Cite `# §L6 _MAX_PER_SUMMARY_BYTES`.
- **Best-effort fallback**: a sibling run with a permission-denied
  `state.json` does not fail the new run — the candidate is silently
  skipped, the rest of the lineage compute proceeds.
- **Failure-mode poisoning**: a synthetic `OSError` raised at the
  `find_lineage_runs` outer scan converts to a `ledger.warnings` entry
  and the run proceeds cold-start (`L-Inv 1`). No `RunResult` field
  other than `warnings` is mutated.
- **Fingerprint write disk-full**: a synthetic `OSError` raised at
  `artifacts.write_design_fingerprint` is caught; `fp` is set to None;
  warning surfaced; lineage block is skipped (`fp is not None` guard);
  the run proceeds cold-start. Pin §L16 risk 2.
- **§H3 break reason persistence**: a synthetic prior run whose
  `ledger.stop_reason` is set sees `sm.transition("incomplete",
  reason=ledger.stop_reason)` — verify `state.json.reason` on disk
  carries the expected text, and `summarize_prior_run` reads it back
  as `PriorRunSummary.reason`.
- **Symlink rejection (§L-Inv 5)**: place a symlink at
  `<harness>/abcd1234/` pointing at an attacker-controlled directory
  with a forged state.json + fingerprint matching the current run's
  fingerprint; `find_lineage_runs` skips it (is_symlink check);
  `RunResult.linked_prior_runs` does NOT include "abcd1234". Cite
  `# §L-Inv 5`.
- **§H9 prune race**: prior run is pruned between `find_lineage_runs`
  (which accepted it) and `summarize_prior_run`'s read; the latter
  returns None on `FileNotFoundError`; the rest of the summaries
  proceed; `linked_prior_runs` omits the pruned id silently.
- **`design_flaws.json` on timeout (§L14 step 8)**: a run that times
  out with non-empty `ledger.design_flaw_gaps` writes
  `<run_dir>/design_flaws.json` BEFORE `apply_caps_and_overflow` (which
  runs inside `handle_timeout`) truncates the in-memory list; the
  sidecar carries the full pre-cap structured records. Cite
  `# §L14 step 8`.
- **`design_flaws.json` on failure (§L14 step 8)**: symmetric pin for
  `handle_failure` path.
- **Resume continuity (§H2 interplay)**: a resumed run re-runs the
  lineage block; the resulting `linked_prior_runs` is honest about the
  resume-time view; the planner phase is skipped so the new
  `prior_attempts.md` is unused but the artifact is present and
  resource-readable. ALSO test: a resume with `ignore_prior_attempts=
  True` skips the lineage block on resume (the caller-supplied flag
  governs the resumed pass).
- **`design_flaws.json` write** (§L8.4): a run with non-empty
  `ledger.design_flaw_gaps` writes `<run_dir>/design_flaws.json`
  during finalizing with the expected `{"gaps": [...]}` shape; the
  artifact is allowlisted and readable via `forge://...`. A run with
  empty `ledger.design_flaw_gaps` ALSO writes the artifact (empty
  list — so absence on the disk has THREE indistinguishable causes
  per §L-Inv 4: pre-§L8.4 legacy, write-failure-with-warning, or
  partial-rmtree residue; all reduce to `design_flaw_gaps=[]` in
  the new summary).

### L13.4 Prompt / schema / transport pins (extend §18, §H16, §C8, §R9)

- **Planner-prompt addendum pin** (mirror of the §18 `SCHEMA_RETRY_SUFFIX`
  pin): assert the exact §L7.1 markdown block is present in
  `planner_system.md`, substring-check after running both stored and
  template through `triage.canonicalize_for_citation` (re-using the
  §11.1 whitespace-canonicalize helper so the pin's canonicalization is
  itself a pinned single source of truth). A future contributor editing
  the addendum has to update the test deliberately. Cite `# §L7.1` and
  `# §11.1 canonicalize_for_citation` on the test.
- **Remediation-prompt addendum pin**: same pattern for §L7.2 in
  `evaluator_remediation.md`. Cite `# §L7.2`.
- **Planner-write-scope pin (§L-Inv 6, layer a)**: assert that
  `planner_system.md` contains the verbatim "## Write scope" paragraph
  added by §L7.1 (substring check, whitespace-canonicalized through
  `triage.canonicalize_for_citation` per the same discipline as the
  prior-attempts pin). The §L-Inv 6 defense layer (a) is the prompt
  directive — the driver's `disallowed_tools=("Edit",)` config does
  NOT block Write, so a config-only pin would assert nothing useful.
  A separate informational assertion that the planner driver's
  `cwd=plan/` is set (config sanity check) is fine but is NOT the
  load-bearing pin.
- **Planner-overwrite-authority pin (§L-Inv 6, layer b)**: assert
  that `engine.run`'s pre-loop sequence writes `inputs/prior_attempts.
  md` AFTER any planner phase would have written there (i.e., the
  orchestrator's `write_prior_attempts(inputs_dir, text)` call lands
  AFTER planner phase completion in a successive run — pin via a
  synthetic two-run test where run-1's planner wrote `inputs/
  prior_attempts.md` with attacker content and run-2 demonstrates
  that the orchestrator-written digest replaces it before run-2's
  planner reads).
- **Schema pins**: `RunForgeInput.model_json_schema()` post-`ignore_prior_
  attempts` is still object-root with no top-level combinators; the
  existing §18 / §H16 / §C8 / §R9 pins hold. `RunResult.model_json_schema()`
  post-`linked_prior_runs` is also still object-root.
- **Resource surface pin (§R9.3 extension)**: the §L9.2 allowlist
  additions appear in `list_resources` output when the artifacts exist;
  reading via `forge://<token>/<run>/inputs/prior_attempts.md`,
  `forge://<token>/<run>/inputs/design.fingerprint`, and
  `forge://<token>/<run>/design_flaws.json` each returns the expected
  MIME via the §R6 read path.

### L13.5 Negative tests

- `FORGE_LINEAGE_TOP_K=-1` → `RunConfig.from_env` raises.
- `FORGE_LINEAGE_TOP_K=11` → raises.
- `FORGE_LINEAGE_TOP_K="abc"` → raises with a clear message.
- A `design.fingerprint` file that is `"not-hex-content\n"` → still
  comparable by string equality (a corrupt fingerprint just won't match
  any other corrupt one); the scan silently skips fingerprint-mismatch
  rather than raising. Pin this so a future "verify hex shape" addition
  is a deliberate change.
- A new run with `ignore_prior_attempts=True` AND `lineage_top_k=4`:
  either kill switch alone skips lineage; the test asserts the union
  (the §L10 guard is `not ignore_prior_attempts AND lineage_top_k>0`,
  so either side being permissive-blocking flips the block off). There
  is no precedence race: both must be permissive to run.

---

## L14. Implementation sequencing

Each step lands behind its new optional surface; partial rollout is
identical to today when callers don't opt in.

1. **`orchestrator/lineage.py` (leaf):** `fingerprint_design`,
   `LineageCandidate`, `find_lineage_runs` (with `is_symlink` rejection
   per §L-Inv 5), `PriorRunSummary` (with `reason` field, not
   `stop_reason`), `summarize_prior_run` (reading `state.reason`,
   `last_completed_iteration`, and the new `design_flaws.json`), and
   `render_prior_attempts` (applying the 8 KiB per-summary cap). Pure
   + small I/O unit tests (§L13.1, §L13.2). No callers yet.
2. **`artifacts.py`:** add `write_design_fingerprint`,
   `write_prior_attempts` (cap + overflow), and `write_design_flaws`
   (§L8.4 sidecar — `{"gaps": [...]}` envelope, atomic 0600). All three
   unit-tested in isolation (§L13.2).
3. **`models.py`:** add `RunForgeInput.ignore_prior_attempts`,
   `RunResult.linked_prior_runs`, `ArtifactIndex.prior_attempts_path` +
   `prior_attempts_uri` + `design_flaws_path` + `design_flaws_uri`.
   Schema-pin tests stay green.
4. **`config.py`:** add `RunConfig.lineage_top_k` with
   `FORGE_LINEAGE_TOP_K` parsing and `[0, 10]` validation in
   `RunConfig.from_env` (raise `ValueError` consistent with
   `FORGE_HARNESS_ROOTS` discipline — `config.py` MUST stay free of
   `mcp.*` imports; call-sites translate to `McpError`). Negative tests
   (§L13.5).
5. **`doctor.py` / `cli.py`:** `forge doctor` already calls
   `RunConfig.from_env()` (cli.py — current code), but a bad
   `FORGE_LINEAGE_TOP_K` raises uncaught `ValueError`, crashing the
   doctor with a Python traceback. To satisfy the §L8.6 "normal `[FAIL]`
   line shape" guarantee, wrap the call in `try/except ValueError` at
   the top of the `doctor()` command and emit a
   `[FAIL] env_config: <exception message>` line on failure (matching
   the existing `(check_name, status, detail)` tuple shape the doctor
   already uses). Additionally, **`server.py`'s module-scope
   `_RESOURCE_CONFIG = RunConfig.from_env()`** still raises at import
   time on bad env — to avoid the opaque "server fails to spawn with
   no diagnostic" case, wrap THAT call in a `try/except ValueError`
   that writes a single stderr line `"Invalid forge-mcp config: <reason>.
   Run \`forge doctor\` for diagnostics."` before re-raising. The
   stderr line gives operators a hook to discover the doctor command.
6. **`orchestrator/ledger.py`:** add `linked_prior_runs` +
   `lineage_overflow_path` fields.
7. **`resources.py`:** append the four §L9.2 entries to
   `_ALLOWED_ARTIFACTS` (`inputs/design.fingerprint`,
   `inputs/prior_attempts.md`, `inputs/prior_attempts-overflow.md`,
   `design_flaws.json`). Update `expand_scope_to_resources`'s `inputs/`
   walk list AND the run-dir top-level walk list to include the new
   filenames (otherwise the artifacts are `match_artifact`-eligible
   but not `list_resources`-discoverable — §L9.2 expanded note).
   Resource-tree fixture in §R9.3 extended to exercise the new entries.
8. **`orchestrator/engine.py` + `orchestrator/lifecycle.py`:** wire the
   §L10 pre-loop block AND the per-terminal-path `design_flaws.json`
   write.
   - In `engine.run`'s outer `try:` body, four localized additions:
     (a) the fingerprint compute + write wrapped in `try/except
     OSError` for `L-Inv 1` (also extends best-effort to fingerprint
     write — §L16 risk 2); (b) the lineage discovery + digest write
     block wrapped in `try/except Exception`; (c) the inline-success
     path's `design_flaws.json` write — snapshot pre-cap, then
     `artifacts.write_design_flaws(...)` BEFORE `apply_caps_and_overflow`;
     (d) change the `incomplete` transition to pass
     `reason=ledger.stop_reason` when set (one-line edit; required
     for §L5.2 to read §H3 break reasons forward).
   - In `lifecycle.handle_timeout` AND `lifecycle.handle_failure`,
     add the SAME pre-cap snapshot + `write_design_flaws` block
     BEFORE the existing `apply_caps_and_overflow` call (each handler
     already calls `apply_caps_and_overflow` internally — see §H1.5 /
     §11.2). Without this, design_flaw_gaps from completed iterations
     on timed-out / failed runs never persist to disk, and lineage
     loses exactly the signal §L8.4 was added to preserve on
     non-converged runs.
   - The conductor stays a thin conductor with the §L10 block as
     adjacent inlined additions; the lifecycle handlers gain three
     lines each (snapshot + try/except OSError + warning).
   - **`stop_reason` invariant pin**: `ledger.stop_reason` is only
     set by §H3 `detect_non_progress` on the inline path
     (`phases.py`); `handle_timeout`'s `sm.transition("incomplete")`
     therefore never has a reason to forward. Pin this in §L13.3 so
     a future refactor that moves `detect_non_progress` doesn't
     silently break the §L10 (d) one-liner.
   - **`run_id` binding**: the §L10 sketch's `current_run_id=run_id`
     is shorthand; in actual `engine.py`, use `self._prepared.run_id`
     (or bind `run_id = self._prepared.run_id` once at the top of
     `run()` if there are many call sites).
9. **`orchestrator/result.py`:** `build_result` populates
   `RunResult.linked_prior_runs` from `ledger.linked_prior_runs`;
   `_artifact_index` adds `.exists()` checks for `inputs/prior_attempts.md`,
   `inputs/design.fingerprint`, and `design_flaws.json`, plus the
   `*_uri` encode_uri calls when `harness_token` is non-None
   (same conditional pattern as the existing §R4.2 `*_uri`
   companions).
10. **Prompt edits:** add the §L7.1 / §L7.2 addenda to
    `planner_system.md` / `evaluator_remediation.md`. Pin tests (§L13.4).
    Verify the planner-write-scope pin (§L-Inv 6) holds against the
    planner driver's existing `cwd=plan/` + `disallowed_tools` config.
11. **Integration tests:** the §L13.3 loop tests using mocked drivers
    and a real `<target_dir>/.harness/` tree of synthesized prior runs
    (including symlink-poisoning, prune-race, §H3-break-persistence,
    per-summary-cap, and `design_flaws.json` fixtures).
12. **Documentation:** README gains a short "Cross-run learning"
    section pointing at `lineage_top_k`, `ignore_prior_attempts`,
    `prior_attempts.md` rendering, and the §H7-style honest threat-model
    note (cross-tenant bleed bounded by identical-design + shared
    `target_dir`; mitigation = distinct `target_dir`s per tenant or
    `FORGE_LINEAGE_TOP_K=0`).
13. **`CLAUDE.md`:** add this doc as a fifth normative source
    (alongside base + hardening + continuity + resource-surface) and
    document the `§L*` / `L-Invariant N` / `L-Decision N` citation
    namespaces. Note that the §18 schema pins continue to derive from
    `RunForgeInput.model_json_schema()` /
    `RunResult.model_json_schema()` (no shift for this brief).
14. Full `scripts/ci.sh` green.

---

## L15. Decisions log (forge-mcp cross-run learning)

| § | Decision | Rationale |
|---|---|---|
| §L0.3 (**L-Decision 0**) | File handoff via `inputs/prior_attempts.md`, not a Claude `session_store` resume or a knowledge DB | SDK `session_store` is for transcript continuity, not cross-run knowledge; a DB violates single-source-of-truth (the filesystem already is); file handoff matches the article's principle 7 and forge-mcp's existing seams (§9.1 planner already reads `add_dirs=[inputs/]`). |
| §L5.3 (**L-Decision 1**) | Digest is **lossy by design** — feed gaps and design flaws, NOT prior contracts / summaries / generator narration | Anchoring on the prior strategy defeats the purpose ("here's what didn't work" is the right signal; "here's what the prior gen tried" anchors). Caps a typical digest at ~5 KiB per run (worst-case ~19.5 KiB per §L5.2; pathological cases trigger the §L6.2 overflow). |
| §L3.1 (**L-Decision 2**) | Lineage **excludes** `cancelled=True` prior runs | Client-disconnect cancellations represent operator action, not a learnable failure; the §8.5 five-step ordering may have left partial artifacts whose gap state is inconsistent. A re-run after a cancellation deserves a cold start. |
| §L3.2 (**L-Decision 3**) | Top-K = 4 default, configurable [0, 10] via env | K=4 surfaces the "we've failed this 4x in a row" plateau signal that K=3 sometimes misses; typical-case digest fits the 32 KiB cap (4 × ~5 KiB ≈ 20 KiB), worst-case overflows (caught by §L6.2). K=0 is the per-deployment kill switch. |
| §L2.1 (**L-Decision 4**) | Fingerprint canonicalization is **minimal** (CRLF/LF normalize + per-line rstrip), NOT triage-style `\s+`-collapse | Meaningful edits should invalidate lineage (a paragraph insertion changes what the planner should plan); cross-platform whitespace noise should not (the right invariant is "same bytes mod platform-encoding noise"). |
| §L6.2 (**L-Decision 5**) | Per-summary hard cap of 8 KiB (`_MAX_PER_SUMMARY_BYTES`) | Without it, summary #1 at the 19.5 KiB worst-case starves the K=2/3/4 older runs (digest budget consumed before the others render). Guarantees no single prior run consumes >25% of the digest budget; truncation falls back through verify_tail → current/expected/fix prose. |
| §L3.2 | Top-K selection is by **recency**, not "best progress" | Recency reflects the most-recent code state most accurately; survivorship bias on "best progress" would anchor the planner on a stale most-progressed attempt even when a more-recent attempt deliberately tried a different approach. |
| §L7.3 | The cross-run signal is a **prompt addendum** (planner reads `prior_attempts.md` through `add_dirs=[inputs/]`), NOT a structured `output_format` field | The planner's output is the plan; cross-run gaps are input-only. Schema-shaping the output for an input concern would be wrong. |
| §L0.3 | No new SDK dependency; no Claude `session_store` adoption; no DB | The §L1.2 data flow is fully on top of existing seams (`canonicalize_design`, `add_dirs`, `state.read_state`, `atomic_write_text`, `_artifact_index`, `_ALLOWED_ARTIFACTS`). Smaller surface, no new failure modes. |
| §L8.6 | `lineage_top_k` parsed at startup with `[0, 10]` validation (Rule 8 fail-fast) via `ValueError`, never silently defaulted | A typo'd env var must not silently disable the feature; the operator notices at startup (via `forge doctor` or `forge serve` exit). `ValueError` (not `McpError`) keeps `config.py` free of `mcp.*` imports — matches the existing `FORGE_HARNESS_ROOTS` validation discipline. |

**Forward-looking (deferred, with stale-assumption notes):**

- **`lineage_fingerprint_override: str | None`** caller field for
  edited-but-equivalent design docs (e.g., a typo fix that the operator
  wants to treat as continuing prior lineage). Adopt when there is real
  operator feedback that the "any edit invalidates lineage" rule is too
  strict in practice. The simpler default holds in v1.
- **Cross-`target_dir` lineage** (a deploy-wide knowledge bank of
  design-flaw findings, indexed by fingerprint across all
  `<target_dir>/.harness/` dirs the server has ever seen). Adopt when a
  multi-tenant deployment story is in scope (related to the §C10 /
  §R11 HTTP-transport forward-looking item). Currently rejected on
  privacy grounds — see §L16 risk 2.
- **Cross-design-doc learning** (e.g., a deployment's standing list of
  "design flaws our planners always find" — backend-cors, missing-tests,
  etc.). Adopt when there is evidence that the same pattern reliably
  recurs across designs; the §C2 / §H18 forward-looking layered-resume
  story will likely surface this naturally first. Until then, YAGNI.
- **Lineage-fed evaluator** (extending the cross-run digest into the
  Evaluator phase's prompt so it knows what prior evaluators found).
  Adopt with care: the Evaluator is the gatekeeper of "done", and
  feeding it prior runs' gaps could anchor it to expect those gaps in
  the current run's output. The current design feeds *only* the
  Planner, where anti-anchoring (§L7) is the explicit antidote. The
  Evaluator already has the current iteration's `verify.txt` (§H1.4)
  and the canonical design doc as its grounding.

---

## L16. Risks & verification notes for the implementer

1. **Best-effort discipline is load-bearing.** Three points enforce
   `L-Inv 1`: (a) the `try/except Exception` block in §L10 around
   lineage discovery; (b) the `try/except OSError` block around the
   fingerprint write (§L10 pre-loop); (c) the `try/except OSError`
   block around `write_design_flaws` called from `engine.run`
   inline-success path AND `lifecycle.handle_timeout` AND
   `lifecycle.handle_failure` (§L14 step 8). Removing any of the three
   (e.g., during a "clean up the engine try block" refactor) turns
   either a corrupted sibling run dir, a disk-full at the wrong
   instant, or a finalizing OSError into a fatal new-run failure. Pin
   §L13.3's "synthetic OSError" tests for ALL THREE call sites; the
   failure message must reference `L-Inv 1` so the reviewer reads the
   rationale before approving the removal.

2. **Fingerprint write fail-fatal regression risk.** §L10's fingerprint
   write is now wrapped in `try/except OSError` so a disk-full / EROFS
   at exactly that instant disables the lineage block (`fp = None`
   short-circuits the guard) but doesn't fail the run. Pin in §L13.3.
   A future refactor that moves the write outside the try (e.g., into
   `canonicalize_design`) MUST re-establish the wrap — otherwise §H1's
   "verify gate" deployments where disk pressure is real (every
   iteration writes verify.txt) become flaky at run-start.

3. **Privacy on shared `target_dir`.** Fingerprint matches only when
   design doc bytes are *identical*, so cross-tenant bleed is bounded to
   "two tenants happened to write the exact same design doc against the
   same harness dir" — which is the same misuse pattern that already
   breaks the §6.5 lockfile model. Document in README under the §H7
   threat-model section: *forge-mcp is single-tenant per `target_dir`;
   multi-tenant deployments use distinct `target_dir`s per tenant.* The
   per-deployment kill switch (`FORGE_LINEAGE_TOP_K=0`) is the right
   answer for operators who can't accept any cross-run text leakage.

4. **The §H3 oscillation loop and §L cross-run learning could fight.**
   §H3 nudges the *next iteration in the same run* to pivot (a per-run
   directive in `write_remediation`); §L tells the *next run's planner*
   to choose a different strategy (a per-design directive in the
   addendum). They are complementary (different time horizons) and the
   §L7 directive is explicit about "different implementation strategy"
   so it doesn't double-pivot in confusing ways. The §L10 engine
   one-liner (`sm.transition("incomplete", reason=ledger.stop_reason)`)
   carries the §H3 break's `stop_reason` into `state.json.reason`,
   which `summarize_prior_run` reads back as `PriorRunSummary.reason`.
   Without this one-liner, the §H3 break signal never crosses the run
   boundary. Pin §L13.3 with a test where a §H3-broken run feeds
   forward and the new planner is given the prior's `reason` so it can
   pivot from the start.

5. **Polluted-context-bleed via the digest.** A malicious or accidentally-
   confidential gap entry in a prior `eval.json` lands in the new
   planner's context. The 200-char title truncation + 500-char
   current/expected/fix truncation + structured shape limit the surface,
   but operators with sensitive workloads should pin
   `FORGE_LINEAGE_TOP_K=0`. Surface in README under §H7 threat-model.
   This is not a bug to fix in v1 — it's a deployment-policy concern.

6. **§R1 resource-surface back-compat.** Adding four entries to
   `_ALLOWED_ARTIFACTS` is structural — a host that read the prior
   `list_resources` output sees new entries when the artifacts exist.
   This is expected and additive. The §R9.1 negative pins (`"run.log"
   not in _ALLOWED_ARTIFACTS`) still hold and gain no new false
   positives.

7. **Cap math under truncation pathology.** A prior run's
   `unresolved_gaps` could contain titles already at the 200-char cap,
   long current/expected/fix prose, dozens of design flaws. The
   per-summary 8 KiB hard cap (`_MAX_PER_SUMMARY_BYTES`, §L-Decision 5)
   is the first defense; the per-prior soft target (10 gaps + 5 flaws
   + 2 KiB verify ≈ 19.5 KiB worst-case, ~3-5 KiB typical) is the
   second; the total digest 32 KiB cap (`_MAX_DIGEST_BYTES`) is the
   third with `prior_attempts-overflow.md` for the dropped tail.
   Pathological prior runs result in overflow files but no read-side
   hang and no starvation of older summaries. Pin §L13.2 with a
   synthetic pathological run AND §L13.3 with a "summary #1 hits
   8 KiB cap, summary #2-#4 still render" test.

8. **Resume re-runs lineage discovery.** A resumed run computes lineage
   *again* (§L4.3), reflecting the harness state at resume-time. A
   prior run that *completed* between the original crash and the
   resume will now be in the resumed run's lineage view even though
   the pre-crash run did not see it. The resumed run skips the
   planner phase, so this is forensic-only; `RunResult.linked_prior_runs`
   honestly reports "this is what the resumed planner would have read."
   `ignore_prior_attempts` on the resume call governs the resumed pass
   identically to a fresh run; the pre-crash flag value is NOT
   preserved (callers control). Pin §L13.3 with a synthetic
   "completed-between-crash-and-resume" prior run AND a "resume with
   `ignore_prior_attempts` flipped" test.

9. **Fingerprint canonicalization choices are forward-incompatible.**
   Changing `fingerprint_design`'s canonicalization (e.g., later
   adopting triage-style `\s+`-collapse) would invalidate every prior
   run's fingerprint and effectively reset all lineage history. This
   is intentional: a canonicalization change IS a semantics change.
   If it ever happens, treat it as a deliberate one-time migration,
   bump the digest to include a `# version: 2` marker in
   `design.fingerprint`, and add a migration note. v1 ships v1.

10. **Lineage compute time bound.** The §L10 lineage block runs OUTSIDE
    `asyncio.wait_for(run_phases, timeout=max_runtime_minutes * 60)`,
    so a pathological harness directory (10,000+ run_id-shaped subdirs,
    slow disk) could burn arbitrary wall-clock before the runtime cap
    is even armed. §H9 `prune_old_runs` is the primary bound — its
    `keep_last` (default 10) caps total candidate count. Deployments
    with `keep_last=0` (no pruning) MUST either set
    `FORGE_LINEAGE_TOP_K=0` (disabling lineage) or accept the
    `O(N_dirs × per-dir IO)` cost. A future enhancement could add a
    self-cap (scan-time deadline, cold-start on overrun); deferred to
    v2 — current default deployments do not hit this risk.

11. **Symlink poisoning into `harness_dir` (security-relevant).** If an
    actor with write access to `<target_dir>/.harness/` places a
    symlink at a `run_id`-shaped name pointing at an attacker-
    controlled directory with a forged `state.json` + `design.fingerprint`
    matching the current run's fingerprint, lineage compute would read
    forged content. `find_lineage_runs` MUST reject `is_symlink()`
    candidates (§L-Inv 5; pinned in §L13.3). The §L6.3 markdown escapes
    reduce but don't eliminate adversarial-input risk — the rejection
    at scan time is the load-bearing defense. The **operation order**
    in `find_lineage_runs` is `is_symlink()` BEFORE `is_dir()` (because
    `is_dir()` follows symlinks and returns True for symlinks-to-dirs;
    short-circuiting on `is_symlink()` first avoids any follow-link
    operation on a suspect entry — pin in §L13.3 with a symlink-to-dir
    fixture). Codex's `workspaceWrite` `writable_roots` are
    `[target_dir, iteration_dir]` (`_codex.py`); the gen CAN write
    under its own `iteration-N/` (which IS inside `.harness/`) but
    NOT into the `harness_dir/` root or sibling `run_id/` dirs, so a
    legitimate gen cannot plant a symlink at a peer `run_id`-shaped
    name. The realistic attack surface is an external actor with shell
    access to the deployment.

12. **TOCTOU between fingerprint read and state read.** `find_lineage_runs`
    reads each candidate's `design.fingerprint` THEN its `state.json` —
    between those reads, a concurrent run on a DIFFERENT `target_dir`
    (Decision 3 permits this) could transition the run that shares
    `harness_dir`. Per-file reads are atomic (§6.5 `os.replace`
    discipline), so each read returns a consistent snapshot, but the
    *pair* is non-atomic. The worst case: a run snapshotted as
    non-terminal at state-read time but terminal at fingerprint-read
    time gets included, and `summarize_prior_run` may then read
    iteration artifacts not yet flushed. The L-Inv 1 best-effort
    discipline absorbs this — any per-field read failure degrades to
    None — so the worst behavioral outcome is a stale-or-empty
    summary, never a fatal error.

13. **Five normative docs now.** Update `CLAUDE.md`'s "Source of truth"
    line to name this companion (fifth normative source) and the
    `§L*` / `L-Inv N` / `L-Decision N` citation namespaces. Base,
    hardening, continuity, and resource-surface sections all still win
    on anything they already specify.
