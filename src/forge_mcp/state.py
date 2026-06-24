from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_replace(path: Path, data: bytes | str, *, dir_fsync: bool) -> None:
    """Write data to path via a same-dir tempfile, then atomically replace.

    Design: §13 crash durability requires that a partial write never leaves
        the target file in a corrupted state; using a same-dir tempfile and
        os.replace provides atomic rename semantics on POSIX.
    Implementation: encode str to UTF-8 bytes; create a tempfile via
        tempfile.mkstemp in path.parent; os.write all bytes; os.fsync the fd;
        chmod 0o600; close; os.replace into place. If dir_fsync, also fsync
        the parent directory (best-effort, swallows OSError). On any exception,
        attempt to unlink the tempfile (best-effort).
    Example: _atomic_replace(Path('/tmp/x'), 'hello', dir_fsync=False) writes
        the string 'hello' to /tmp/x with mode 0o600.
    """
    raw: bytes = data.encode() if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        os.write(fd, raw)
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        tmp = None  # type: ignore[assignment]
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise

    if dir_fsync:
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


def durable_replace(path: Path, data: bytes | str) -> None:
    """Atomically replace path with data and fsync both file and parent dir.

    Design: §13 this is the writer for state.json (run + per-plan) and
        spec.md, where losing the last write on a crash is unacceptable.
    Implementation: delegates to _atomic_replace with dir_fsync=True so the
        directory entry pointing to the new inode is also flushed to disk.
    Example: durable_replace(Path('/run/state.json'), '{}') writes '{}' and
        fsyncs both the file and its parent directory.
    """
    _atomic_replace(path, data, dir_fsync=True)


def light_replace(path: Path, data: bytes | str) -> None:
    """Atomically replace path with data; skip parent-dir fsync.

    Design: §13 lighter writer for fingerprints and ordinary artifacts where
        a directory-entry fsync is not worth the latency cost.
    Implementation: delegates to _atomic_replace with dir_fsync=False so only
        the file fd is fsynced before the rename.
    Example: light_replace(Path('/run/fp.json'), b'[]') writes bytes and
        skips the parent-dir fsync.
    """
    _atomic_replace(path, data, dir_fsync=False)


def durable_append(path: Path, text: str) -> None:
    """Append text to path durably; create the file if it does not yet exist.

    Design: §13/I3 spec_amendments.md is an append-only audit log — it must
        NEVER be replaced wholesale (which would truncate prior entries).
        First creation gets a parent-dir fsync to make the new inode visible.
    Implementation: if path does not exist, open in exclusive-create mode
        ('x') to avoid a race, then fsync the parent directory (best-effort).
        Then open in append mode, write text, flush, and os.fsync the fd.
    Example: two calls with 'entry-1\\n' and 'entry-2\\n' leave both lines in
        the file with no truncation between them.
    """
    if not path.exists():
        with open(path, "x"):
            pass
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    with open(path, "a") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def write_json(path: Path, obj: Any, *, durable: bool) -> None:
    """Serialise obj to JSON and write it to path via the chosen durability tier.

    Design: §13 provides a single entry-point for JSON state files so callers
        do not choose between json.dumps and model_dump_json directly.
    Implementation: if obj has model_dump_json (a Pydantic BaseModel) call that;
        otherwise use json.dumps with sort_keys=True. Route to durable_replace
        when durable=True, else light_replace.
    Example: write_json(p, {'k': 1}, durable=True) writes '{"k": 1}' durably.
    """
    if hasattr(obj, "model_dump_json"):
        text: str = obj.model_dump_json()
    else:
        text = json.dumps(obj, sort_keys=True)

    if durable:
        durable_replace(path, text)
    else:
        light_replace(path, text)
