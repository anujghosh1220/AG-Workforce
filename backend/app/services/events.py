"""Phase 7 — database-backed event/outbox system.

Events are idempotent (unique idempotency_key), workspace-scoped, and
safely recoverable: pending events are re-dispatched by the worker.
Circular triggers are prevented by a propagation-depth cap.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import WorkflowEvent, WorkflowEventStatus

logger = logging.getLogger("agworkforce.events")
MAX_DEPTH = 3

# handlers registered by name -> fn(db, event)
_HANDLERS: dict[str, callable] = {}


def register_handler(event_type: str, fn):
    _HANDLERS[event_type] = fn


def _idem_key(wid, event_type, payload, explicit=None) -> str:
    if explicit:
        return explicit
    raw = f"{wid}|{event_type}|{sorted(payload.items(), key=lambda x: str(x[0]))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def publish(db: Session, wid: uuid.UUID, event_type: str, payload: dict | None = None,
            workflow_id: uuid.UUID | None = None, step_id: uuid.UUID | None = None,
            idempotency_key: str | None = None) -> WorkflowEvent | None:
    """Insert an event row. Returns None if an identical event already exists."""
    payload = dict(payload or {})
    key = _idem_key(wid, event_type, payload, idempotency_key)
    ev = WorkflowEvent(
        workspace_id=wid, workflow_id=workflow_id, step_id=step_id,
        event_type=event_type, payload=payload, idempotency_key=key)
    try:
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError as exc:
        logger.warning("event insert rejected: %s", exc)
        return None  # idempotent duplicate
    return ev


def list_events(db: Session, wid: uuid.UUID, limit: int = 100,
                workflow_id: uuid.UUID | None = None) -> list[WorkflowEvent]:
    q = select(WorkflowEvent).where(WorkflowEvent.workspace_id == wid)
    if workflow_id:
        q = q.where(WorkflowEvent.workflow_id == workflow_id)
    return list(db.scalars(q.order_by(WorkflowEvent.created_at.desc())
                           .limit(limit)).all())


def dispatch_pending(db: Session, limit: int = 50) -> int:
    """Process pending events through registered handlers. Worker entrypoint."""
    rows = db.scalars(select(WorkflowEvent).where(
        WorkflowEvent.status == WorkflowEventStatus.pending)
        .order_by(WorkflowEvent.created_at).limit(limit)).all()
    n = 0
    for ev in rows:
        ev.attempts += 1
        handler = _HANDLERS.get(ev.event_type)
        try:
            if handler:
                depth = int((ev.payload or {}).get("_depth", 0))
                if depth < MAX_DEPTH:
                    handler(db, ev)
            ev.status = WorkflowEventStatus.processed
            ev.processed_at = datetime.now(timezone.utc)
            ev.error = None
            n += 1
        except Exception as exc:  # keep event pending for retry
            ev.status = WorkflowEventStatus.failed if ev.attempts >= 5 else WorkflowEventStatus.pending
            ev.error = str(exc)[:500]
            logger.exception("event %s dispatch failed", ev.id)
    db.flush()
    return n


def safe_publish(db: Session, wid, event_type: str, payload=None, **kw):
    """Never let event publishing break the caller."""
    try:
        return publish(db, wid, event_type, payload, **kw)
    except Exception:
        logger.exception("failed to publish %s", event_type)
        return None
