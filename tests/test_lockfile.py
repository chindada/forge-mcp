"""§6.5 TargetLock acquire/release semantics and stale recovery."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from forge_mcp.lockfile import LockBusy, TargetLock


def _lock(target_dir: Path) -> TargetLock:
    """Construct a TargetLock at the conventional target path.

    Design: tests exercise the §6.5 layout `<target_dir>/.harness/run.lock`.
    Implementation: create `.harness` and return TargetLock for run.lock.
    Example: lock = _lock(tmp_path).
    """
    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    return TargetLock(target_dir / ".harness" / "run.lock")


def test_acquire_mints_8hex_run_id_and_writes_payload(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock = _lock(target_dir)
    lock.acquire()
    try:
        assert len(lock.run_id) == 8 and all(c in "0123456789abcdef" for c in lock.run_id)
        payload = json.loads((target_dir / ".harness" / "run.lock").read_text())
        assert payload["pid"] == os.getpid()
        assert payload["run_id"] == lock.run_id
        assert payload["target_dir"] == str(target_dir)
    finally:
        lock.release()


def test_double_acquire_same_target_raises_lockbusy(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    a = _lock(target_dir)
    b = _lock(target_dir)
    a.acquire()
    try:
        with pytest.raises(LockBusy):
            b.acquire()
    finally:
        a.release()


def test_release_is_idempotent(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock = _lock(target_dir)
    lock.acquire()
    lock.release()
    lock.release()


def test_stale_recovery_on_dead_pid(target_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock_path = target_dir / ".harness" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.write_text(
        json.dumps({"pid": 999999, "run_id": "deadbeef", "target_dir": str(lock_path.parent)})
    )
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)
    fresh = TargetLock(lock_path)
    fresh.acquire()
    try:
        assert fresh.run_id != "deadbeef"
    finally:
        fresh.release()


def test_stale_recovery_on_old_mtime(target_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock_path = target_dir / ".harness" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "run_id": "abc12345", "target_dir": str(lock_path.parent)})
    )
    old = time.time() - 37 * 3600
    os.utime(lock_path, (old, old))
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    fresh = TargetLock(lock_path)
    fresh.acquire()
    try:
        assert fresh.run_id != "abc12345"
    finally:
        fresh.release()


def test_dead_pid_lock_is_stolen(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock_path = target_dir / ".harness" / "run.lock"
    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999999999,
                "run_id": "deadbeef",
                "started_at": "2020-01-01T00:00:00+00:00",
                "target_dir": str(target_dir / ".harness"),
                "create_time": 1.0,
            }
        )
        + "\n"
    )
    lock = _lock(target_dir)
    lock.acquire()
    try:
        assert lock.run_id != "deadbeef"
    finally:
        lock.release()


def test_recycled_pid_create_time_mismatch_is_stolen(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock_path = target_dir / ".harness" / "run.lock"
    (target_dir / ".harness").mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "run_id": "deadbeef",
                "started_at": "2020-01-01T00:00:00+00:00",
                "target_dir": str(target_dir / ".harness"),
                "create_time": -1.0,
            }
        )
        + "\n"
    )
    lock = _lock(target_dir)
    lock.acquire()
    try:
        assert lock.run_id != "deadbeef"
    finally:
        lock.release()


def test_live_matching_lock_blocks(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    a = _lock(target_dir)
    a.acquire()
    b = _lock(target_dir)
    try:
        with pytest.raises(LockBusy):
            b.acquire()
    finally:
        a.release()


def test_adopt_run_id_reuses_identity(target_dir: Path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    lock = _lock(target_dir)
    lock.acquire(adopt_run_id="abcd1234")
    try:
        assert lock.run_id == "abcd1234"
        payload = json.loads((target_dir / ".harness" / "run.lock").read_text())
        assert payload["run_id"] == "abcd1234"
    finally:
        lock.release()
