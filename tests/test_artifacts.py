from __future__ import annotations

import hashlib

from forge_mcp.artifacts import RunLayout, ensure_iteration_dir, init_run_layout


def test_flat_run_level_paths(tmp_path):
    """Design: §10 the layout is flattened to the run root (one plan, no plans/<id>/).
    Implementation: assert plan.json, plan.md, plan_state.json live at the run root.
    Example: lay.plan_json == run_dir / 'plan.json'.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.spec_md == tmp_path / "spec.md"
    assert lay.plan_json == tmp_path / "plan.json"
    assert lay.plan_md == tmp_path / "plan.md"
    assert lay.plan_state_json == tmp_path / "plan_state.json"


def test_iteration_paths_keyed_on_n_only(tmp_path):
    """Design: §10 iteration artifacts are keyed only on n, dir name 'iteration-{n}' at run root.
    Implementation: call the single-arg iteration methods and assert parents/names.
    Example: lay.eval(2).parent == lay.iteration_dir(2); dir name is 'iteration-2'.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.iteration_dir(2) == tmp_path / "iteration-2"
    assert lay.iteration_dir(2).name == "iteration-2"
    for meth, fname in [
        (lay.contract, "contract.md"),
        (lay.summary, "summary.md"),
        (lay.eval, "eval.json"),
        (lay.triage, "triage.json"),
        (lay.gap_fingerprint, "gap_fingerprint.json"),
        (lay.verify_txt, "verify.txt"),
    ]:
        p = meth(2)
        assert p.parent == lay.iteration_dir(2)
        assert p.name == fname


def test_removed_path_methods_are_gone(tmp_path):
    """Design: §10 planset/merge/conflict/git/per-plan-dir paths are removed.
    Implementation: the removed attributes no longer exist on RunLayout.
    Example: hasattr(lay, 'planset_json') is False.
    """
    lay = RunLayout.for_run(tmp_path)
    for attr in (
        "planset_json",
        "plan_dir",
        "plan_state",
        "plan_manifest",
        "plan_merge",
        "conflict_fingerprint",
        "git_violation",
    ):
        assert not hasattr(lay, attr), f"{attr} should be removed"


def test_ensure_iteration_dir_creates_at_run_root(tmp_path):
    """Design: §10 ensure_iteration_dir is keyed only on n and creates the dir on demand.
    Implementation: call with (layout, n) and assert the dir exists at the run root.
    Example: ensure_iteration_dir(lay, 1) creates run_dir/iteration-1.
    """
    lay = RunLayout.for_run(tmp_path)
    path = ensure_iteration_dir(lay, 1)
    assert path == tmp_path / "iteration-1"
    assert path.is_dir()


def test_init_run_layout_freezes_design_and_seeds_spec(tmp_path):
    """Design: §10/§3.1 design.md is immutable; spec.md starts equal to design (UNCHANGED).
    Implementation: init then read files.
    Example: spec.md == design content at init; spec_amendments empty.
    """
    text = "# design\nbody\n"
    fp = hashlib.sha256(text.encode()).hexdigest()
    lay = init_run_layout(tmp_path, text, design_fingerprint=fp)
    assert lay.inputs_design.read_text() == text
    assert lay.spec_md.read_text() == text
    assert lay.design_fingerprint.read_text() == fp
    assert lay.spec_fingerprint.read_text() == fp
    assert lay.spec_amendments.read_text() == ""
