# forge-mcp — Artifact Containment (off-cwd-write cleanup)

**What:** A normative brief that **completes** the §11.4 off-cwd-write
recovery so it removes the orphaned file an agent physically wrote at the
wrong path, instead of only rescuing that file's content. Today recovery
rebuilds the canonical `plan.md` / `contract.md` from the agent's `Write`
tool-call content but leaves the misplaced physical copy on disk; when the
agent targets a path inside `target_dir` but outside `.harness/<run-id>/`,
that copy is neither git-ignored (§13) nor cleaned up — it leaks into the
caller's workspace. Companion to `forge-mcp-design.md` and the nine prior
briefs. This brief's namespaces are `§G*`, `G-Invariant N`, and
`G-Decision N` (`G` for off-cwd-write **Guard**), so code comments can
cite it unambiguously (e.g. `# §G2 prune off-cwd copies`, `# §G-Inv 2
content-match gate`).

**Status:** Design complete. The root cause in §G1 was confirmed at design
time against a real run (`88c16115`) by reading the leaking remediation
session's transcript: the agent called `Write` with
`file_path='/Users/<user>/dev_projects/forge-mcp/contract.md'` (an
absolute path to the repo root), 5720 chars / 5762 UTF-8 bytes —
byte-identical to the recovered canonical `iteration-2/contract.md`. The
§18 schema-pin tests **do not shift for this brief**: `RunForgeInput` and
`RunResult` are untouched (G-Decision 5).

**Audience:** The implementing agent. The seam helper signature in §G2 and
its four deletion gates are themselves normative — every divergence is a
defect.

**Scope (chosen explicitly):** record the design-time defect facts (§G1);
add one pure helper at the §11.4 recovery home and call it from both
recovery seams (§G2); documentation (§G3) and test pins (§G4).
**Explicitly out of scope:** the §11.4 *prevention* layer (the prompt
OVERRIDEs stay verbatim — §G5), the *content-rescue* helper
(`collect_writes_to_basename` is untouched), the generator/Codex write
path (it writes code into `target_dir` legitimately), any orchestrator
target_dir sweep (G-Decision 1), `RunForgeInput` / `RunResult`, and the
committed `uv.lock`.

---

## G0. Thesis and relationship to the base doc

### G0.1 What this brief completes, not supersedes

§11.4 ("Off-cwd-write recovery", `design.md:718`) defines **two layers**
against an agent that ignores its cwd and saves `plan.md` / `contract.md`
elsewhere:

1. **Prevention** — an OVERRIDE block in `planner_system.md` /
   `evaluator_remediation.md` forbidding absolute paths and the skill's
   default save location.
2. **Recovery** — when cwd's `plan.md` / `contract.md` is missing, the
   driver rescues the **last non-empty** `Write` tool_use whose basename
   matches ("the agent's intent is in `input.content` regardless of where
   `file_path` pointed"), rewrites it to the correct path, and returns a
   recovery descriptor surfaced as a `ledger.warnings` entry.

Layer 2 recovers the **content** but is silent on the **physical file**
the agent's `Write` already created off-cwd. This brief adds exactly that
missing step to layer 2. It does not contradict §11.4; it discharges
§11.4's own implicit obligation to §13 ("all run artifacts live under
`<target_dir>/.harness/<run-id>/`"). §11.4 gains one sentence (§G3.2);
everything else in §11.4 stands.

### G0.2 Why the leak is real and intermittent

`<target_dir>/.harness/.gitignore` is `*` (§13), so every path under
`.harness/<run-id>/` is git-ignored. A file the agent writes to
`<target_dir>/contract.md` (or any path inside `target_dir` but outside
`.harness/`) is **outside** that umbrella: it shows up as an untracked
file in the caller's repository and risks being committed. The leak fires
only when (a) recovery fires at all — i.e. the agent disobeyed the layer-1
prompt OVERRIDE — and (b) the off-cwd path it chose lands inside
`target_dir`. Both are probabilistic LLM behaviors, so the leak is
intermittent ("sometimes"), exactly the class the §11.4 safety net exists
to absorb. Removing the orphan removes the last way that disobedience
escapes the harness.

The north star (§1, context anxiety) is unaffected: this is a
post-recovery filesystem tidy with no new agent-facing surface, no handoff
change, and no new failure mode (G-Inv 3 keeps it best-effort).

---

## G1. Defect facts at design time (run `88c16115`)

- The two recovery seams are `drivers/planner.py:72-79` (`write_plan` →
  `plan.md`) and `drivers/evaluator.py:184-191` (`write_remediation` →
  `contract.md`). Both follow the identical shape: build options with
  `cwd=<canonical dir>`, run Claude, and **iff** the canonical file is
  absent, `recovered = collect_writes_to_basename(turn.messages, <name>)`
  then `atomic_write_text(<canonical>, recovered)`.
- `collect_writes_to_basename` (`drivers/_claude.py:250`) matches `Write`
  blocks by **basename only** (`Path(input["file_path"]).name == basename`)
  and returns `input["content"]` — deliberately path-agnostic, which is
  why a misplaced write is recoverable at all and why the recovered bytes
  equal the leaked file's bytes.
- **Evidence (the leaking write):** in run `88c16115`, the iter-1→2
  remediation (session `8d1aa955…`, phase `iter_remediating`) called
  `Write` with `file_path='/Users/<user>/dev_projects/forge-mcp/contract.md'`
  — an absolute path to `target_dir`'s root. The session transcript's
  project-dir encoding (`…-harness-88c16115-iteration-2`) confirms `cwd`
  **was** correctly the iteration dir, so this was **not** a cwd-propagation
  bug: the agent overrode its instructions. `next_dir/contract.md` was
  therefore absent, recovery fired (the run's `RunResult.warnings` surfaced
  `recovered remediation Write tool content for contract.md` — the
  descriptor flows to the result, it is not persisted in the run dir), and
  `iteration-2/contract.md` was rebuilt from the same `input.content` —
  byte-identical (5762 bytes) to the orphan left at the repo root.
- **Both drivers are exposed.** Only `contract.md` leaked this run, but
  `plan.md` recovery is the same pattern with the same gap.
- `RunContext.target_dir: Path | None` (`runcontext.py:21`) supplies the
  containment root for the evaluator seam — its ctx is built by `_ctx(...)`
  (`phases.py:65`), which sets `target_dir=deps.target_dir`. The **planner**
  ctx (§9.1, `phases.py:116`) is built with `run_dir` + Claude config only,
  so `ctx.target_dir` is `None` there; the planner seam sources the
  containment root from `run_dir` instead (§G2.2, G-Decision 6).
  `atomic_write_text` lives at `artifacts.py:16`; `truncate_for_warning` at
  `drivers/_claude.py:215`.

`G-Invariant 1 (artifact containment):` after a planner or evaluator
**recovery**, no file authored by that turn remains under `target_dir`
outside `.harness/<run-id>/`. The canonical copy under `.harness/` is the
sole surviving artifact of the recovered write.

---

## G2. Seam contract (`drivers/_claude.py`, `planner.py`, `evaluator.py`)

### G2.1 New pure helper — `prune_offcwd_write_copies`

Add beside `collect_writes_to_basename` in `drivers/_claude.py` (the §11.4
home; importing it from the two drivers keeps the §5.2 edge `drivers →
drivers._claude`, no new dependency). Signature is normative:

```python
def prune_offcwd_write_copies(
    messages: list[Any],
    basename: str,
    *,
    canonical_path: Path,
    content: str,
    target_dir: Path | None,
) -> list[str]:
    ...
```

**Behavior.** Return `[]` immediately when `target_dir is None`. Otherwise
iterate exactly as the sibling `collect_writes_to_basename` does
(`_claude.py:260-267`) — `for msg in messages:` then `for block in
(getattr(msg, "content", None) or []):`, keeping blocks with
`getattr(block, "name", None) == "Write"`, reading `inp = getattr(block,
"input", None) or {}` and `raw = str(inp.get("file_path") or "")` (the same
defensive accessors: a missing or `None` `file_path` yields `""` and is
skipped, never a `KeyError`/`TypeError`, neither of which the gate-swallow
below would catch). Skip a block whose `Path(raw).name != basename`.

**Resolve once, then gate.** For each surviving block, compute a single
absolute path — `resolved = os.path.abspath(canonical_path.parent / raw)`
when `raw` is relative, or `os.path.abspath(raw)` when absolute. Relative
paths **must** be joined onto `canonical_path.parent` (the driver cwd)
before `abspath` — a bare `os.path.abspath(raw)` would resolve against the
process `os.getcwd()`, the wrong base. `unlink()` `resolved` **only when
every gate holds**, collecting the removed paths (encounter order,
de-duplicated by `resolved`):

1. **Off-canonical.** `resolved != os.path.abspath(canonical_path)` — never
   delete the file recovery just rebuilt. (Abspath both sides so a
   non-normalized `canonical_path` still compares equal.)
2. **Inside `target_dir`.**
   `Path(resolved).is_relative_to(Path(os.path.abspath(target_dir)))`.
   `os.path.abspath` is pure-lexical and collapses `..`, so an escaping
   path (`…/iteration-2/../../../etc/passwd`) is correctly excluded, and
   `is_relative_to` avoids the `str.startswith` sibling-prefix trap
   (`/foo` vs `/foobar`). A path outside `target_dir` (e.g.
   `/tmp/contract.md`) is **left untouched** (G-Decision 3). Gates 1–2 do
   no filesystem I/O, matching §13's deliberate abspath-not-realpath rule
   (G-Decision 4 — `realpath`/`Path.resolve` would collapse symlinks, which
   §13 forbids).
3. **Regular file.** `Path(resolved).is_file()` is true. Note `is_file()`
   **follows symlinks**: a symlink named `<basename>` that points at a
   regular file passes this gate, and the later `unlink()` removes only the
   link, leaving its target — acceptable, the off-cwd link is itself the
   leaked artifact (the content gate below still applies).
4. **Content match.** `Path(resolved).read_text() == content` — the file's
   text, decoded with the same default encoding `atomic_write_text` wrote
   it (text-mode `os.fdopen(fd, "w")`, `artifacts.py:16`), equals the
   content recovery just persisted. This is the gate that makes deletion
   safe — a real, unrelated file that merely shares the basename has
   different content and is never removed (G-Decision 2, G-Inv 2). (The
   comparison is symmetric text, not raw bytes; both sides round-trip
   through the same str + default encoding, so it is exact today without
   pinning an encoding — see G-Decision 2.)

Wrap each candidate's filesystem operations (`is_file` / `read_text` /
`unlink`) so any `OSError` / `ValueError` (the latter covers
`UnicodeDecodeError` on a binary same-named file) is swallowed and
**continues to the next candidate** — the prune never raises and never
aborts the scan (G-Inv 3). The helper does no logging and no I/O beyond
`is_file` / `read_text` / `unlink` on candidates; it is unit-testable on a
`tmp_path` with hand-built message objects (each a stand-in message whose
`.content` is a list of blocks exposing `.name` and `.input`).

`G-Invariant 2 (content-match gate):` `prune_offcwd_write_copies` unlinks
a path **only** when its on-disk text equals the just-recovered `content`,
it resolves inside `target_dir`, and it is not `canonical_path`. No file
failing any gate is ever removed. This is the single guard against deleting
caller-owned files.

`G-Invariant 3 (best-effort; never fails the run):` cleanup is advisory.
Any error while pruning is swallowed; the worst regression is the prior
behavior (orphan remains), never a successful run flipped to `failed`.
Pruning happens only on the recovery branch, so the happy path (canonical
file present) is byte-for-byte unchanged.

### G2.2 Call sites — `planner.write_plan` and `evaluator.write_remediation`

In **both** drivers, in the existing recovery branch, immediately after
`atomic_write_text(<canonical>, recovered)` and before `return`, call the
helper and fold the count into the existing descriptor. The **evaluator**
ctx is built by `_ctx(...)` (`phases.py:65`) and carries `target_dir`, so
it passes `ctx.target_dir`:

```python
atomic_write_text(contract_path, recovered)  # evaluator.py
removed = prune_offcwd_write_copies(
    turn.messages, "contract.md",
    canonical_path=contract_path, content=recovered, target_dir=ctx.target_dir,
)  # §G2 off-cwd cleanup; §13 containment
msg = "recovered remediation Write tool content for contract.md"
if removed:
    msg += f"; pruned {len(removed)} off-cwd copy"
return truncate_for_warning(msg)
```

The **planner** edit is the same shape with `plan_path` / `"plan.md"` and
the `recovered planner Write tool content for plan.md` descriptor — with
one difference: the planner ctx (§9.1, `phases.py:116`) carries **no**
`target_dir`, so `ctx.target_dir` is `None`. The planner sources the
containment root from `run_dir`, which §13 guarantees is
`<target_dir>/.harness/<run-id>/`:

```python
atomic_write_text(plan_path, recovered)  # planner.py
removed = prune_offcwd_write_copies(
    turn.messages, "plan.md",
    canonical_path=plan_path, content=recovered,
    target_dir=ctx.run_dir.parent.parent,  # §13: == target_dir; §9.1 planner ctx has no target_dir
)
msg = "recovered planner Write tool content for plan.md"
if removed:
    msg += f"; pruned {len(removed)} off-cwd copy"
return truncate_for_warning(msg)
```

The descriptor text is **extended, not replaced** — the existing
`recovered …` prefix stays so prior warning-shape expectations hold; the
`; pruned N off-cwd copy` suffix appears only when a copy was actually
removed. Nothing else in either driver changes; no orchestrator wiring and
no `RunContext` shape changes (G-Decision 6).

`G-Decision 6 (planner containment root from `run_dir`, not a threaded
`target_dir`):` the planner's §9.1 `RunContext` omits `target_dir` by
design ("planner predates the loop … run_dir + Claude config only"). Rather
than thread `target_dir` into it — a change to §9.1's field list this brief
does not need — the planner call site derives the root as
`ctx.run_dir.parent.parent`, which the §13 `<target_dir>/.harness/<run-id>/`
layout makes exactly `target_dir`. The helper signature and the evaluator
call site are unchanged; only the planner's `target_dir=` argument differs.
This keeps "supersedes nothing" intact (no base-doc contract is altered).

`G-Decision 1 (clean at the recovery seam, not a global sweep):` the
recovery branch already knows the basename, the canonical path, and the
exact recovered bytes — it is the natural, narrowest owner of cleanup. A
post-phase orchestrator sweep of `target_dir` would be broader, would need
to re-derive that context, and would risk touching unrelated files; it is
explicitly **not** done here (a future defense-in-depth layer, not this
brief).

`G-Decision 2 (content-match over path-trust):` the helper verifies the
file's text equals the recovered `content` rather than trusting "the agent
named this path, so delete it". This protects a legitimate same-named file
and a partial/edited write, and mirrors the safe `cmp … && rm` discipline
a hand-written cleanup would use.

`G-Decision 3 (confine to `target_dir`; leave external strays):` only
copies inside the caller's workspace are the §13 leak. A write the agent
sent outside `target_dir` (e.g. `/tmp`) is not the harness's to delete and
is left alone — narrower blast radius, no surprise deletions on the host.

`G-Decision 4 (abspath, not realpath):` containment uses `os.path.abspath`
to match §13's canonicalization of `target_dir`; symlinked `target_dir`
aliases get distinct roots by §13's intent, so realpath collapsing is
deliberately avoided.

---

## G3. Documentation updates (in scope, same change)

### G3.1 `CLAUDE.md`

Append the tenth-brief paragraph after the §F (generator full-access)
paragraph in the source-of-truth section. It MUST: name this brief
(`docs/specs/forge-mcp-artifact-containment.md`); name the `§G*`,
`G-Invariant N`, `G-Decision N` namespaces with citation examples
(`# §G2 prune off-cwd copies`, `# §G-Inv 2 content-match gate`); state
that it **completes §11.4 layer-2** (recovery now prunes the off-cwd copy)
and enforces §13, superseding nothing; and state the §18 posture — **no
shift**: `RunForgeInput` / `RunResult` are untouched. The
environment-overrides table and commands are unchanged.

### G3.2 `docs/specs/forge-mcp-design.md` — §11.4

Extend §11.4 layer-2 with one sentence so the new mechanism has a
normative home (the repo convention that every behavior cites the design
doc): **append** — after the layer-2 sentence that ends "…surfaces it via
`ledger.warnings` + `status.update(kind="warning")`." — the new sentence
*"Recovery then prunes any content-identical off-cwd copy the agent left
inside `target_dir` (§G), so a disobeyed write cannot leak an artifact past
`.harness/` (§13)."* (Append after the full sentence, not mid-clause after
"descriptor `str`", whose source continues "(capped via
`truncate_for_warning`)".) No other §11.4 text changes.

`G-Invariant 4 (documented completion):` §11.4 and `CLAUDE.md` must record
that recovery now removes the off-cwd copy. Leaving §11.4 describing only
content-rescue (the pre-§G state) is itself a documentation defect.

---

## G4. Test plan & pins (the §18-extension list)

1. **Helper unit pin (`tests/test_drivers_protocols.py`).** Drive
   `prune_offcwd_write_copies` on a `tmp_path` with hand-built message
   objects — each message exposing a `.content` list of blocks, each block
   exposing `.name` and `.input` (mirroring how `collect_writes_to_basename`
   reads `msg.content` → `block.name`/`block.input`) — asserting each gate:
   - **Leak pruned:** an absolute off-cwd copy under `target_dir` whose text
     equals `content` is deleted; the helper returns `[that path]`; the
     canonical file is untouched.
   - **Relative off-cwd resolves:** a `file_path` like `"../../contract.md"`
     resolving (joined onto `canonical_path.parent`, then `abspath`) inside
     `target_dir` is pruned.
   - **Different content survives (G-Inv 2):** a same-basename file with
     text ≠ `content` is **not** deleted.
   - **Outside `target_dir` survives (G-Decision 3):** a path outside
     `target_dir` is **not** deleted.
   - **Canonical survives (gate 1):** `canonical_path` itself is never
     deleted even though its basename and content match.
   - **Malformed block skipped:** a `Write` block whose `input` has no
     `file_path` (or `file_path=None`) is ignored — no `KeyError`/`TypeError`,
     no deletion (mirrors `collect_writes_to_basename`'s defensive accessor).
   - **`target_dir=None` ⇒ `[]`:** no filesystem access, returns empty.
2. **Driver-level pins (planner + evaluator).** Following the existing
   recovery tests, a fake runner returns a turn whose messages carry a
   `Write` block pointing at an absolute off-cwd path that physically
   exists on disk; after `write_plan` / `write_remediation`, assert: the
   canonical file exists, the off-cwd file is **gone**, and the returned
   descriptor contains both the existing `recovered …` prefix and the
   `; pruned 1 off-cwd copy` suffix. For the **planner** case the fake
   `RunContext` must carry a `run_dir` under a temp `target_dir`
   (`<tmp>/.harness/<run-id>/…`) so `run_dir.parent.parent` resolves to that
   temp `target_dir` (the planner ctx has no `target_dir` of its own —
   G-Decision 6); for the **evaluator** case set `ctx.target_dir`
   to the temp root directly.
3. **Regression (happy path untouched).** The existing §11.4 recovery
   tests still pass: when the canonical file is present, neither driver
   invokes the helper and the descriptor is `None`; when recovery fires
   with no off-cwd copy on disk, the descriptor keeps its original text
   (no `; pruned …` suffix).
4. **No schema drift.** `RunForgeInput.model_json_schema()` and
   `RunResult.model_json_schema()` pins are unchanged (G-Decision 5).
5. **Full gate.** `bash scripts/ci.sh` (≡ `make ci`) green: ruff, format,
   pyright, docstring checker (the new `def` carries a Rule 21
   three-section docstring citing `§G2`), fast pytest.

---

## G5. Non-goals (re-asserted)

- **No prompt changes.** The §11.4 layer-1 OVERRIDEs in
  `planner_system.md:9,13` and `evaluator_remediation.md:5` stay verbatim;
  prevention is unchanged, this brief only completes recovery.
- **No change to `collect_writes_to_basename`.** Content-rescue is
  correct; this brief adds a *sibling* cleanup, it does not touch the
  rescue.
- **No generator/Codex change.** The generator writes code into
  `target_dir` by design; off-cwd pruning is scoped to the
  `plan.md`/`contract.md` recovery seams only.
- **No orchestrator `target_dir` sweep.** G-Decision 1.
- **No git operations (Rule 11).** Cleanup is a plain `unlink()` of an
  untracked stray — never `git rm`/`clean`/any mutation; the content-match
  gate keeps it off tracked files regardless.

`G-Decision 5 (no schema change):` `RunForgeInput` / `RunResult` are not
touched; the §18 schema-pin tests do not shift for this brief.

---

## G6. Verification scenarios

The implementing agent MUST run these before declaring done and report
PASS / FAIL for each:

1. `bash scripts/ci.sh` exits 0 (all five checks).
2. The new §G4 helper-unit and driver-level pins pass (run them
   explicitly).
3. **Repro under control:** with a fake remediation turn whose `Write`
   block names an absolute path at a temp `target_dir` root and that file
   physically present, `write_remediation` leaves the temp `target_dir`
   clean apart from `.harness/<run-id>/…` and the canonical
   `iteration-N/contract.md` exists. (The real-CLI e2e cannot
   deterministically force the LLM to disobey the prompt, so this branch
   is pinned by the fake-driven repro, not by `make e2e`; say so if e2e is
   skipped.)
4. `RunForgeInput.model_json_schema()` / `RunResult.model_json_schema()`
   are byte-identical pre/post (no schema drift).

---

## G7. Risks and mitigations

1. **Deleting a caller-owned file.** Mitigated three ways: the
   content-match gate (G-Inv 2 — the file's text must equal the
   just-recovered `content`), `target_dir` confinement (G-Decision 3), and
   the canonical-path skip (gate 1). A tracked project file sharing the
   basename has different content and is never touched.
2. **Symlinked `target_dir` alias.** §13 already declares aliases get
   distinct roots intentionally; the abspath-not-realpath containment
   (G-Decision 4) matches that, so a write under the canonical abspath is
   pruned and an aliased-path write is simply left (no wrong deletion).
3. **Cleanup error mid-prune** (permission, race, file vanished). Swallowed
   per G-Inv 3; degrades to the pre-§G behavior (orphan remains), never
   fails the run.
4. **Multiple off-cwd copies / multiple `Write` blocks.** All matching,
   gated paths are pruned and returned in the descriptor count; nothing is
   left behind silently.

---

End of brief.
