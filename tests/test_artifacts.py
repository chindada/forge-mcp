from __future__ import annotations

import hashlib

from forge_mcp.artifacts import RunLayout, init_run_layout


def test_layout_paths(tmp_path):
    """Design: §11 the layout exposes every run-dir path.
    Implementation: a few representative paths resolve under run_dir.
    Example: iteration file under plans/<id>/iteration-N/.
    """
    lay = RunLayout.for_run(tmp_path)
    assert lay.spec_md == tmp_path / "spec.md"
    assert lay.iteration_dir("p1", 2).name == "iteration-2"
    assert lay.eval("p1", 2).parent == lay.iteration_dir("p1", 2)


def test_init_run_layout_freezes_design_and_seeds_spec(tmp_path):
    """Design: §3.1/§11 design.md is immutable; spec.md starts equal to design.
    Implementation: init then read files.
    Example: spec.md == design.md content at init; spec_amendments empty.
    """
    text = "# design\nbody\n"
    fp = hashlib.sha256(text.encode()).hexdigest()
    lay = init_run_layout(tmp_path, text, design_fingerprint=fp)
    assert lay.inputs_design.read_text() == text
    assert lay.spec_md.read_text() == text
    assert lay.design_fingerprint.read_text() == fp
    assert lay.spec_fingerprint.read_text() == fp
    assert lay.spec_amendments.read_text() == ""
