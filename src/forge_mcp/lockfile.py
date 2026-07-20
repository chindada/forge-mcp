"""Per-target crash-survivable O_EXCL lock with PID-reuse-safe stale recovery (§12)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType

import psutil


@dataclass(frozen=True)
class LockPayload:
    """Forensic payload written into run.lock.

    Design: §12 carries all forensic fields needed to decide whether a holder
        is alive and to detect PID reuse across reboots.
    Implementation: frozen dataclass serialised to JSON; create_time from
        psutil.Process is used as a PID-reuse guard.
    Example: LockPayload(pid=1, run_id='20260623183102', started_at='t0',
        target_dir='/tmp/x', create_time=1.23).
    """

    pid: int
    run_id: str
    started_at: str
    target_dir: str
    create_time: float


def _holder_is_alive(payload: LockPayload) -> bool:
    """Return True only when the payload holder is confirmed live and unrecycled.

    Design: §12 liveness-first recovery — we steal only when the holder is
        provably dead or its PID has been recycled; a long-running healthy
        process must never be evicted.
    Implementation: check psutil.pid_exists first (fast); then probe
        psutil.Process to get create_time and compare with the payload value.
        If create_time is unavailable (NoSuchProcess / AccessDenied), degrade
        to pid-liveness only. Return False for NoSuchProcess.
    Example: _holder_is_alive(payload_with_impossible_pid) returns False.
    """
    pid = payload.pid
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        actual_ct = proc.create_time()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # Cannot verify create_time; trust pid_exists result
        return True
    # PID-reuse guard: a mismatched create_time means the slot was recycled
    if payload.create_time != 0.0 and actual_ct != payload.create_time:
        return False
    return True


def _read_payload(lock_path: Path) -> LockPayload | None:
    """Read and parse a LockPayload from a lock file; return None on any error.

    Design: §12 stale recovery reads the existing lock before deciding whether
        to steal; a corrupt or missing file counts as stale.
    Implementation: read the file as text, json.loads, construct LockPayload
        from the dict. Any exception (IOError, KeyError, TypeError) returns None.
    Example: _read_payload(Path('/nonexistent')) returns None.
    """
    try:
        raw = lock_path.read_text()
        d = json.loads(raw)
        return LockPayload(
            pid=int(d["pid"]),
            run_id=str(d["run_id"]),
            started_at=str(d["started_at"]),
            target_dir=str(d["target_dir"]),
            create_time=float(d["create_time"]),
        )
    except Exception:
        return None


def _write_payload_to_fd(fd: int, payload: LockPayload) -> None:
    """Encode payload as JSON, write to fd, fsync, and close.

    Design: §12 the lock file carries a JSON forensic payload so post-mortem
        analysis can identify which run held the lock.
    Implementation: json.dumps the dataclass as a dict, encode to UTF-8,
        os.write, os.fsync, os.close.
    Example: after _write_payload_to_fd(fd, p), the fd is closed and the file
        contains JSON.
    """
    data = json.dumps(asdict(payload)).encode()
    os.write(fd, data)
    os.fsync(fd)
    os.close(fd)


def _make_payload(target_dir: Path, run_id: str, started_at: str) -> LockPayload:
    """Build a LockPayload for the current process.

    Design: §12 the payload encodes who holds the lock and when, enabling
        PID-reuse detection via psutil create_time.
    Implementation: os.getpid() for pid; psutil.Process().create_time() for
        the reuse guard; caller supplies run_id, started_at, target_dir.
    Example: _make_payload(Path('/x'), '20260623183102', 't0') returns a
        LockPayload with pid == os.getpid().
    """
    pid = os.getpid()
    try:
        ct = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        ct = 0.0
    return LockPayload(
        pid=pid,
        run_id=run_id,
        started_at=started_at,
        target_dir=str(target_dir),
        create_time=ct,
    )


def _atomic_steal(
    lock_path: Path,
    guard_path: Path,
    new_payload: LockPayload,
) -> bool:
    """Try to steal a stale lock using an O_EXCL guard file.

    Design: §12 the steal must be race-free; two concurrent stealers both
        write the guard via O_EXCL so only one proceeds; the loser re-reads
        the guard holder and backs off unless the guard itself is stale.
    Implementation:
        1. Try O_CREAT|O_EXCL on guard_path; on FileExistsError check the
           guard holder — if alive, back off (return False); if dead, unlink
           the guard and retry with os.replace of a temp file.
        2. After acquiring the guard, re-read lock_path and re-confirm it is
           still stale; write a temp payload and os.replace into lock_path;
           remove the guard.
    Example: returns True when the steal succeeds, False when a live process
        holds the guard and the caller should back off.
    """
    guard_payload = _make_payload(guard_path.parent, new_payload.run_id, new_payload.started_at)
    guard_payload_guard = LockPayload(
        pid=guard_payload.pid,
        run_id=guard_payload.run_id,
        started_at=guard_payload.started_at,
        target_dir=guard_payload.target_dir,
        create_time=guard_payload.create_time,
    )

    # --- Acquire the guard ---
    try:
        gfd = os.open(str(guard_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Someone else holds the guard — check if that holder is alive
        guard_holder = _read_payload(guard_path)
        if guard_holder is not None and _holder_is_alive(guard_holder):
            # Live guard holder — back off, let it win
            return False
        # Dead guard holder — take it over
        try:
            os.unlink(str(guard_path))
        except FileNotFoundError:
            pass
        try:
            gfd = os.open(str(guard_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False  # Another stealer beat us again — back off
    try:
        _write_payload_to_fd(gfd, guard_payload_guard)
    except Exception:
        try:
            os.unlink(str(guard_path))
        except OSError:
            pass
        raise

    # --- We hold the guard; re-confirm the lock is still stale ---
    try:
        current = _read_payload(lock_path)
        if current is not None and _holder_is_alive(current):
            # Lock was reclaimed by a live process between our check and the guard
            return False

        # Write new payload to a temp file and os.replace over lock_path
        fd, tmp = tempfile.mkstemp(dir=lock_path.parent)
        try:
            _write_payload_to_fd(fd, new_payload)
            os.replace(tmp, str(lock_path))
            tmp = None  # type: ignore[assignment]
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    finally:
        try:
            os.unlink(str(guard_path))
        except FileNotFoundError:
            pass


class TargetLock:
    """Per-target O_EXCL lock with liveness-first stale recovery.

    Design: §12 serializes concurrent forge runs against a single target_dir
        by writing a crash-survivable marker whose payload enables PID-reuse-
        safe recovery without ever stealing a live healthy run.
    Implementation: acquire uses O_CREAT|O_EXCL to create run.lock atomically;
        on collision it evaluates holder liveness and either raises or steals.
        release is idempotent: it only unlinks if this instance created the lock.
        Context-manager sugar calls acquire/release.
    Example: with TargetLock() as lk: lk.acquire(path, run_id, started_at).
    """

    def __init__(self) -> None:
        """Initialise an unacquired TargetLock.

        Design: §12 the lock object is a thin wrapper; no resources are held
            until acquire() is called.
        Implementation: store None sentinels for the lock path and payload so
            release() can safely detect whether this instance holds a lock.
        Example: TargetLock() creates an idle lock object.
        """
        self._lock_path: Path | None = None
        self._payload: LockPayload | None = None

    def acquire(self, target_dir: Path, run_id: str, started_at: str) -> None:
        """Acquire the per-target run.lock, stealing stale locks when safe.

        Design: §12 only one concurrent forge run per target_dir is permitted;
            a healthy holder causes an immediate raise rather than a wait.
        Implementation: ensure <target_dir>/.harness/ exists; attempt
            O_CREAT|O_EXCL on run.lock; on FileExistsError evaluate holder
            liveness — raise RuntimeError for live holders, call _atomic_steal
            for dead/corrupt holders; if the steal succeeds, record ownership.
        Example: acquire(Path('/x'), '20260623183102', 't0') creates
            /x/.harness/run.lock with the caller's pid in the payload.
        """
        harness = target_dir / ".harness"
        harness.mkdir(parents=True, exist_ok=True)
        lock_path = harness / "run.lock"
        guard_path = harness / "run.lock.steal"
        payload = _make_payload(target_dir, run_id, started_at)

        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                existing = _read_payload(lock_path)
                if existing is None:
                    # Corrupt/unreadable — steal it
                    stole = _atomic_steal(lock_path, guard_path, payload)
                    if stole:
                        self._lock_path = lock_path
                        self._payload = payload
                        return
                    # Failed steal — re-check in next loop iteration
                    continue
                if _holder_is_alive(existing):
                    raise RuntimeError(
                        f"Target {target_dir} is already locked by pid={existing.pid} "
                        f"run_id={existing.run_id}"
                    ) from None
                # Dead holder — attempt race-safe steal
                stole = _atomic_steal(lock_path, guard_path, payload)
                if stole:
                    self._lock_path = lock_path
                    self._payload = payload
                    return
                # Steal failed (another stealer won); loop and try O_EXCL again
                continue
            else:
                _write_payload_to_fd(fd, payload)
                self._lock_path = lock_path
                self._payload = payload
                return

    def release(self) -> None:
        """Release the lock if this instance holds it; idempotent.

        Design: §12 release must never raise so callers in finally blocks are
            always safe; a double release is a no-op.
        Implementation: unlink _lock_path only when set (this instance owns
            the lock); swallow FileNotFoundError; clear sentinels afterwards.
        Example: calling release() twice on the same TargetLock does not raise.
        """
        if self._lock_path is None:
            return
        try:
            os.unlink(str(self._lock_path))
        except FileNotFoundError:
            pass
        self._lock_path = None
        self._payload = None

    def __enter__(self) -> TargetLock:
        """Return self for use as a context manager.

        Design: §12 context-manager sugar keeps call-sites concise.
        Implementation: return self; acquire() must still be called explicitly.
        Example: with TargetLock() as lk: lk.acquire(...).
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Release the lock on context exit.

        Design: §12 context-manager exit always releases so locks are not
            leaked when the body raises.
        Implementation: call self.release() unconditionally; propagate any
            original exception by returning None.
        Example: exiting a with-block via exception still releases the lock.
        """
        self.release()
