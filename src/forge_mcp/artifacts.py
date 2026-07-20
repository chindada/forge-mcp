from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from forge_mcp.state import durable_append, durable_replace, light_replace


@dataclass(frozen=True)
class RunLayout:
    """Single source of truth for every path under a forge-mcp run directory.

    Design: §10 all file paths for a run are derived from a single root so no
        caller hard-codes relative paths. With one plan per run the layout is
        flat: plan.json/plan.md/plan_state.json and iteration-<n>/ live at the
        run root (no plans/<id>/ nesting).
    Implementation: frozen dataclass with one field (root: Path); computed
        properties return concrete Paths; iteration artifacts are keyed only on
        n; class-method factory for_run wraps construction.
    Example: RunLayout.for_run(p).spec_md == p / 'spec.md'.
    """

    root: Path

    @classmethod
    def for_run(cls, run_dir: Path) -> RunLayout:
        """Return a RunLayout rooted at run_dir.

        Design: §10 callers construct layouts via this factory so the field name
            stays an implementation detail.
        Implementation: construct and return a new frozen instance.
        Example: RunLayout.for_run(Path('/runs/abc')).root == Path('/runs/abc').
        """
        return cls(root=run_dir)

    # ── top-level files ─────────────────────────────────────────

    @property
    def state_json(self) -> Path:
        """Return path to the run-level state file.

        Design: §10 state.json is the durable orchestrator checkpoint.
        Implementation: root / 'state.json'.
        Example: lay.state_json == run_dir / 'state.json'.
        """
        return self.root / "state.json"

    @property
    def run_log(self) -> Path:
        """Return path to the append-only run log.

        Design: §10 run.log captures structured orchestrator events.
        Implementation: root / 'run.log'.
        Example: lay.run_log == run_dir / 'run.log'.
        """
        return self.root / "run.log"

    # ── inputs/ ────────────────────────────────────────────

    @property
    def inputs_dir(self) -> Path:
        """Return path to the inputs subdirectory.

        Design: §10 immutable design inputs live under inputs/ to separate them
            from orchestrator-owned artifacts.
        Implementation: root / 'inputs'.
        Example: lay.inputs_dir == run_dir / 'inputs'.
        """
        return self.root / "inputs"

    @property
    def inputs_design(self) -> Path:
        """Return path to the immutable design document.

        Design: §10 design.md is written once at init and never overwritten.
        Implementation: inputs_dir / 'design.md'.
        Example: lay.inputs_design.name == 'design.md'.
        """
        return self.inputs_dir / "design.md"

    @property
    def design_fingerprint(self) -> Path:
        """Return path to the design fingerprint file.

        Design: §10 stores the SHA-256 hex digest of design.md for integrity checks.
        Implementation: inputs_dir / 'design.fingerprint'.
        Example: lay.design_fingerprint.name == 'design.fingerprint'.
        """
        return self.inputs_dir / "design.fingerprint"

    # ── spec files ─────────────────────────────────────────

    @property
    def spec_md(self) -> Path:
        """Return path to the orchestrator-owned spec document.

        Design: §10 spec.md evolves as amendments are accepted; distinct from
            the immutable inputs/design.md.
        Implementation: root / 'spec.md'.
        Example: lay.spec_md == run_dir / 'spec.md'.
        """
        return self.root / "spec.md"

    @property
    def spec_amendments(self) -> Path:
        """Return path to the append-only spec amendments log.

        Design: §10/I3 amendments are never replaced, only appended.
        Implementation: root / 'spec_amendments.md'.
        Example: lay.spec_amendments.name == 'spec_amendments.md'.
        """
        return self.root / "spec_amendments.md"

    @property
    def spec_fingerprint(self) -> Path:
        """Return path to the spec fingerprint file.

        Design: §10 mirrors design_fingerprint for the evolving spec.md.
        Implementation: root / 'spec.fingerprint'.
        Example: lay.spec_fingerprint.name == 'spec.fingerprint'.
        """
        return self.root / "spec.fingerprint"

    # ── plan (single, run-level) ──────────────────────────────

    @property
    def plan_json(self) -> Path:
        """Return path to the structured Plan JSON.

        Design: §10 plan.json holds the single structured Plan (was planset.json).
        Implementation: root / 'plan.json'.
        Example: lay.plan_json.name == 'plan.json'.
        """
        return self.root / "plan.json"

    @property
    def plan_md(self) -> Path:
        """Return path to the human-readable plan body document.

        Design: §10 plan.md holds the single plan's body (was plan-<id>.md);
            with one plan there is no id to disambiguate.
        Implementation: root / 'plan.md'.
        Example: lay.plan_md.name == 'plan.md'.
        """
        return self.root / "plan.md"

    @property
    def plan_state_json(self) -> Path:
        """Return path to the single run-level plan state file.

        Design: §10 plan_state.json is the plan-level PlanState checkpoint (was
            plans/<id>/state.json), now flattened to the run root.
        Implementation: root / 'plan_state.json'.
        Example: lay.plan_state_json.name == 'plan_state.json'.
        """
        return self.root / "plan_state.json"

    # ── per-iteration files (keyed on n) ──────────────────────────

    def iteration_dir(self, n: int) -> Path:
        """Return path to iteration directory n at the run root.

        Design: §10 iterations are numbered subdirs directly under the run root
            (no plans/<id>/ nesting); the dir name keeps the 'iteration-<n>' form.
        Implementation: root / f'iteration-{n}'.
        Example: lay.iteration_dir(3).name == 'iteration-3'.
        """
        return self.root / f"iteration-{n}"

    def contract(self, n: int) -> Path:
        """Return path to the iteration contract document.

        Design: §10 contract.md records what the Generator must implement.
        Implementation: iteration_dir(n) / 'contract.md'.
        Example: lay.contract(1).name == 'contract.md'.
        """
        return self.iteration_dir(n) / "contract.md"

    def summary(self, n: int) -> Path:
        """Return path to the iteration summary document.

        Design: §10 summary.md is the Generator's self-reported outcome.
        Implementation: iteration_dir(n) / 'summary.md'.
        Example: lay.summary(1).name == 'summary.md'.
        """
        return self.iteration_dir(n) / "summary.md"

    def eval(self, n: int) -> Path:
        """Return path to the iteration eval JSON.

        Design: §10 eval.json holds the Evaluator's structured result.
        Implementation: iteration_dir(n) / 'eval.json'.
        Example: lay.eval(1).name == 'eval.json'.
        """
        return self.iteration_dir(n) / "eval.json"

    def triage(self, n: int) -> Path:
        """Return path to the iteration triage JSON.

        Design: §10 triage.json records the orchestrator's triage decision.
        Implementation: iteration_dir(n) / 'triage.json'.
        Example: lay.triage(1).name == 'triage.json'.
        """
        return self.iteration_dir(n) / "triage.json"

    def gap_fingerprint(self, n: int) -> Path:
        """Return path to the iteration gap-fingerprint JSON.

        Design: §10 gap_fingerprint.json fingerprints open gaps to detect loops.
        Implementation: iteration_dir(n) / 'gap_fingerprint.json'.
        Example: lay.gap_fingerprint(1).name == 'gap_fingerprint.json'.
        """
        return self.iteration_dir(n) / "gap_fingerprint.json"

    def verify_txt(self, n: int) -> Path:
        """Return path to the iteration verify output text file.

        Design: §10 verify.txt captures raw verification stdout for diagnostics.
        Implementation: iteration_dir(n) / 'verify.txt'.
        Example: lay.verify_txt(1).name == 'verify.txt'.
        """
        return self.iteration_dir(n) / "verify.txt"


def init_run_layout(
    run_dir: Path,
    design_text: str,
    *,
    design_fingerprint: str,
) -> RunLayout:
    """Create the run directory structure and write immutable design + seeded spec.

    Design: §10/§3.1 design.md is frozen at run creation; spec.md starts as a
        copy of design and may evolve; spec_amendments.md is created empty as an
        append-only log.
    Implementation: make inputs/ with mode 0o700; write design.md and spec.md
        via durable_replace (crash-safe); write fingerprints via light_replace;
        create empty spec_amendments.md via durable_append (no-op append of '').
    Example: init_run_layout(p, 'hi', design_fingerprint='abc') leaves
        p/inputs/design.md == 'hi' and p/spec.md == 'hi'.
    """
    lay = RunLayout.for_run(run_dir)

    os.makedirs(lay.inputs_dir, mode=0o700, exist_ok=True)

    # Immutable design inputs
    durable_replace(lay.inputs_design, design_text)
    light_replace(lay.design_fingerprint, design_fingerprint)

    # Orchestrator-owned spec (seeded from design at init)
    durable_replace(lay.spec_md, design_text)
    light_replace(lay.spec_fingerprint, design_fingerprint)

    # Empty append-only amendments log
    durable_append(lay.spec_amendments, "")

    return lay


def ensure_iteration_dir(layout: RunLayout, n: int) -> Path:
    """Create and return the iteration directory for n with mode 0o700.

    Design: §10 iteration dirs are created on demand so the orchestrator does
        not pre-allocate all directories at run start; keyed only on n now that
        there is a single plan.
    Implementation: os.makedirs with exist_ok=True so repeated calls are safe;
        mode 0o700 restricts access to the owner only.
    Example: ensure_iteration_dir(lay, 1) returns lay.iteration_dir(1) and
        guarantees the directory exists on disk.
    """
    path = layout.iteration_dir(n)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path
