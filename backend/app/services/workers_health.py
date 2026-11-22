"""Worker heartbeat + health reporting (Phase 8.6/8.8).

Stable identity: worker_id = "<type>:<hostname>:<pid>" generated ONCE per
process — never regenerated per heartbeat. A pid+host pair cannot collide
with an unrelated live process; after a restart the old row simply goes
stale while the new process writes its own row.

Statuses: running | idle | stopped | failed (+ derived "stale" in health()).
Never stores job payloads, prompts, messages or secrets — job NAME only.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkerHeartbeat

STALE_AFTER = timedelta(seconds=90)
FAILED_AFTER = 3  # consecutive failures -> status "failed"
HEARTBEAT_SECONDS = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15"))


def worker_id_for(worker_type: str) -> str:
    return f"{worker_type}:{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create(db: Session, worker_type: str,
                   worker_id: str | None = None) -> WorkerHeartbeat:
    wid = worker_id or worker_id_for(worker_type)
    row = db.scalar(select(WorkerHeartbeat).where(
        WorkerHeartbeat.worker_id == wid))
    now = _now()
    if row is None:
        row = WorkerHeartbeat(
            worker_id=wid, worker_type=worker_type, status="running",
            host=socket.gethostname(), pid=os.getpid(),
            started_at=now, last_heartbeat=now)
        db.add(row)
    else:
        # same process id -> refresh liveness; preserve history
        row.last_heartbeat = now
        if row.status in ("stopped", "failed"):
            row.status = "running"
        if row.host is None:
            row.host = socket.gethostname()
            row.pid = os.getpid()
    db.flush()
    return row


def beat(db: Session, worker_type: str, job: str | None = None,
         worker_id: str | None = None) -> WorkerHeartbeat:
    row = _get_or_create(db, worker_type, worker_id)
    row.last_heartbeat = _now()
    if job is not None:
        row.current_job = job[:200]
        row.status = "running"
        row.last_job_name = job[:200]
    elif row.status == "failed":
        row.status = "running"
    db.flush()
    return row


def idle(db: Session, worker_type: str, worker_id: str | None = None) -> None:
    row = _get_or_create(db, worker_type, worker_id)
    row.status = "idle" if row.current_job is None else row.status
    row.last_heartbeat = _now()
    db.flush()


def success(db: Session, worker_id: str) -> None:
    row = db.scalar(select(WorkerHeartbeat).where(
        WorkerHeartbeat.worker_id == worker_id))
    if row:
        row.last_success_at = _now()
        row.consecutive_failures = 0
        row.current_job = None
        db.flush()


def failure(db: Session, worker_id: str, error: str | None = None) -> None:
    row = db.scalar(select(WorkerHeartbeat).where(
        WorkerHeartbeat.worker_id == worker_id))
    if row:
        row.last_failure_at = _now()
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.last_error = (error or "")[:300] or None
        if row.consecutive_failures >= FAILED_AFTER:
            row.status = "failed"
        db.flush()


def stop(db: Session, worker_id: str) -> None:
    row = db.scalar(select(WorkerHeartbeat).where(
        WorkerHeartbeat.worker_id == worker_id))
    if row:
        row.status = "stopped"
        row.current_job = None
        db.flush()


def start(db: Session, worker_type: str, worker_id: str | None = None) -> WorkerHeartbeat:
    """Explicit startup marker — bumps recovery_attempts on restart."""
    row = _get_or_create(db, worker_type, worker_id)
    row.status = "running"
    row.started_at = row.started_at or _now()
    row.recovery_attempts = (row.recovery_attempts or 0) + 1
    db.flush()
    return row


class WorkerHandle:
    """Per-process worker identity + optional background heartbeat thread.

    The heartbeat thread keeps last_heartbeat fresh even while the worker
    loop sleeps between polls or inside a long-running job.
    """

    def __init__(self, worker_type: str, interval: float = HEARTBEAT_SECONDS):
        self.worker_type = worker_type
        self.worker_id = worker_id_for(worker_type)
        self.interval = interval
        self._current_job: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- persistence helpers (own session; never disturb job transactions)
    def _with_db(self, fn):
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            fn(db)
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    def _beat_once(self):
        job = self._current_job
        def _do(db):
            beat(db, self.worker_type, job=job, worker_id=self.worker_id)
        self._with_db(_do)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._beat_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    # -- public API
    def start(self) -> "WorkerHandle":
        def _do(db):
            start(db, self.worker_type, worker_id=self.worker_id)
        self._with_db(_do)
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True,
                name=f"{self.worker_type}-heartbeat")
            self._thread.start()
        return self

    def set_job(self, name: str | None):
        with self._lock:
            self._current_job = name[:200] if name else None

    def job(self, name: str):
        """Context manager: marks current_job for the duration."""
        handle = self

        class _CM:
            def __enter__(self):
                handle.set_job(name)
                handle.beat_now()
                return handle

            def __exit__(self, *exc):
                handle.set_job(None)
                if exc[0] is None:
                    handle.record_success()
                else:
                    handle.record_failure(exc[1])
                return False
        return _CM()

    def beat_now(self):
        self._beat_once()

    def record_success(self):
        def _do(db):
            success(db, self.worker_id)
        self._with_db(_do)

    def record_failure(self, exc: BaseException | None = None):
        err = type(exc).__name__ if exc else None
        def _do(db):
            failure(db, self.worker_id, error=err)
        self._with_db(_do)

    def shutdown(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        def _do(db):
            stop(db, self.worker_id)
        self._with_db(_do)


def health(db: Session, stale_after: timedelta = STALE_AFTER,
           worker_type: str | None = None, status: str | None = None,
           stale_only: bool = False) -> dict:
    now = _now()
    q = select(WorkerHeartbeat)
    if worker_type:
        q = q.where(WorkerHeartbeat.worker_type == worker_type)
    rows = db.scalars(q).all()
    out = []
    counts = {"running": 0, "idle": 0, "stopped": 0, "failed": 0,
              "stale": 0, "degraded": 0, "healthy": 0}
    for r in rows:
        hb = r.last_heartbeat
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        age = (now - hb).total_seconds()
        stale = age > stale_after.total_seconds()
        eff = "stale" if stale and r.status in ("running", "idle") else r.status
        degraded = (r.consecutive_failures or 0) > 0 and not stale
        if eff == "stale":
            counts["stale"] += 1
        elif eff in counts:
            counts[eff] += 1
        if degraded:
            counts["degraded"] += 1
        if eff in ("running", "idle") and not stale and not degraded:
            counts["healthy"] += 1
        item = {
            "worker_id": r.worker_id, "worker_type": r.worker_type,
            "host": r.host, "pid": r.pid,
            "status": eff,
            "stored_status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "last_heartbeat": hb.isoformat(),
            "heartbeat_age_seconds": int(age),
            "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
            "last_failure_at": r.last_failure_at.isoformat() if r.last_failure_at else None,
            "last_job_name": r.last_job_name,
            "last_error": r.last_error,
            "consecutive_failures": r.consecutive_failures,
            "recovery_attempts": r.recovery_attempts,
            "current_job": r.current_job,  # job name only, never payloads
            "stale": stale,
            "degraded": degraded,
        }
        if status and eff != status:
            continue
        if stale_only and not stale:
            continue
        out.append(item)
    return {"workers": out,
            "counts": counts,
            "stale_after_seconds": int(stale_after.total_seconds()),
            "checked_at": now.isoformat()}
