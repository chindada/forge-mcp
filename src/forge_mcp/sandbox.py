"""Sandbox isolation: copy, manifest capture, and change detection (§7.2/§7.3)."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ChangeKind = Literal["added", "deleted", "modified", "mode-changed"]


@dataclass(frozen=True)
class Entry:
    """One filesystem entry in a manifest snapshot.

    Design: §7.2 defines three kinds of entries — file, symlink, dir — each
        carrying the digest and mode fields that are meaningful for that kind.
        Symlinks carry no mode; dirs carry no digest; files carry both.
    Implementation: frozen dataclass so it can be used as a dict value safely;
        kind is a Literal union; digest and mode are Optional because they are
        absent for certain kinds.
    Example: Entry(kind="file", digest="abc123", mode=0o644).
    """

    kind: Literal["file", "symlink", "dir"]
    digest: str | None
    mode: int | None


@dataclass(frozen=True)
class Change:
    """One detected filesystem change between a base manifest and the current state.

    Design: §7.3 tracks added/deleted/modified/mode-changed changes with the
        path, the kind of change, the entry kind (new kind for type-flips), and
        the post-edit mode for file/dir rows.
    Implementation: frozen dataclass; mode is None for symlink entries and for
        deleted rows (the entry no longer exists to stat).
    Example: Change(path="src/main.py", kind="modified", entry_kind="file", mode=0o644).
    """

    path: str
    kind: ChangeKind
    entry_kind: Literal["file", "symlink", "dir"]
    mode: int | None


Manifest = dict[str, Entry]

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _digest_file(p: Path) -> str:
    """Return the hex SHA-256 digest of a regular file's byte content.

    Design: §7.2 file entries use byte-exact content comparison so binary and
        text files are handled identically and no encoding ambiguity arises.
    Implementation: read the entire file as bytes and hash with hashlib.sha256;
        never open in text mode.
    Example: _digest_file(Path("/etc/hostname")) returns a 64-char hex string.
    """
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _digest_link(p: Path) -> str:
    """Return the hex SHA-256 digest of a symlink's target string (never dereferenced).

    Design: §7.2 symlink entries hash the raw target string so dangling links
        are handled safely without any filesystem access to the target path.
    Implementation: os.readlink returns the target string; encode to bytes before
        hashing so the interface is consistent with _digest_file.
    Example: for a link pointing to "foo", returns sha256("foo".encode()).hexdigest().
    """
    return hashlib.sha256(os.readlink(p).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def copy_sandbox(src: Path, dst: Path) -> None:
    """Recursively copy *src* to *dst*, excluding top-level `.git` and `.harness`.

    Design: §7.2 the sandbox copy must exclude VCS and harness metadata directories
        at the source root so the agent sees only project files; symlinks must be
        copied as links (never dereferenced) to preserve relative path semantics.
    Implementation: shutil.copytree with symlinks=True and an ignore function that
        drops .git and .harness only when the directory being inspected is src itself;
        dirs_exist_ok=False (dst must not pre-exist) to avoid partial merges.
    Example: copy_sandbox(Path("/proj"), Path("/sandbox")) excludes /proj/.git.
    """

    def _ignore(directory: str, names: list[str]) -> list[str]:
        """Return names to exclude from the copytree walk for *directory*.

        Design: §7.2 only .git and .harness at the source root are excluded;
            subdirectories inside the project are never filtered so the full tree
            is copied.
        Implementation: compare the current directory against src; return a list
            of names matching the excluded set only at that level, empty otherwise.
        Example: _ignore(str(src), [".git", "pkg"]) returns [".git"].
        """
        if Path(directory) == src:
            return [n for n in names if n in {".git", ".harness"}]
        return []

    shutil.copytree(src, dst, symlinks=True, ignore=_ignore)


def capture_manifest(root: Path) -> Manifest:
    """Return a manifest mapping relative POSIX paths to Entry objects for *root*.

    Design: §7.2 every file, symlink, and directory under root is catalogued so
        that detect_changes can perform a precise diff; symlinks to directories
        must be classified as symlink entries, not recursed as directories.
    Implementation: os.walk with followlinks=False so symlinked dirs are not
        recursed; for each directory yielded by walk, check os.path.islink first
        to classify symlinked dirs correctly; similarly check each file name in
        the dirnames list that os.walk may have pruned but we catch via the
        per-dir loop.  The root itself is not included in the manifest.
    Example: capture_manifest(Path("/sandbox")) returns {"pkg/a.py": Entry(...)}.
    """
    manifest: Manifest = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)

        # Record the directory itself (skip the root)
        if dp != root:
            rel = dp.relative_to(root).as_posix()
            if os.path.islink(dp):
                # A symlink whose target happens to be a dir — classify as symlink
                manifest[rel] = Entry(
                    kind="symlink",
                    digest=_digest_link(dp),
                    mode=None,
                )
            else:
                st = dp.lstat()
                manifest[rel] = Entry(
                    kind="dir",
                    digest=None,
                    mode=stat.S_IMODE(st.st_mode),
                )

        # Prune symlink-dirs from dirnames so os.walk won't descend into them;
        # we already recorded them above and don't want to walk their contents.
        dirnames[:] = [d for d in dirnames if not os.path.islink(dp / d)]

        # Record each file/symlink in this directory
        for name in filenames:
            fp = dp / name
            rel = fp.relative_to(root).as_posix()
            if os.path.islink(fp):
                manifest[rel] = Entry(
                    kind="symlink",
                    digest=_digest_link(fp),
                    mode=None,
                )
            else:
                st = fp.stat()
                manifest[rel] = Entry(
                    kind="file",
                    digest=_digest_file(fp),
                    mode=stat.S_IMODE(st.st_mode),
                )

    return manifest


def detect_changes(root: Path, base: Manifest) -> list[Change]:
    """Return the list of Changes between *base* manifest and the current state of *root*.

    Design: §7.3 compares the freshly-captured manifest against the base snapshot
        to detect added, deleted, modified (content or type-flip), and mode-changed
        entries; symlinks are compared by hashed target string, never dereferenced.
    Implementation: capture the current manifest, then iterate the union of all
        paths; classify each as added/deleted/modified/mode-changed per §7.3 rules;
        type-flips (same path, different kind) are reported as modified with the new
        entry_kind; mode comparison applies only to files and dirs, never symlinks.
    Example: detect_changes(sandbox, base) returns [Change(path="x", kind="added", ...)].
    """
    current = capture_manifest(root)
    base_paths = set(base.keys())
    current_paths = set(current.keys())
    changes: list[Change] = []

    for path in sorted(base_paths | current_paths):
        in_base = path in base
        in_current = path in current

        if in_current and not in_base:
            # Added
            entry = current[path]
            mode = entry.mode if entry.kind != "symlink" else None
            changes.append(Change(path=path, kind="added", entry_kind=entry.kind, mode=mode))

        elif in_base and not in_current:
            # Deleted — entry no longer exists; use base kind, mode=None
            changes.append(Change(path=path, kind="deleted", entry_kind=base[path].kind, mode=None))

        else:
            # Both present
            b = base[path]
            c = current[path]

            if b.kind != c.kind:
                # Type-flip → modified; record new (current) kind
                mode = c.mode if c.kind != "symlink" else None
                changes.append(Change(path=path, kind="modified", entry_kind=c.kind, mode=mode))
            elif b.digest != c.digest:
                # Content/target changed
                mode = c.mode if c.kind != "symlink" else None
                changes.append(Change(path=path, kind="modified", entry_kind=c.kind, mode=mode))
            elif c.kind != "symlink" and b.mode != c.mode:
                # Mode changed (files and dirs only)
                changes.append(
                    Change(path=path, kind="mode-changed", entry_kind=c.kind, mode=c.mode)
                )

    return changes


# ---------------------------------------------------------------------------
# Merge engine (§7.4)
# ---------------------------------------------------------------------------


@dataclass
class Conflict:
    """One detected merge conflict over a single path.

    Design: §6.7/§7.4 a conflict records the contested path, the plans whose
        change-sets collided on it, and the change kinds involved so the
        scheduler can resolve a winner and emit a stable fingerprint.
    Implementation: plain (non-frozen) dataclass with list fields; the
        fingerprint sorts plan ids and kinds so it is order-independent.
    Example: Conflict(path="f.txt", plan_ids=["pa", "pb"], kinds=["modified"]).
    """

    path: str
    plan_ids: list[str]
    kinds: list[str]

    def fingerprint(self) -> str:
        """Return a stable, order-independent identity string for this conflict.

        Design: §6.7 conflicts are deduplicated and persisted by a fingerprint
            that must not depend on the order plans or kinds were discovered.
        Implementation: join the path with sorted plan ids and sorted kinds using
            the `path|ids|kinds` shape.
        Example: fingerprint() -> "f.txt|pa,pb|modified".
        """
        return f"{self.path}|{','.join(sorted(self.plan_ids))}|{','.join(sorted(self.kinds))}"


@dataclass
class MergeResult:
    """Outcome of applying a wave's non-conflicting changes to the target tree.

    Design: §7.4 apply_merge reports which paths it actually wrote and which it
        skipped because they were flagged as conflicts upstream.
    Implementation: plain dataclass; applied_paths is in apply order, conflicts
        carries any Conflict objects threaded through for the caller's record.
    Example: MergeResult(applied_paths=["a.txt"], conflicts=[]).
    """

    applied_paths: list[str]
    conflicts: list[Conflict] = field(default_factory=list)


class WriterMap:
    """Cumulative record of the first plan to write each path across all waves.

    Design: §7.4 cross-wave conflict detection needs to know which earlier plan
        already owns a path so a later, non-dependent plan touching it can be
        flagged; ownership is first-writer-wins and never overwritten.
    Implementation: a private dict from path to plan id; record() is a no-op when
        the path is already owned, writer_of() returns the owner or None.
    Example: wm.record("f", "pa"); wm.writer_of("f") == "pa".
    """

    def __init__(self) -> None:
        """Initialize an empty writer map.

        Design: §7.4 a fresh merge run starts with no recorded writers.
        Implementation: allocate the backing path->plan_id dict.
        Example: WriterMap().writer_of("x") is None.
        """
        self._writers: dict[str, str] = {}

    def record(self, path: str, plan_id: str) -> None:
        """Record *plan_id* as the writer of *path* unless already owned.

        Design: §7.4 ownership is first-writer-wins so the earliest wave keeps
            credit for a path even if later waves are (legitimately) allowed to
            build on it.
        Implementation: insert only when the path is not already present.
        Example: record("f", "pa") then record("f", "pb") leaves owner "pa".
        """
        self._writers.setdefault(path, plan_id)

    def writer_of(self, path: str) -> str | None:
        """Return the plan id that first wrote *path*, or None if unwritten.

        Design: §7.4 detect_conflicts queries prior ownership to find cross-wave
            collisions.
        Implementation: dict lookup with a None default.
        Example: writer_of("unwritten") is None.
        """
        return self._writers.get(path)


def _is_leaf_change(change: Change) -> bool:
    """Return True when a change concerns a file/symlink leaf (not a directory).

    Design: §7.4 add/add and most same-wave content conflicts apply only to leaf
        paths; directories carry no digest and are merged structurally.
    Implementation: a change is a leaf when its entry_kind is file or symlink; a
        deletion's entry_kind reflects the kind that was removed.
    Example: _is_leaf_change(Change("f", "added", "file", 0o644)) is True.
    """
    return change.entry_kind in ("file", "symlink")


def detect_conflicts(
    changes_by_plan: dict[str, list[Change]],
    writer_map: WriterMap,
    is_dependent: Callable[[str, str], bool],
) -> list[Conflict]:
    """Return the conflicts in a wave's change-sets, honouring cumulative writers.

    Design: §7.4 a wave merges several plans at once. Two sibling plans touching
        the same leaf path collide (modify/modify, modify/delete, delete/add, and
        add/add) unless they merely agree on absence (delete/delete, which auto-
        merges) or the path is a directory (no content to disagree on). A later
        plan that touches a path an earlier merged wave already wrote also
        collides, unless it legitimately builds on a transitive dependency. A
        directory deleted by one plan conflicts with any add/modify under that
        directory made by a non-dependent plan.
    Implementation: group changes by path to find same-wave siblings; a leaf path
        touched by >1 plan is a conflict unless every touching change is a
        deletion. For each plan/path, consult writer_map for a different earlier
        owner and flag unless is_dependent(plan, owner). Finally, for every
        directory a plan deletes, scan all other plans for add/modify changes
        nested under that directory by a non-dependent plan. Conflicts are keyed
        by path so each contested path yields exactly one Conflict.
    Example: detect_conflicts({"pa": [mod_f], "pb": [mod_f]}, wm, no_dep) -> one.
    """
    # path -> {plan_id -> Change}
    by_path: dict[str, dict[str, Change]] = {}
    for plan_id, changes in changes_by_plan.items():
        for change in changes:
            by_path.setdefault(change.path, {})[plan_id] = change

    # Accumulate per-path conflict participants so each path yields one Conflict.
    conflict_plans: dict[str, set[str]] = {}
    conflict_kinds: dict[str, set[str]] = {}

    def _flag(path: str, plan_ids: set[str], kinds: set[str]) -> None:
        """Merge a set of conflicting plans/kinds into the per-path accumulators.

        Design: §7.4 a single path may collide for several reasons (siblings and
            cross-wave); all contributors fold into one Conflict for that path.
        Implementation: union the incoming plan ids and kinds into the path's
            entries in the accumulator dicts.
        Example: _flag("f", {"pa", "pb"}, {"modified"}) seeds the f.txt conflict.
        """
        conflict_plans.setdefault(path, set()).update(plan_ids)
        conflict_kinds.setdefault(path, set()).update(kinds)

    # --- Same-wave sibling collisions on a single path ---
    for path, plan_changes in by_path.items():
        if len(plan_changes) < 2:
            continue
        kinds = {c.kind for c in plan_changes.values()}
        # delete/delete (only deletions across all touching plans) auto-merges.
        if kinds == {"deleted"}:
            continue
        # Directory add/add is structural, not a content conflict.
        if all(not _is_leaf_change(c) for c in plan_changes.values()) and kinds == {"added"}:
            continue
        _flag(path, set(plan_changes), {c.kind for c in plan_changes.values()})

    # --- Cross-wave cumulative collisions against earlier writers ---
    for plan_id, changes in changes_by_plan.items():
        for change in changes:
            owner = writer_map.writer_of(change.path)
            if owner is None or owner == plan_id:
                continue
            if is_dependent(plan_id, owner):
                continue
            _flag(change.path, {plan_id, owner}, {change.kind})

    # --- Directory-prefix relations: deleted dir vs add/modify underneath ---
    for plan_id, changes in changes_by_plan.items():
        for change in changes:
            if not (change.kind == "deleted" and change.entry_kind == "dir"):
                continue
            prefix = change.path + "/"
            for other_id, other_changes in changes_by_plan.items():
                if other_id == plan_id:
                    continue
                if is_dependent(other_id, plan_id):
                    continue
                for other in other_changes:
                    if other.kind in ("added", "modified") and other.path.startswith(prefix):
                        _flag(other.path, {plan_id, other_id}, {change.kind, other.kind})

    return [
        Conflict(
            path=path,
            plan_ids=sorted(conflict_plans[path]),
            kinds=sorted(conflict_kinds[path]),
        )
        for path in sorted(conflict_plans)
    ]


def _remove_node(dst: str) -> None:
    """Remove whatever filesystem node currently lives at *dst*, if any.

    Design: §7.4 the remove-before-recreate rule must clear an existing node of a
        differing kind (file/symlink/dir) before the new node is written so a
        type-flip does not collide with the stale entry.
    Implementation: when dst exists, rmtree directories and unlink files/symlinks;
        os.path.lexists is used so a symlink (even dangling) is detected.
    Example: _remove_node("/t/x") deletes a dir, file, or link at that path.
    """
    if not os.path.lexists(dst):
        return
    if os.path.isdir(dst) and not os.path.islink(dst):
        shutil.rmtree(dst)
    else:
        os.unlink(dst)


def apply_merge(
    target: Path,
    sandboxes: dict[str, Path],
    changes_by_plan: dict[str, list[Change]],
    conflicting_paths: set[str],
) -> MergeResult:
    """Apply every non-conflicting change from a wave's plans onto *target*.

    Design: §7.4 changes are applied in a fixed kind order so the tree is always
        consistent: added directories (parents first) before the leaves inside
        them, then file/symlink adds and modifies, then standalone mode-changes,
        then deletions, then a deepest-first prune of directories emptied by the
        merge. Paths flagged as conflicts are skipped entirely. An explicit chmod
        re-applies each change's recorded post-edit mode so the exec bit survives
        a restrictive umask, and the remove-before-recreate rule clears stale
        nodes before symlink retargets and type-flips.
    Implementation: bucket the surviving changes by kind. Create added dirs with
        os.makedirs(exist_ok=True) (unlinking a leaf first on a file/symlink->dir
        flip) then chmod. Copy file/symlink leaves from the owning sandbox,
        truncating regular-file modifies in place but removing-before-recreating
        symlinks and any cross-kind flip; chmod files to the recorded mode.
        Apply mode-changes, then missing-path-tolerant deletes, then prune empty
        directories deepest-first while preserving any intentionally-added dir.
    Example: apply_merge(t, {"pa": sa}, {"pa": [add_file]}, set()) writes the file.
    """
    applied: list[str] = []

    # Bucket surviving (non-conflicting) changes, remembering the owning sandbox.
    added_dirs: list[tuple[str, Change]] = []
    leaves: list[tuple[str, Change]] = []
    mode_changes: list[tuple[str, Change]] = []
    deletions: list[tuple[str, Change]] = []
    # All paths intentionally added as dirs anywhere this wave (prune guard).
    added_dir_paths: set[str] = set()

    for plan_id, changes in changes_by_plan.items():
        for change in changes:
            if change.entry_kind == "dir" and change.kind in ("added", "modified"):
                added_dir_paths.add(change.path)
            if change.path in conflicting_paths:
                continue
            if change.kind in ("added", "modified") and change.entry_kind == "dir":
                added_dirs.append((plan_id, change))
            elif change.kind in ("added", "modified"):
                leaves.append((plan_id, change))
            elif change.kind == "mode-changed":
                mode_changes.append((plan_id, change))
            elif change.kind == "deleted":
                deletions.append((plan_id, change))

    # (1) Added directories, parents first.
    for _plan_id, change in sorted(added_dirs, key=lambda pc: pc[1].path.count("/")):
        dst = str(target / change.path)
        # file/symlink -> dir flip: clear the stale leaf before makedirs.
        if os.path.lexists(dst) and not (os.path.isdir(dst) and not os.path.islink(dst)):
            os.unlink(dst)
        os.makedirs(dst, exist_ok=True)
        if change.mode is not None:
            os.chmod(dst, change.mode)
        applied.append(change.path)

    # (2) File/symlink adds and modifies.
    for plan_id, change in leaves:
        src = str(sandboxes[plan_id] / change.path)
        dst = str(target / change.path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if change.entry_kind == "symlink":
            # Remove-before-recreate covers retarget and any type-flip.
            _remove_node(dst)
            os.symlink(os.readlink(src), dst)
        else:
            # Type-flip into a file: clear a stale dir/symlink first.
            if os.path.lexists(dst) and (os.path.isdir(dst) or os.path.islink(dst)):
                _remove_node(dst)
            with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            if change.mode is not None:
                os.chmod(dst, change.mode)
        applied.append(change.path)

    # (3) Standalone mode-changes.
    for _plan_id, change in mode_changes:
        dst = str(target / change.path)
        if change.mode is not None and os.path.lexists(dst) and not os.path.islink(dst):
            os.chmod(dst, change.mode)
            applied.append(change.path)

    # (4) Deletions of files/symlinks (missing-path tolerant). Dirs are pruned.
    for _plan_id, change in deletions:
        if change.entry_kind == "dir":
            continue
        dst = str(target / change.path)
        if os.path.lexists(dst):
            os.unlink(dst)
        applied.append(change.path)

    # Wave-end prune: deepest-first, remove empty dirs not intentionally added.
    prune_candidates: set[str] = set()
    for _plan_id, change in deletions:
        if change.entry_kind == "dir":
            prune_candidates.add(change.path)
    for path in sorted(prune_candidates, key=lambda p: p.count("/"), reverse=True):
        if path in added_dir_paths:
            continue
        dst = str(target / path)
        if os.path.isdir(dst) and not os.path.islink(dst) and os.listdir(dst) == []:
            os.rmdir(dst)
            applied.append(path)

    return MergeResult(applied_paths=applied, conflicts=[])


def resolve_conflict_winner(
    plan_a: str,
    plan_b: str,
    file_scope: dict[str, list[str]],
) -> tuple[str, str]:
    """Return (winner, loser) for two plans contesting a path.

    Design: §7.4 the plan with the narrower, more-specific declared file scope
        owns the contested path because it claimed it more tightly; on a scope tie
        the lexicographically lower plan id wins so resolution is deterministic.
    Implementation: compare scope specificity — a non-empty scope is narrower than
        an empty (unscoped) one, and among non-empty scopes fewer globs is
        narrower. On a tie, the smaller id is the winner. The caller adds a
        depends_on(loser -> winner) edge; cycle avoidance lives in the scheduler.
    Example: resolve_conflict_winner("pa", "pb", {"pa": [], "pb": []}) -> ("pa", "pb").
    """
    scope_a = file_scope.get(plan_a, [])
    scope_b = file_scope.get(plan_b, [])

    def _specificity(scope: list[str]) -> tuple[int, int]:
        """Return a sort key where a smaller tuple means a narrower scope.

        Design: §7.4 narrower scope wins; an explicit scope is narrower than an
            unscoped (empty) plan, and fewer globs is narrower than many.
        Implementation: rank unscoped plans last (1, 0) and scoped plans by glob
            count (0, len) so ascending order puts the narrowest first.
        Example: _specificity(["a/*"]) < _specificity([]) is True.
        """
        if not scope:
            return (1, 0)
        return (0, len(scope))

    spec_a = _specificity(scope_a)
    spec_b = _specificity(scope_b)
    if spec_a != spec_b:
        return (plan_a, plan_b) if spec_a < spec_b else (plan_b, plan_a)
    return (plan_a, plan_b) if plan_a <= plan_b else (plan_b, plan_a)
