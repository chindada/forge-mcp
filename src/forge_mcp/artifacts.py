from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from forge_mcp.state import durable_append, durable_replace, light_replace


@dataclass(frozen=True)
class RunLayout:
    """Single source of truth for every path under a forge-mcp run directory.

    Design: §11 all file paths for a run are derived from a single root so no
        caller hard-codes relative paths — downstream tasks import this class.
    Implementation: frozen dataclass with one field (root: Path); computed
        properties return concrete Paths; class-method factory for_run wraps
        construction.
    Example: RunLayout.for_run(p).spec_md == p / 'spec.md'.
    """

    root: Path

    @classmethod
    def for_run(cls, run_dir: Path) -> RunLayout:
        """Return a RunLayout rooted at run_dir.

        Design: §11 callers construct layouts via this factory so the field
            name stays an implementation detail.
        Implementation: construct and return a new frozen instance.
        Example: RunLayout.for_run(Path('/runs/abc')).root == Path('/runs/abc').
        """
        return cls(root=run_dir)

    # ── top-level files ────────────────────────────────────────────────────

    @property
    def state_json(self) -> Path:
        """Return path to the run-level state file.

        Design: §11 state.json is the durable orchestrator checkpoint.
        Implementation: root / 'state.json'.
        Example: lay.state_json == run_dir / 'state.json'.
        """
        return self.root / "state.json"

    @property
    def run_log(self) -> Path:
        """Return path to the append-only run log.

        Design: §11 run.log captures structured orchestrator events.
        Implementation: root / 'run.log'.
        Example: lay.run_log == run_dir / 'run.log'.
        """
        return self.root / "run.log"

    @property
    def conflict_fingerprint(self) -> Path:
        """Return path to the conflict-fingerprint JSON.

        Design: §11 tracks git-conflict state to detect re-emergence.
        Implementation: root / 'conflict_fingerprint.json'.
        Example: lay.conflict_fingerprint.name == 'conflict_fingerprint.json'.
        """
        return self.root / "conflict_fingerprint.json"

    # ── inputs/ ───────────────────────────────────────────────────────────

    @property
    def inputs_dir(self) -> Path:
        """Return path to the inputs subdirectory.

        Design: §11 immutable design inputs live under inputs/ to separate them
            from orchestrator-owned artifacts.
        Implementation: root / 'inputs'.
        Example: lay.inputs_dir == run_dir / 'inputs'.
        """
        return self.root / "inputs"

    @property
    def inputs_design(self) -> Path:
        """Return path to the immutable design document.

        Design: §11 design.md is written once at init and never overwritten.
        Implementation: inputs_dir / 'design.md'.
        Example: lay.inputs_design.name == 'design.md'.
        """
        return self.inputs_dir / "design.md"

    @property
    def design_fingerprint(self) -> Path:
        """Return path to the design fingerprint file.

        Design: §11 stores the SHA-256 hex digest of design.md for integrity checks.
        Implementation: inputs_dir / 'design.fingerprint'.
        Example: lay.design_fingerprint.name == 'design.fingerprint'.
        """
        return self.inputs_dir / "design.fingerprint"

    # ── spec files ────────────────────────────────────────────────────────

    @property
    def spec_md(self) -> Path:
        """Return path to the orchestrator-owned spec document.

        Design: §11 spec.md evolves as amendments are accepted; distinct from
            the immutable inputs/design.md.
        Implementation: root / 'spec.md'.
        Example: lay.spec_md == run_dir / 'spec.md'.
        """
        return self.root / "spec.md"

    @property
    def spec_amendments(self) -> Path:
        """Return path to the append-only spec amendments log.

        Design: §11/I3 amendments are never replaced, only appended.
        Implementation: root / 'spec_amendments.md'.
        Example: lay.spec_amendments.name == 'spec_amendments.md'.
        """
        return self.root / "spec_amendments.md"

    @property
    def spec_fingerprint(self) -> Path:
        """Return path to the spec fingerprint file.

        Design: §11 mirrors design_fingerprint for the evolving spec.md.
        Implementation: root / 'spec.fingerprint'.
        Example: lay.spec_fingerprint.name == 'spec.fingerprint'.
        """
        return self.root / "spec.fingerprint"

    # ── planset ───────────────────────────────────────────────────────────

    @property
    def planset_json(self) -> Path:
        """Return path to the planset manifest JSON.

        Design: §11 planset.json records all candidate plan IDs and metadata.
        Implementation: root / 'planset.json'.
        Example: lay.planset_json.name == 'planset.json'.
        """
        return self.root / "planset.json"

    # ── per-plan files ────────────────────────────────────────────────────

    def plan_md(self, plan_id: str) -> Path:
        """Return path to the human-readable plan document for plan_id.

        Design: §11 plan-<id>.md lives at the run root alongside planset.json.
        Implementation: root / f'plan-{plan_id}.md'.
        Example: lay.plan_md('abc').name == 'plan-abc.md'.
        """
        return self.root / f"plan-{plan_id}.md"

    def plan_dir(self, plan_id: str) -> Path:
        """Return path to the per-plan working directory for plan_id.

        Design: §11 plans/<id>/ holds sandbox, state, manifest, and iterations.
        Implementation: root / 'plans' / plan_id.
        Example: lay.plan_dir('abc') == run_dir / 'plans' / 'abc'.
        """
        return self.root / "plans" / plan_id

    def plan_state(self, plan_id: str) -> Path:
        """Return path to the per-plan state.json for plan_id.

        Design: §11 each plan tracks its own checkpoint separately from the run.
        Implementation: plan_dir(plan_id) / 'state.json'.
        Example: lay.plan_state('abc').name == 'state.json'.
        """
        return self.plan_dir(plan_id) / "state.json"

    def plan_manifest(self, plan_id: str) -> Path:
        """Return path to the per-plan sandbox manifest for plan_id.

        Design: §11 manifest.json records files written by the sandbox agent.
        Implementation: plan_dir(plan_id) / 'manifest.json'.
        Example: lay.plan_manifest('abc').name == 'manifest.json'.
        """
        return self.plan_dir(plan_id) / "manifest.json"

    def plan_merge(self, plan_id: str) -> Path:
        """Return path to the per-plan merge descriptor for plan_id.

        Design: §11 merge.json captures merge strategy and result metadata.
        Implementation: plan_dir(plan_id) / 'merge.json'.
        Example: lay.plan_merge('abc').name == 'merge.json'.
        """
        return self.plan_dir(plan_id) / "merge.json"

    # ── per-iteration files ───────────────────────────────────────────────

    def iteration_dir(self, plan_id: str, n: int) -> Path:
        """Return path to iteration directory n for plan_id.

        Design: §11 iterations are numbered subdirs under the plan dir.
        Implementation: plan_dir(plan_id) / f'iteration-{n}'.
        Example: lay.iteration_dir('abc', 3).name == 'iteration-3'.
        """
        return self.plan_dir(plan_id) / f"iteration-{n}"

    def contract(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration contract document.

        Design: §11 contract.md records what the sandbox agent must implement.
        Implementation: iteration_dir(plan_id, n) / 'contract.md'.
        Example: lay.contract('abc', 1).name == 'contract.md'.
        """
        return self.iteration_dir(plan_id, n) / "contract.md"

    def summary(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration summary document.

        Design: §11 summary.md is the sandbox agent's self-reported outcome.
        Implementation: iteration_dir(plan_id, n) / 'summary.md'.
        Example: lay.summary('abc', 1).name == 'summary.md'.
        """
        return self.iteration_dir(plan_id, n) / "summary.md"

    def eval(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration eval JSON.

        Design: §11 eval.json holds the verifier's structured evaluation result.
        Implementation: iteration_dir(plan_id, n) / 'eval.json'.
        Example: lay.eval('abc', 1).name == 'eval.json'.
        """
        return self.iteration_dir(plan_id, n) / "eval.json"

    def triage(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration triage JSON.

        Design: §11 triage.json records the orchestrator's triage decision.
        Implementation: iteration_dir(plan_id, n) / 'triage.json'.
        Example: lay.triage('abc', 1).name == 'triage.json'.
        """
        return self.iteration_dir(plan_id, n) / "triage.json"

    def gap_fingerprint(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration gap-fingerprint JSON.

        Design: §11 gap_fingerprint.json fingerprints open gaps to detect loops.
        Implementation: iteration_dir(plan_id, n) / 'gap_fingerprint.json'.
        Example: lay.gap_fingerprint('abc', 1).name == 'gap_fingerprint.json'.
        """
        return self.iteration_dir(plan_id, n) / "gap_fingerprint.json"

    def verify_txt(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration verify output text file.

        Design: §11 verify.txt captures raw verifier stdout for diagnostics.
        Implementation: iteration_dir(plan_id, n) / 'verify.txt'.
        Example: lay.verify_txt('abc', 1).name == 'verify.txt'.
        """
        return self.iteration_dir(plan_id, n) / "verify.txt"

    def git_violation(self, plan_id: str, n: int) -> Path:
        """Return path to the iteration git-violation text file.

        Design: §11 git-violation.txt records any gitguard violations found.
        Implementation: iteration_dir(plan_id, n) / 'git-violation.txt'.
        Example: lay.git_violation('abc', 1).name == 'git-violation.txt'.
        """
        return self.iteration_dir(plan_id, n) / "git-violation.txt"


def init_run_layout(
    run_dir: Path,
    design_text: str,
    *,
    design_fingerprint: str,
) -> RunLayout:
    """Create the run directory structure and write immutable design + seeded spec.

    Design: §11/§3.1 design.md is frozen at run creation; spec.md starts as a
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


def ensure_iteration_dir(layout: RunLayout, plan_id: str, n: int) -> Path:
    """Create and return the iteration directory for plan_id/n with mode 0o700.

    Design: §11 iteration dirs are created on demand so the orchestrator does
        not pre-allocate all N directories at run start.
    Implementation: os.makedirs with exist_ok=True so repeated calls are safe;
        mode 0o700 restricts access to the owner only.
    Example: ensure_iteration_dir(lay, 'abc', 1) returns lay.iteration_dir('abc', 1)
        and guarantees the directory exists on disk.
    """
    path = layout.iteration_dir(plan_id, n)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path
