"""Appointment reminder worker — delivers due reminders on a poll loop."""
import logging
import os
import threading

from app.core.database import SessionLocal
from app.services import reminders

logger = logging.getLogger("agworkforce.reminders")
_stop = threading.Event()
_thread = None
POLL = 30.0
_HANDLE = None


def _handle():
    global _HANDLE
    if _HANDLE is None:
        from app.services.workers_health import WorkerHandle
        _HANDLE = WorkerHandle("reminder").start()
    return _HANDLE


def process_due_reminders() -> int:
    db = SessionLocal()
    try:
        n = reminders.process_due_reminders(db)
        db.commit()
        return n
    except Exception:
        logger.exception("reminder worker error")
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        db.close()


def _loop():
    h = _handle()
    while not _stop.is_set():
        try:
            h.set_job("process_due_reminders")
            process_due_reminders()
            h.set_job(None)
            h.record_success()
        except Exception as exc:
            h.set_job(None)
            h.record_failure(exc)
            logger.exception("reminder loop error")
        h.beat_now()
        _stop.wait(POLL)


def start_worker():
    if os.environ.get("ENVIRONMENT") == "test" or os.environ.get("AGW_DISABLE_WORKER") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="reminder-worker")
    _thread.start()
    logger.info("appointment reminder worker started")


def stop_worker():
    _stop.set()
    if _HANDLE is not None:
        _HANDLE.shutdown()
