# tests/test_sandbox_merge.py
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from forge_mcp.sandbox import (
    Change,
    WriterMap,
    apply_merge,
    capture_manifest,
    detect_changes,
    detect_conflicts,
    resolve_conflict_winner,
)


def _no_dep(a: str, b: str) -> bool:
    """Stand-in is_dependent that declares no plan a dependency of any other.

    Design: §7.4 cross-wave/sibling conflict detection consults an is_dependent
        callback to exempt legitimate build-on-dependency changes; these tests
        model independent plans so the callback always returns False.
    Implementation: ignore both arguments and return False unconditionally.
    Example: _no_dep("pa", "pb") returns False.
    """
    return False


def _changes(root: Path, mutate: Callable[[Path], object]) -> list[Change]:
    """Snapshot *root*, apply *mutate*, and return the detected Changes.

    Design: §7.3/§7.4 a plan's change-set is the diff between the sandbox's base
        manifest and its post-edit state; this helper produces that change-set for
        a single mutation closure.
    Implementation: capture the base manifest, run mutate(root) for its side
        effects, then return detect_changes(root, base).
    Example: _changes(s, lambda r: (r / "a").write_text("x")) returns one add.
    """
    base = capture_manifest(root)
    mutate(root)
    return detect_changes(root, base)


def test_disjoint_files_merge_clean(tmp_path: Path):
    """Design: §7.4 disjoint adds from two plans union cleanly.
    Implementation: two sandboxes add different files; merge applies both.
    Example: target has both files, no conflicts.
    """
    target = tmp_path / "t"
    target.mkdir()
    sa = tmp_path / "a"
    sb = tmp_path / "b"
    sa.mkdir()
    sb.mkdir()
    ca = _changes(sa, lambda r: (r / "a.txt").write_text("x"))
    cb = _changes(sb, lambda r: (r / "b.txt").write_text("y"))
    wm = WriterMap()
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, wm, _no_dep)
    assert conflicts == []
    res = apply_merge(target, {"pa": sa, "pb": sb}, {"pa": ca, "pb": cb}, set())
    assert (target / "a.txt").exists() and (target / "b.txt").exists()
    assert res.conflicts == []


def test_same_path_modify_modify_is_conflict_no_apply(tmp_path: Path):
    """Design: §7.4/I9 two plans modifying one path -> conflict, apply none.
    Implementation: both edit the same file.
    Example: conflict reported; winner chosen by id.
    """
    sa = tmp_path / "a"
    sb = tmp_path / "b"
    for s in (sa, sb):
        s.mkdir()
        (s / "f.txt").write_text("base")
    ca = _changes(sa, lambda r: (r / "f.txt").write_text("A"))
    cb = _changes(sb, lambda r: (r / "f.txt").write_text("B"))
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep)
    assert len(conflicts) == 1 and conflicts[0].path == "f.txt"
    winner, loser = resolve_conflict_winner("pa", "pb", {"pa": [], "pb": []})
    assert (winner, loser) == ("pa", "pb")  # lower id wins on a scope tie


def test_delete_delete_auto_merges(tmp_path: Path):
    """Design: §7.4/I9 delete/delete agrees on absence -> not a conflict.
    Implementation: both delete the same file.
    Example: conflicts empty.
    """
    sa = tmp_path / "a"
    sb = tmp_path / "b"
    for s in (sa, sb):
        s.mkdir()
        (s / "f.txt").write_text("x")
    ca = _changes(sa, lambda r: (r / "f.txt").unlink())
    cb = _changes(sb, lambda r: (r / "f.txt").unlink())
    assert detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep) == []


def test_executable_bit_preserved_through_merge(tmp_path: Path):
    """Design: §7.4 explicit chmod carries the executable bit under umask 0o077.
    Implementation: add an executable file; merge; check mode.
    Example: target file is 0o755.
    """
    target = tmp_path / "t"
    target.mkdir()
    sa = tmp_path / "a"
    sa.mkdir()

    def add_exec(r: Path) -> None:
        """Create an executable script under *r* for the merge to carry forward.

        Design: §7.4 the merge must re-apply the recorded post-edit mode so the
            exec bit survives even when the process umask would otherwise strip it.
        Implementation: write a small shell script then chmod it to 0o755.
        Example: add_exec(sandbox) leaves sandbox/run.sh executable.
        """
        p = r / "run.sh"
        p.write_text("#!/bin/sh\n")
        os.chmod(p, 0o755)

    ca = _changes(sa, add_exec)
    old = os.umask(0o077)
    try:
        apply_merge(target, {"pa": sa}, {"pa": ca}, set())
    finally:
        os.umask(old)
    assert os.stat(target / "run.sh").st_mode & 0o111


def test_cumulative_cross_wave_conflict(tmp_path: Path):
    """Design: §7.4 a later non-dependent plan touching an earlier-written path conflicts.
    Implementation: writer map already has the path from a prior wave.
    Example: conflict detected for the second plan.
    """
    sb = tmp_path / "b"
    sb.mkdir()
    (sb / "f.txt").write_text("base")
    cb = _changes(sb, lambda r: (r / "f.txt").write_text("B"))
    wm = WriterMap()
    wm.record("f.txt", "pa")  # earlier wave wrote it
    conflicts = detect_conflicts({"pb": cb}, wm, _no_dep)
    assert len(conflicts) == 1 and "pa" in conflicts[0].plan_ids


def _add_under_pkg(name: str) -> Callable[[Path], object]:
    """Return a mutate closure that creates `pkg/` and one file under it.

    Design: §7.4 directory add/add is structural; two plans may each add the same
        directory and DIFFERENT independent files beneath it without conflict.
    Implementation: build a closure that makes pkg/ (if needed) then writes
        pkg/<name>; returned for use with the _changes helper.
    Example: _add_under_pkg("a.py")(sandbox) creates sandbox/pkg/a.py.
    """

    def _mutate(r: Path) -> None:
        """Create `pkg/` under *r* and write the captured file name into it.

        Design: §7.4 each plan independently materialises the shared added dir.
        Implementation: mkdir(exist_ok=True) the pkg dir, then write the file.
        Example: _mutate(sandbox) yields sandbox/pkg/<name>.
        """
        (r / "pkg").mkdir(exist_ok=True)
        (r / "pkg" / name).write_text("x")

    return _mutate


def test_directory_add_add_is_not_conflict(tmp_path: Path):
    """Design: §7.4 dir add/add is exempt; independent files under it merge clean.
    Implementation: both plans mkdir pkg/ and each adds a different file.
    Example: no conflicts; merge lands both files.
    """
    target = tmp_path / "t"
    target.mkdir()
    sa = tmp_path / "a"
    sb = tmp_path / "b"
    sa.mkdir()
    sb.mkdir()
    ca = _changes(sa, _add_under_pkg("a.py"))
    cb = _changes(sb, _add_under_pkg("b.py"))
    assert detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep) == []
    apply_merge(target, {"pa": sa, "pb": sb}, {"pa": ca, "pb": cb}, set())
    assert (target / "pkg" / "a.py").exists() and (target / "pkg" / "b.py").exists()


def test_same_path_add_add_is_conflict(tmp_path: Path):
    """Design: §7.4 two plans adding the same leaf path collide (even if identical).
    Implementation: both create f.txt — differing content, then identical content.
    Example: exactly one conflict on f.txt in both variants.
    """
    sa = tmp_path / "a"
    sb = tmp_path / "b"
    sa.mkdir()
    sb.mkdir()
    ca = _changes(sa, lambda r: (r / "f.txt").write_text("A"))
    cb = _changes(sb, lambda r: (r / "f.txt").write_text("B"))
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep)
    assert len(conflicts) == 1 and conflicts[0].path == "f.txt"
    # Identical-content add/add is an intentional conflict too (brief §7.4).
    si = tmp_path / "i"
    sj = tmp_path / "j"
    si.mkdir()
    sj.mkdir()
    ci = _changes(si, lambda r: (r / "f.txt").write_text("same"))
    cj = _changes(sj, lambda r: (r / "f.txt").write_text("same"))
    same = detect_conflicts({"pi": ci, "pj": cj}, WriterMap(), _no_dep)
    assert len(same) == 1 and same[0].path == "f.txt"


def test_deleted_dir_vs_add_underneath_is_conflict(tmp_path: Path):
    """Design: §7.4 a deleted dir vs a non-dependent add under it is a conflict.
    Implementation: A removes pkg/; B adds pkg/new.py; flip is_dependent to exempt.
    Example: conflict names both plans; dependency variant has none.
    """
    import shutil

    sa = tmp_path / "a"
    sa.mkdir()
    (sa / "pkg").mkdir()
    (sa / "pkg" / "a.py").write_text("x")
    ca = _changes(sa, lambda r: shutil.rmtree(r / "pkg"))
    # B's base already has pkg/, so its change-set is ONLY the file add under it
    # (no pkg-dir add) — isolating the directory-prefix relation under test.
    sb = tmp_path / "b"
    sb.mkdir()
    (sb / "pkg").mkdir()
    cb = _changes(sb, lambda r: (r / "pkg" / "new.py").write_text("y"))
    assert [c.path for c in cb] == ["pkg/new.py"]
    conflicts = detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _no_dep)
    assert len(conflicts) == 1 and conflicts[0].path == "pkg/new.py"
    assert "pa" in conflicts[0].plan_ids and "pb" in conflicts[0].plan_ids

    def _b_depends_on_a(later: str, earlier: str) -> bool:
        """Declare that plan pb builds on plan pa (and nothing else).

        Design: §7.4 a legitimate build-on-dependency change under a deleted dir is
            exempt from the directory-prefix conflict.
        Implementation: return True only for the (pb, pa) ordered pair.
        Example: _b_depends_on_a("pb", "pa") is True; ("pa", "pb") is False.
        """
        return (later, earlier) == ("pb", "pa")

    assert detect_conflicts({"pa": ca, "pb": cb}, WriterMap(), _b_depends_on_a) == []


def test_apply_merge_skips_conflicting_path(tmp_path: Path):
    """Design: §7.4 apply_merge skips every path flagged in conflicting_paths.
    Implementation: a plan modifies f.txt but f.txt is marked conflicting.
    Example: target keeps the base content; f.txt not in applied_paths.
    """
    target = tmp_path / "t"
    target.mkdir()
    (target / "f.txt").write_text("base")
    sa = tmp_path / "a"
    sa.mkdir()
    (sa / "f.txt").write_text("base")
    ca = _changes(sa, lambda r: (r / "f.txt").write_text("modified"))
    res = apply_merge(target, {"pa": sa}, {"pa": ca}, {"f.txt"})
    assert (target / "f.txt").read_text() == "base"
    assert "f.txt" not in res.applied_paths


def test_symlink_retarget_no_file_exists_error(tmp_path: Path):
    """Design: §7.4 remove-before-recreate lets a symlink retarget succeed.
    Implementation: target has link->old; a plan retargets it to new.
    Example: apply_merge does not raise; link points to new.
    """
    target = tmp_path / "t"
    target.mkdir()
    os.symlink("old", target / "link")
    sa = tmp_path / "a"
    sa.mkdir()
    os.symlink("old", sa / "link")

    def _retarget(r: Path) -> None:
        """Retarget the sandbox's `link` symlink from old to new.

        Design: §7.4 a symlink modify must remove the stale link before relinking.
        Implementation: unlink the existing link and recreate it pointing at new.
        Example: _retarget(sandbox) leaves sandbox/link -> new.
        """
        (r / "link").unlink()
        os.symlink("new", r / "link")

    ca = _changes(sa, _retarget)
    apply_merge(target, {"pa": sa}, {"pa": ca}, set())
    assert os.readlink(target / "link") == "new"


def test_file_to_dir_flip(tmp_path: Path):
    """Design: §7.4 a file->dir type-flip unlinks the leaf before makedirs.
    Implementation: target has a regular file x; a plan flips it to a directory.
    Example: apply_merge does not raise; x is a directory afterward.
    """
    target = tmp_path / "t"
    target.mkdir()
    (target / "x").write_text("hi")
    sa = tmp_path / "a"
    sa.mkdir()
    (sa / "x").write_text("hi")

    def _flip(r: Path) -> None:
        """Flip the sandbox's regular file `x` into a directory.

        Design: §7.4 a type-flip is detected as a modified change with dir kind.
        Implementation: unlink the file then mkdir a directory at the same path.
        Example: _flip(sandbox) replaces sandbox/x the file with sandbox/x the dir.
        """
        (r / "x").unlink()
        (r / "x").mkdir()

    ca = _changes(sa, _flip)
    apply_merge(target, {"pa": sa}, {"pa": ca}, set())
    assert (target / "x").is_dir()


def test_prune_removes_emptied_dir_keeps_added_dir(tmp_path: Path):
    """Design: §7.4 deepest-first prune drops emptied dirs but keeps added ones.
    Implementation: A deletes dir d/ (emptying it); B intentionally adds kept/.
    Example: after merge d/ is gone but kept/ remains.
    """
    import shutil

    target = tmp_path / "t"
    target.mkdir()
    (target / "d").mkdir()
    (target / "d" / "f.txt").write_text("x")
    sa = tmp_path / "a"
    sa.mkdir()
    (sa / "d").mkdir()
    (sa / "d" / "f.txt").write_text("x")
    ca = _changes(sa, lambda r: shutil.rmtree(r / "d"))
    sb = tmp_path / "b"
    sb.mkdir()
    cb = _changes(sb, lambda r: (r / "kept").mkdir())
    apply_merge(target, {"pa": sa, "pb": sb}, {"pa": ca, "pb": cb}, set())
    assert not (target / "d").exists()
    assert (target / "kept").is_dir()
