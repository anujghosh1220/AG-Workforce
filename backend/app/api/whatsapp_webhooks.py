"""WhatsApp Business Cloud API webhooks — Meta GET verification + POST events."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.whatsapp.base import get_whatsapp_provider
from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
from app.models import Channel, Message, MessageDirection, MessageStatus, WebhookEvent
from app.services.conversation_engine import handle_inbound

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp-webhooks"])


@router.get("")
def verify_webhook(hub_mode: str | None = Query(None, alias="hub.mode"),
                   hub_token: str | None = Query(None, alias="hub.verify_token"),
                   hub_challenge: str | None = Query(None, alias="hub.challenge")):
    p = MetaCloudProvider()
    challenge = p.verify_challenge(hub_mode, hub_token, hub_challenge)
    if challenge is None:
        from fastapi import HTTPException
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@router.post("")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    p = get_whatsapp_provider()
    if p is None:
        return {"status": "ignored", "reason": "provider not configured"}
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not p.verify_signature(raw, sig):
        from fastapi import HTTPException
        raise HTTPException(401, "invalid signature")
    import json
    try:
        payload = json.loads(raw)
    except Exception:
        return {"status": "ignored", "reason": "bad json"}

    results = []
    for ev in p.parse_events(payload):
        # idempotency
        existing = db.scalar(select(WebhookEvent).where(
            WebhookEvent.provider == "meta_cloud", WebhookEvent.event_id == ev.event_id))
        if existing:
            results.append({"event_id": ev.event_id, "status": "duplicate"})
            continue
        we = WebhookEvent(provider="meta_cloud", event_id=ev.event_id,
                          event_type=ev.kind, payload=ev.raw or {}, processed=False)
        db.add(we)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            results.append({"event_id": ev.event_id, "status": "duplicate"})
            continue

        if ev.kind == "message" and ev.from_phone and ev.text:
            # resolve workspace: match lead by phone across workspaces is unsafe;
            # use phone-number-id -> workspace mapping via Workspace.settings
            from app.models import Workspace
            wid = None
            phone_id = os_phone_id = None
            import os as _os
            pid = _os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
            for w in db.scalars(select(Workspace)).all():
                st = getattr(w, "settings", None) or {}
                if st.get("whatsapp_phone_number_id") == pid:
                    wid = w.id
                    break
            if wid is not None:
                r = handle_inbound(db, wid, channel=Channel.whatsapp,
                                   from_address=ev.from_phone, to_address=None,
                                   subject=None, body=ev.text,
                                   external_id=ev.event_id, thread_id=ev.message_id)
                results.append({"event_id": ev.event_id, "status": "processed", **r})
            else:
                results.append({"event_id": ev.event_id, "status": "unmatched_workspace"})
        elif ev.kind == "status" and ev.message_id:
            # map provider lifecycle onto the local Message (monotonic:
            # sent -> delivered -> read; failed is terminal)
            msg = db.scalar(select(Message).where(
                Message.provider_message_id == ev.message_id))
            if msg is not None:
                rank = {MessageStatus.sent: 1, MessageStatus.delivered: 2,
                        MessageStatus.read: 3}
                new_st = {"delivered": MessageStatus.delivered,
                          "read": MessageStatus.read,
                          "sent": MessageStatus.sent,
                          "failed": MessageStatus.failed}.get(ev.status or "")
                if new_st == MessageStatus.failed:
                    msg.status = MessageStatus.failed
                    msg.provider_failed_at = datetime.now(timezone.utc)
                    msg.provider_error = (ev.error or "")[:300] or None
                elif new_st in rank and rank.get(msg.status, 0) < rank[new_st]:
                    msg.status = new_st
                    if new_st == MessageStatus.delivered:
                        msg.provider_delivered_at = datetime.now(timezone.utc)
                    elif new_st == MessageStatus.read:
                        msg.provider_read_at = datetime.now(timezone.utc)
                results.append({"event_id": ev.event_id, "status": "receipt",
                                "receipt": ev.status, "matched": True})
            else:
                results.append({"event_id": ev.event_id, "status": "receipt",
                                "receipt": ev.status, "matched": False})
        elif ev.kind == "template_status" and ev.raw:
            # Meta message_template_status_update -> sync local template
            raw = ev.raw or {}
            tpl_id = str(raw.get("message_template_id") or "")
            new_status = str(raw.get("event") or "").upper()
            reason = raw.get("reason") or None
            ts = raw.get("timestamp")
            from app.models import MessageTemplate
            q = select(MessageTemplate).where(
                MessageTemplate.channel == Channel.whatsapp)
            if tpl_id:
                q = q.where(MessageTemplate.provider_template_id == tpl_id)
            else:
                q = q.where(MessageTemplate.name == str(raw.get("message_template_name") or ""))
            cands = db.scalars(q).all()
            if len(cands) == 1:
                t = cands[0]
                smap = {"APPROVED": "approved", "REJECTED": "rejected",
                        "PENDING": "pending", "PAUSED": "paused",
                        "DISABLED": "disabled"}
                internal = smap.get(new_status)
                if internal:
                    # out-of-order protection: Meta statuses are roughly
                    # monotonic in review flow; never let an older event
                    # revert a newer terminal/provider-confirmed state.
                    rank = {"pending": 0, "approved": 1, "rejected": 1,
                            "paused": 2, "disabled": 2}
                    if rank.get(internal, 0) >= rank.get(t.provider_status, 0):
                        t.provider_status = internal
                        t.is_active = internal == "approved"
                        if reason:
                            t.rejection_reason = str(reason)[:1000]
                        if ts:
                            try:
                                t.last_synced_at = datetime.fromtimestamp(
                                    int(ts), tz=timezone.utc)
                            except Exception:
                                t.last_synced_at = datetime.now(timezone.utc)
                results.append({"event_id": ev.event_id, "status": "template_update",
                                "matched": True, "new_status": internal})
            else:
                # unmatched or ambiguous -> quarantine, never guess workspace
                results.append({"event_id": ev.event_id,
                                "status": "quarantined",
                                "reason": "template_not_uniquely_matched",
                                "candidates": len(cands)})
        else:
            results.append({"event_id": ev.event_id, "status": "ignored"})
        we.processed = True
    db.commit()
    return {"results": results}
