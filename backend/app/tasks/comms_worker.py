"""Follow-up worker — processes scheduled follow-ups safely.

Stop conditions enforced at execution time: prospect replied, opted out,
human handoff, max attempts reached, or conversation closed.
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import (
    Channel, Conversation, FollowUp, FollowUpStatus, Lead, LeadStage,
    MessageDirection, MessageStatus,
)
import app.services.outreach as outreach
from app.services.conversation_engine import get_or_create_conversation
from app.services.helpers import log_activity
from app.models import ActorType

logger = logging.getLogger("agworkforce.comms")
_stop = threading.Event()
_thread = None
POLL = 30.0
_HANDLE = None


def _handle():
    global _HANDLE
    if _HANDLE is None:
        from app.services.workers_health import WorkerHandle
        _HANDLE = WorkerHandle("comms").start()
    return _HANDLE


def _now():
    return datetime.now(timezone.utc)


def process_due_follow_ups() -> int:
    """Process all due scheduled follow-ups. Returns count processed."""
    db = SessionLocal()
    processed = 0
    try:
        now = _now()
        due = db.scalars(select(FollowUp).where(
            FollowUp.status == FollowUpStatus.scheduled,
            FollowUp.scheduled_at <= now,
        ).order_by(FollowUp.scheduled_at).limit(50)).all()
        for fu in due:
            conv = db.get(Conversation, fu.conversation_id)
            if conv is None:
                fu.status = FollowUpStatus.skipped
                continue
            # ---- stop conditions
            if conv.state in ("opted_out", "closed", "human_handoff", "demo_scheduled"):
                fu.status = FollowUpStatus.cancelled
                continue
            lead = db.get(Lead, fu.lead_id) if fu.lead_id else conv.lead
            if lead and lead.stage in (LeadStage.won, LeadStage.lost, LeadStage.do_not_contact):
                fu.status = FollowUpStatus.cancelled
                continue
            if fu.attempt >= fu.max_attempts:
                fu.status = FollowUpStatus.completed
                continue
            # prospect replied since scheduling? (inbound msg newer than last outbound)
            last_in = db.scalar(select(__import__('app.models', fromlist=['Message']).Message.created_at)
                                .where(__import__('app.models', fromlist=['Message']).Message.conversation_id == conv.id,
                                       __import__('app.models', fromlist=['Message']).Message.direction == MessageDirection.inbound)
                                .order_by(__import__('app.models', fromlist=['Message']).Message.created_at.desc()).limit(1))
            if last_in and fu.scheduled_at and last_in > fu.created_at:
                fu.status = FollowUpStatus.cancelled
                continue

            fu.attempt += 1
            draft = outreach.generate_reply_draft(db, fu.workspace_id, conv)
            msg = outreach.create_outbound_draft(
                db, fu.workspace_id, conv,
                draft.get("subject") or f"Following up — {lead.business_name if lead else ''}",
                draft.get("body", ""), agent_slug="communication-manager")
            # attempt autonomous send only if explicitly permitted + compliance ok
            sent = False
            agent = db.scalar(select(__import__('app.models', fromlist=['Agent']).Agent).where(
                __import__('app.models', fromlist=['Agent']).Agent.workspace_id == fu.workspace_id,
                __import__('app.models', fromlist=['Agent']).Agent.slug == "communication-manager"))
            autonomous = bool(agent and agent.enabled and
                              "send_email" in (agent.permissions or {}).get("autonomous_actions", []))
            if autonomous:
                res = outreach.send_message(db, fu.workspace_id, msg)
                sent = bool(res.get("sent"))
            if sent:
                fu.status = FollowUpStatus.sent
                fu.sent_at = _now()
                if fu.attempt >= fu.max_attempts:
                    fu.status = FollowUpStatus.completed
                else:
                    fu.status = FollowUpStatus.scheduled
                    fu.scheduled_at = _now() + __import__('datetime').timedelta(hours=fu.interval_hours)
            else:
                fu.status = FollowUpStatus.scheduled if fu.attempt < fu.max_attempts else FollowUpStatus.completed
                fu.scheduled_at = _now() + __import__('datetime').timedelta(hours=fu.interval_hours)
                fu.last_error = "draft created, awaiting approval" if not autonomous else "send failed"
            processed += 1
        db.commit()
    except Exception:
        logger.exception("follow-up worker error")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
    return processed


def _loop():
    h = _handle()
    while not _stop.is_set():
        try:
            h.set_job("process_due_follow_ups")
            process_due_follow_ups()
            h.set_job(None)
            h.record_success()
        except Exception as exc:
            h.set_job(None)
            h.record_failure(exc)
            logger.exception("follow-up loop error")
        h.beat_now()
        _stop.wait(POLL)


def start_worker():
    if os.environ.get("ENVIRONMENT") == "test" or os.environ.get("AGW_DISABLE_WORKER") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="comms-worker")
    _thread.start()
    logger.info("comms follow-up worker started")


def stop_worker():
    _stop.set()
    if _HANDLE is not None:
        _HANDLE.shutdown()
