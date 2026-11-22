"""Inbound email webhook — signature-verified, idempotent."""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.email.base import get_email_provider
from app.models import Channel, WebhookEvent
from app.services.conversation_engine import handle_inbound

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _resolve_workspace(db: Session, to_address: str | None):
    """Map inbound recipient address -> workspace via agent email config.
    Workspaces configure their inbound address in workspace.settings later;
    for now match lead by sender only after workspace is resolved via a
    configured inbound address stored on Workspace.settings['inbound_email'].
    Fallback: if exactly one workspace has leads matching sender domain we
    still require explicit mapping — return None when ambiguous."""
    # Look up workspace by inbound address stored in settings JSON on Workspace
    from app.models import Workspace
    if to_address:
        ws = db.scalars(select(Workspace)).all()
        for w in ws:
            settings = getattr(w, "settings", None) or {}
            if settings.get("inbound_email", "").lower() == to_address.lower():
                return w.id
    return None


@router.post("/email/{provider}")
async def email_webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    if provider != "resend":
        return {"status": "ignored", "reason": "unknown provider"}
    ep = get_email_provider()
    if ep is None:
        return {"status": "ignored", "reason": "provider not configured"}
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not ep.verify_webhook(headers, raw):
        from fastapi import HTTPException
        raise HTTPException(401, "invalid signature")
    import json
    try:
        payload = json.loads(raw)
    except Exception:
        return {"status": "ignored", "reason": "bad json"}
    event = ep.parse_event(payload)
    if event is None:
        return {"status": "ignored", "reason": "unhandled event type"}

    # idempotency
    existing = db.scalar(select(WebhookEvent).where(
        WebhookEvent.provider == provider, WebhookEvent.event_id == event.event_id))
    if existing:
        return {"status": "duplicate"}
    we = WebhookEvent(provider=provider, event_id=event.event_id,
                      event_type=event.event_type, payload=payload, processed=False)
    db.add(we)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}

    result = {"status": "received"}
    # ---- delivery lifecycle events: correlate by provider message id
    if event.event_type in ("delivered", "bounced", "complained", "failed") \
            and event.message_id:
        from app.models import Message, MessageStatus, SuppressionRecord
        msg = db.scalar(select(Message).where(
            Message.provider_message_id == event.message_id))
        if msg is not None:
            now = __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc)
            if event.event_type == "delivered":
                if msg.status in (MessageStatus.sent, MessageStatus.draft,
                                  MessageStatus.pending_approval):
                    msg.status = MessageStatus.delivered
                    msg.provider_delivered_at = now
            elif event.event_type == "bounced":
                msg.status = MessageStatus.failed
                msg.provider_failed_at = now
                msg.provider_error = "bounced"
                db.add(SuppressionRecord(
                    workspace_id=msg.workspace_id, channel=msg.channel,
                    value=(msg.conversation.lead.email
                           if msg.channel == Channel.email else
                           msg.conversation.lead.phone) or "",
                    reason="email bounced"))
            elif event.event_type == "complained":
                msg.status = MessageStatus.failed
                msg.provider_failed_at = now
                msg.provider_error = "complaint"
                db.add(SuppressionRecord(
                    workspace_id=msg.workspace_id, channel=msg.channel,
                    value=(msg.conversation.lead.email
                           if msg.channel == Channel.email else
                           msg.conversation.lead.phone) or "",
                    reason="spam complaint"))
            elif event.event_type == "failed":
                msg.status = MessageStatus.failed
                msg.provider_failed_at = now
                msg.provider_error = "provider failure"
            result = {"status": "processed", "event": event.event_type,
                      "matched": True}
        else:
            result = {"status": "processed", "event": event.event_type,
                      "matched": False}
        we.processed = True
        db.commit()
        return result
    if event.event_type == "received" and event.from_address and event.body_text:
        wid = _resolve_workspace(db, event.to_address)
        if wid is not None:
            result = handle_inbound(
                db, wid, channel=Channel.email,
                from_address=event.from_address, to_address=event.to_address,
                subject=event.subject, body=event.body_text,
                external_id=event.event_id, thread_id=event.thread_id,
            )
        else:
            result = {"status": "unmatched_workspace"}
    we.processed = True
    db.commit()
    return result
