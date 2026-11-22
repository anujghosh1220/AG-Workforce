"""Inbound email webhook — signature-verified, idempotent."""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.email.base import get_email_provider
from app.models import Channel, Conversation, Message, MessageStatus, WebhookEvent
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
        from app.models import SuppressionRecord
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
                if msg.conversation_id:
                    conv = db.get(Conversation, msg.conversation_id)
                    if conv and conv.state in ("contacted", "new"):
                        conv.state = "engaged"
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
                # rollup: conversation + campaign stats
                if msg.conversation_id:
                    conv = db.get(Conversation, msg.conversation_id)
                    if conv:
                        conv.needs_human = True
                _bump_campaign_stats(db, msg, "bounced")
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
                if msg.conversation_id:
                    conv = db.get(Conversation, msg.conversation_id)
                    if conv:
                        conv.needs_human = True
                        conv.state = "complained"
                _bump_campaign_stats(db, msg, "complained")
            elif event.event_type == "failed":
                msg.status = MessageStatus.failed
                msg.provider_failed_at = now
                msg.provider_error = "provider failure"
                _bump_campaign_stats(db, msg, "failed")
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
        if wid is None:
            # Quarantine: never guess a workspace for ambiguous inbound mail.
            we.payload = {**(we.payload or {}),
                          "quarantined": True,
                          "reason": "unmatched_or_ambiguous_workspace",
                          "to": event.to_address}
            result = {"status": "quarantined",
                      "reason": "no unique workspace for recipient"}
        else:
            # duplicate-inbound guard: same provider message id already
            # recorded in this workspace -> do not create a second message
            if event.message_id:
                dup = db.scalar(select(Message.id).where(
                    Message.workspace_id == wid,
                    Message.provider_message_id == event.message_id))
                if dup is not None:
                    result = {"status": "duplicate_inbound"}
                    we.processed = True
                    db.commit()
                    return result
            result = handle_inbound(
                db, wid, channel=Channel.email,
                from_address=event.from_address, to_address=event.to_address,
                subject=event.subject, body=event.body_text,
                external_id=event.event_id, thread_id=event.thread_id,
            )
            # record provider id on the created inbound message for dedupe
            if event.message_id and result.get("message_id"):
                im = db.get(Message, uuid.UUID(result["message_id"]))
                if im is not None and not im.provider_message_id:
                    im.provider_message_id = event.message_id
    we.processed = True
    db.commit()
    return result


def _bump_campaign_stats(db: Session, msg, key: str) -> None:
    """Increment campaign.stats[key] for campaigns containing this lead.
    stats is a JSON dict; campaigns store lead_ids as a JSON list."""
    from app.models import Campaign, Conversation
    conv = db.get(Conversation, msg.conversation_id) if msg.conversation_id else None
    lead = conv.lead if conv else None
    if lead is None:
        return
    lid = str(lead.id)
    for c in db.scalars(select(Campaign).where(
            Campaign.workspace_id == msg.workspace_id)).all():
        ids = [str(x) for x in (c.lead_ids or [])]
        if lid in ids:
            stats = dict(c.stats or {})
            stats[key] = int(stats.get(key, 0)) + 1
            c.stats = stats
