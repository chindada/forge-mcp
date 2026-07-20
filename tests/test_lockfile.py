# tests/test_lockfile.py
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forge_mcp.lockfile import TargetLock


def test_acquire_writes_payload_and_blocks_second(tmp_path: Path):
    """Design: §12 the lock serializes runs per target and carries a payload.
    Implementation: first acquire writes run.lock; a second acquire on the live
        lock raises (cannot steal a healthy holder).
    Example: run.lock content is the JSON payload.
    """
    a = TargetLock()
    a.acquire(tmp_path, run_id="20260623183102", started_at="t0")
    data = json.loads((tmp_path / ".harness" / "run.lock").read_text())
    assert data["pid"] == os.getpid() and data["run_id"] == "20260623183102"
    with pytest.raises(Exception):  # noqa: B017
        TargetLock().acquire(tmp_path, run_id="20260623183103", started_at="t1")
    a.release()


def test_release_is_idempotent(tmp_path: Path):
    """Design: §12 release() is idempotent.
    Implementation: double release does not raise; a fresh acquire then works.
    Example: second acquire succeeds after release.
    """
    a = TargetLock()
    a.acquire(tmp_path, run_id="20260623183102", started_at="t0")
    a.release()
    a.release()
    TargetLock().acquire(tmp_path, run_id="20260623183104", started_at="t2")


def test_steals_dead_holder(tmp_path: Path):
    """Design: §12 liveness-first stale recovery steals a dead holder's lock.
    Implementation: write a run.lock with an impossible pid; acquire steals it.
    Example: acquire succeeds and rewrites the payload.
    """
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "run.lock").write_text(
        json.dumps(
            {
                "pid": 2**30,
                "run_id": "old",
                "started_at": "x",
                "target_dir": str(tmp_path),
                "create_time": 0.0,
            }
        )
    )
    lock = TargetLock()
    lock.acquire(tmp_path, run_id="20260623183105", started_at="t3")
    assert json.loads((harness / "run.lock").read_text())["run_id"] == "20260623183105"
    lock.release()
