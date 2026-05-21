"""§6.5 TargetLock — one lock per target_dir, with stale recovery."""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

import filelock
import psutil

STALE_AFTER_SECONDS = 36 * 3600  # §6.5 — exceeds the 24h runtime cap.


class LockBusy(Exception):
    """Raised when another live run holds the target_dir lock.

    Design: §6.5 distinguishes busy targets from generic IO failure so
        preflight can fail fast with a SERVER_ERROR.
    Implementation: carries the lockfile path and embeds it in the message.
    Example: raise LockBusy(Path('/repo/.harness/run.lock')).
    """

    def __init__(self, path: Path) -> None:
        """Carry the lockfile path on the exception.

        Design: operators need the exact file to inspect or remove when a live
            lock blocks a new run.
        Implementation: store the path and pass a readable string to Exception.
        Example: LockBusy(Path('/tmp/t/.harness/run.lock')).path.
        """
        super().__init__(f"target lock busy: {path}")
        self.path = path


class TargetLock:
    """File-backed lock for one target_dir; mints the run_id (§6.5).

    Design: the lock payload owns the run identity, and stale recovery is
        allowed only for dead pids or locks older than the 36h threshold.
    Implementation: use filelock for the mutex and a JSON payload for forensic
        metadata; release is idempotent for cancellation paths.
    Example: lock = TargetLock(path); lock.acquire(); lock.release().
    """

    def __init__(self, path: Path) -> None:
        """Bind the lock to a filesystem path without touching disk.

        Design: deferred IO lets preflight order target validation before lock
            acquisition and keeps tests simple.
        Implementation: store the payload path and sibling filelock path.
        Example: TargetLock(Path('.harness/run.lock')).
        """
        self._path = path
        self._fl = filelock.FileLock(str(path) + ".fl")
        self._run_id: str | None = None
        self._released = True

    @property
    def run_id(self) -> str:
        """Return the 8-hex run_id minted by acquire().

        Design: §6.5 requires state.json, run dir, result, and lock payload to
            share the same identifier.
        Implementation: raise before acquire so callers cannot fabricate ids.
        Example: lock.acquire(); assert len(lock.run_id) == 8.
        """
        if self._run_id is None:
            raise RuntimeError("TargetLock.run_id read before acquire()")
        return self._run_id

    def acquire(self, adopt_run_id: str | None = None) -> None:
        """Acquire the lock, stealing stale holders; optionally adopt a run_id.

        Design: §H2 resume adopts the located run id so lockfile, run dir,
            state.json, and RunResult retain continuity; §H9 makes staleness
            PID-reuse-safe with process create_time.
        Implementation: unlink stale payloads, acquire filelock nonblocking,
            set adopted or minted run_id, and write private JSON with create_time.
        Example: lock.acquire(adopt_run_id='abcd1234').
        """
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._path.exists() and self._is_stale():
            self._path.unlink(missing_ok=True)
        try:
            self._fl.acquire(timeout=0)
        except filelock.Timeout as exc:
            raise LockBusy(self._path) from exc
        self._run_id = adopt_run_id if adopt_run_id is not None else secrets.token_hex(4)
        try:
            create_time = psutil.Process(os.getpid()).create_time()
        except Exception:
            create_time = 0.0
        payload = {
            "pid": os.getpid(),
            "run_id": self._run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "target_dir": str(self._path.parent),
            "create_time": create_time,
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n")
        if os.name == "posix":
            os.chmod(self._path, 0o600)
        self._released = False

    def release(self) -> None:
        """Idempotently release the lock and remove the payload.

        Design: §8.5 releases during cancellation before final state writing;
            cleanup may call release again without changing behavior.
        Implementation: guard with `_released`, unlink the payload, and best
            effort release the underlying filelock.
        Example: lock.release(); lock.release() is a no-op.
        """
        if self._released:
            return
        try:
            self._path.unlink(missing_ok=True)
        finally:
            try:
                self._fl.release()
            except Exception:
                pass
            self._released = True

    def _is_stale(self) -> bool:
        """Decide whether an existing lockfile may be stolen (§H9).

        Design: pid_exists alone misclassifies recycled PIDs as live holders;
            comparing stored and live create_time detects reuse precisely.
        Implementation: unreadable payload, old mtime, dead pid, missing
            create_time, or create_time mismatch all count as stale.
        Example: a dead run whose PID was reused returns True.
        """
        try:
            data = json.loads(self._path.read_text())
            pid = int(data.get("pid", 0))
        except Exception:
            return True
        try:
            if time.time() - self._path.stat().st_mtime > STALE_AFTER_SECONDS:
                return True
        except FileNotFoundError:
            return True
        if not psutil.pid_exists(pid):
            return True
        stored_create_time = data.get("create_time")
        if stored_create_time is None:
            return True
        try:
            live_create_time = psutil.Process(pid).create_time()
        except Exception:
            return True
        return live_create_time != stored_create_time
