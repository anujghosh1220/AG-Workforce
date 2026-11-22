"""Phase 7 — orchestrator background worker.

- Recovers interrupted workflows after restart.
- Resumes workflows waiting on approvals.
- Dispatches pending workflow events.
"""
import logging
import os
import threading
import time

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Workflow, WorkflowStatus
from app.services import events as ev
from app.services import orchestrator

logger = logging.getLogger("agworkforce.orchestrator")
_stop = threading.Event()
_thread = None
POLL = 15.0
_HANDLE = None


def _handle():
    global _HANDLE
    if _HANDLE is None:
        from app.services.workers_health import WorkerHandle
        _HANDLE = WorkerHandle("orchestrator").start()
    return _HANDLE


def run_once() -> dict:
    h = _handle()
    h.set_job("orchestrator_cycle")
    h.beat_now()
    db = SessionLocal()
    try:
        recovered = orchestrator.recover_interrupted(db)
        # resume approval-waiting workflows whose approvals were decided
        waiting = db.scalars(select(Workflow).where(
            Workflow.status == WorkflowStatus.waiting_approval)).all()
        resumed = 0
        for wf in waiting:
            try:
                orchestrator.resume_after_approval(db, wf)
                resumed += 1
            except Exception:
                logger.exception("resume failed for %s", wf.id)
                db.rollback()
        dispatched = ev.dispatch_pending(db)
        db.commit()
        h.set_job(None)
        h.record_success()
        return {"recovered": recovered, "resumed": resumed,
                "events": dispatched}
    except Exception as exc:
        h.set_job(None)
        h.record_failure(exc)
        raise
    finally:
        db.close()


def _loop():
    while not _stop.is_set():
        try:
            run_once()
        except Exception:
            logger.exception("orchestrator worker loop error")
        _stop.wait(POLL)


def start_worker():
    if os.environ.get("ENVIRONMENT") == "test" or os.environ.get("AGW_DISABLE_WORKER") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True,
                               name="orchestrator-worker")
    _thread.start()
    logger.info("orchestrator worker started")


def stop_worker():
    _stop.set()
    if _HANDLE is not None:
        _HANDLE.shutdown()
