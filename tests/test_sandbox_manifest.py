# tests/test_sandbox_manifest.py
from __future__ import annotations

import os
from pathlib import Path

from forge_mcp.sandbox import capture_manifest, copy_sandbox, detect_changes


def test_copy_excludes_git_and_harness_keeps_symlinks(tmp_path: Path):
    """Design: §7.2 copy excludes .git/.harness, copies symlinks as links.
    Implementation: build a tree, copy, assert exclusions and link kind.
    Example: dst has no .git; link stays a link.
    """
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".harness").mkdir()
    (src / "pkg").mkdir()
    (src / "pkg" / "a.txt").write_text("hello")
    os.symlink("a.txt", src / "pkg" / "link")
    dst = tmp_path / "dst"
    copy_sandbox(src, dst)
    assert not (dst / ".git").exists() and not (dst / ".harness").exists()
    assert (dst / "pkg" / "a.txt").read_text() == "hello"
    assert (dst / "pkg" / "link").is_symlink()


def test_manifest_kinds_and_digests(tmp_path: Path):
    """Design: §7.2 file/symlink/dir manifest entries.
    Implementation: capture and inspect kinds.
    Example: file has digest+mode, symlink no mode, dir no digest.
    """
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_text("x")
    os.symlink("f", tmp_path / "d" / "s")
    m = capture_manifest(tmp_path)
    assert m["d"].kind == "dir" and m["d"].digest is None and m["d"].mode is not None
    assert m["d/f"].kind == "file" and m["d/f"].digest and m["d/f"].mode is not None
    assert m["d/s"].kind == "symlink" and m["d/s"].digest and m["d/s"].mode is None


def test_detect_add_modify_delete_mode_typeflip(tmp_path: Path):
    """Design: §7.3 added/deleted/modified/mode-changed + type-flip→modified.
    Implementation: snapshot, mutate, detect.
    Example: each kind present.
    """
    (tmp_path / "keep").write_text("k")
    (tmp_path / "mod").write_text("1")
    (tmp_path / "gone").write_text("g")
    (tmp_path / "exec").write_text("#!/bin/sh\n")
    base = capture_manifest(tmp_path)
    (tmp_path / "new").write_text("n")  # added
    (tmp_path / "mod").write_text("2")  # modified
    (tmp_path / "gone").unlink()  # deleted
    os.chmod(tmp_path / "exec", 0o755)  # mode-changed
    (tmp_path / "keep").unlink()  # type-flip file->dir
    (tmp_path / "keep").mkdir()
    detected = detect_changes(tmp_path, base)
    changes = {(c.path, c.kind) for c in detected}
    assert ("new", "added") in changes
    assert ("mod", "modified") in changes
    assert ("gone", "deleted") in changes
    assert ("exec", "mode-changed") in changes
    assert ("keep", "modified") in changes  # type-flip treated as modified
    # type-flip carries the NEW kind (consumed by Task 13 merge engine)
    assert any(
        c.path == "keep" and c.kind == "modified" and c.entry_kind == "dir" for c in detected
    )


def test_symlink_to_directory_is_captured_and_detected(tmp_path: Path):
    """Design: §7.2/§7.3 a symlink whose target is a directory is a symlink entry,
        not recursed, and participates in add/modify/retarget/delete detection.
    Implementation: os.walk lists symlink-dirs in dirnames; capture must record
        them there. Build linkdir->realdir, snapshot, retarget, detect.
    Example: linkdir captured as symlink; retarget reported as modified.
    """
    (tmp_path / "realdir").mkdir()
    (tmp_path / "realdir" / "a.txt").write_text("a")
    (tmp_path / "otherdir").mkdir()
    os.symlink("realdir", tmp_path / "linkdir")  # symlink whose target is a dir

    m = capture_manifest(tmp_path)
    # The symlink-to-dir is a first-class symlink entry, never recursed as a dir.
    assert m["linkdir"].kind == "symlink" and m["linkdir"].digest and m["linkdir"].mode is None
    assert "linkdir/a.txt" not in m  # contents never walked through the link

    base = capture_manifest(tmp_path)
    (tmp_path / "linkdir").unlink()
    os.symlink("otherdir", tmp_path / "linkdir")  # retarget
    changes = {(c.path, c.kind, c.entry_kind) for c in detect_changes(tmp_path, base)}
    assert ("linkdir", "modified", "symlink") in changes

    # Deletion of a symlink-to-dir is detected too.
    base2 = capture_manifest(tmp_path)
    (tmp_path / "linkdir").unlink()
    assert ("linkdir", "deleted") in {(c.path, c.kind) for c in detect_changes(tmp_path, base2)}


def test_dangling_symlink_does_not_raise(tmp_path: Path):
    """Design: §7.2 symlink digest hashes the target string, never dereferenced.
    Implementation: a dangling link captures cleanly.
    Example: capture succeeds; retarget detected as modified.
    """
    os.symlink("nowhere", tmp_path / "dl")
    base = capture_manifest(tmp_path)
    assert base["dl"].kind == "symlink"
    (tmp_path / "dl").unlink()
    os.symlink("elsewhere", tmp_path / "dl")
    assert ("dl", "modified") in {(c.path, c.kind) for c in detect_changes(tmp_path, base)}
