"""Sales Manager background worker — periodic analysis per workspace."""
import logging
import os
import threading
import time

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Workspace
from app.services import manager_engine

logger = logging.getLogger("agworkforce.manager")
_stop = threading.Event()
_thread = None
POLL = 300.0  # 5 minutes
_HANDLE = None


def _handle():
    global _HANDLE
    if _HANDLE is None:
        from app.services.workers_health import WorkerHandle
        _HANDLE = WorkerHandle("manager").start()
    return _HANDLE


def run_all() -> int:
    db = SessionLocal()
    try:
        wids = [w.id for w in db.scalars(select(Workspace.id)).all()]
        for wid in wids:
            try:
                manager_engine.run_analysis(db, wid, kind="scan")
            except Exception:
                logger.exception("manager analysis failed for workspace %s", wid)
                try:
                    db.rollback()
                except Exception:
                    pass
        db.commit()
        return len(wids)
    finally:
        db.close()


def _loop():
    h = _handle()
    while not _stop.is_set():
        try:
            h.set_job("run_analysis_all_workspaces")
            run_all()
            h.set_job(None)
            h.record_success()
        except Exception as exc:
            h.set_job(None)
            h.record_failure(exc)
            logger.exception("manager worker loop error")
        h.beat_now()
        _stop.wait(POLL)


def start_worker():
    if os.environ.get("ENVIRONMENT") == "test" or os.environ.get("AGW_DISABLE_WORKER") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="manager-worker")
    _thread.start()
    logger.info("sales manager worker started")


def stop_worker():
    _stop.set()
    if _HANDLE is not None:
        _HANDLE.shutdown()
