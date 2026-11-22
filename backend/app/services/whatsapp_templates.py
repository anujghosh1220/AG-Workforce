"""WhatsApp template synchronization + approved-template sending (Phase 8.8).

Templates are synced from the Meta WhatsApp Business Account and stored in
MessageTemplate. Provider status is authoritative — a template is never
treated as approved unless Meta reports APPROVED.

Service-window rule (Meta policy):
- Inside 24h of the lead's last inbound message: free-form text allowed.
- Outside: only an explicitly selected APPROVED template may be sent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.whatsapp.base import WhatsAppMessage, get_whatsapp_provider
from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
from app.models import (
    Channel, Conversation, Lead, LeadStage, Message, MessageDirection,
    MessageStatus, MessageTemplate,
)
from app.services import compliance

_STATUS_MAP = {
    "PENDING": "pending", "APPROVED": "approved", "REJECTED": "rejected",
    "PAUSED": "paused", "DISABLED": "disabled",
}


class TemplateError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------ sync


def sync_templates(db: Session, wid: uuid.UUID) -> dict:
    """Pull all templates from Meta for this workspace's WABA and upsert.
    Idempotent: matched on (workspace, channel=whatsapp, name, language,
    provider_template_id). Never fabricates approval status."""
    provider = MetaCloudProvider()
    try:
        result = provider.list_templates()
    except RuntimeError as exc:
        if "not_configured" in str(exc):
            raise TemplateError("not_configured",
                                "WhatsApp provider credentials not set")
        raise TemplateError("provider_error", str(exc)[:300])

    synced, created, updated = 0, 0, 0
    seen: set[tuple] = set()
    for t in result["templates"]:
        name = t.get("name")
        lang = (t.get("language") or "en")[:10]
        pid = str(t.get("id") or "")
        if not name:
            continue
        key = (name, lang, pid)
        seen.add(key)
        status = _STATUS_MAP.get(str(t.get("status") or "").upper(), "pending")
        # never upgrade local state from provider data alone
        row = db.scalar(select(MessageTemplate).where(
            MessageTemplate.workspace_id == wid,
            MessageTemplate.channel == Channel.whatsapp,
            MessageTemplate.name == name,
            MessageTemplate.language == lang))
        if row is None and pid:
            row = db.scalar(select(MessageTemplate).where(
                MessageTemplate.workspace_id == wid,
                MessageTemplate.channel == Channel.whatsapp,
                MessageTemplate.provider_template_id == pid))
        if row is None:
            row = MessageTemplate(
                workspace_id=wid, name=name, channel=Channel.whatsapp,
                body="", language=lang)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.provider_template_id = pid or row.provider_template_id
        row.provider_status = status
        row.provider_category = t.get("category")
        row.components = {"components": t.get("components") or [],
                          "sub_category": t.get("sub_category")}
        row.rejection_reason = t.get("rejected_reason")
        row.last_synced_at = _now()
        row.is_active = status == "approved"
        synced += 1
    db.flush()
    return {"synced": synced, "created": created, "updated": updated,
            "provider_count": result["count"]}


# ------------------------------------------------------------ helpers


def _within_service_window(db: Session, conv: Conversation | None) -> bool:
    if conv is None:
        return False
    last_in = db.scalar(select(Message.created_at).where(
        Message.conversation_id == conv.id,
        Message.direction == MessageDirection.inbound,
    ).order_by(Message.created_at.desc()).limit(1))
    return last_in is not None and (_now() - last_in) < timedelta(hours=24)


def _validate_components(components: list | None) -> list:
    """Validate Meta template component payloads. Returns cleaned list."""
    if components is None:
        return []
    if not isinstance(components, list):
        raise TemplateError("invalid_components", "components must be a list")
    out = []
    for comp in components[:10]:  # Meta caps components
        if not isinstance(comp, dict):
            raise TemplateError("invalid_components", "component must be object")
        ctype = comp.get("type")
        if ctype not in ("header", "body", "footer", "button"):
            raise TemplateError("invalid_components", f"bad component type {ctype}")
        params = comp.get("parameters") or []
        if not isinstance(params, list) or len(params) > 20:
            raise TemplateError("invalid_parameters",
                                "parameters must be a list (max 20)")
        for p in params:
            if not isinstance(p, dict) or "type" not in p:
                raise TemplateError("invalid_parameters",
                                    "parameter missing type")
            if p["type"] == "text" and not isinstance(p.get("text"), str):
                raise TemplateError("invalid_parameters",
                                    "text parameter must be a string")
            if len(str(p.get("text", ""))) > 1024:
                raise TemplateError("invalid_parameters",
                                    "parameter value too long")
        out.append({"type": ctype,
                    "parameters": [{"type": p.get("type"),
                                    **({"text": str(p.get("text", ""))[:1024]}
                                       if p.get("type") == "text" else {})}
                                   for p in params]})
    return out


def send_approved_template(db: Session, wid: uuid.UUID, *,
                           lead: Lead, to_phone: str,
                           template: MessageTemplate,
                           components: list | None = None,
                           conv: Conversation | None = None,
                           actor_id=None) -> dict:
    """Send an APPROVED WhatsApp template. Enforces workspace ownership,
    provider approval, parameter validation and immediate compliance
    recheck. Returns {"sent": bool, ...}."""
    if template.workspace_id != wid or template.channel != Channel.whatsapp:
        raise TemplateError("not_found", "template not found")
    if template.provider_status != "approved":
        raise TemplateError("template_not_approved",
                            f"provider status is {template.provider_status}")
    if not template.provider_template_id:
        raise TemplateError("not_synced",
                            "template has no provider_template_id — sync first")
    comps = _validate_components(components)

    # compliance recheck immediately before send
    reason = compliance.outbound_block_reason(
        db, wid, Channel.whatsapp, value=to_phone, lead_id=lead.id)
    if reason:
        return {"sent": False, "reason": reason, "status": "blocked"}

    provider = get_whatsapp_provider()
    if provider is None:
        return {"sent": False, "reason": "whatsapp provider not configured",
                "status": "not_configured"}

    try:
        res = provider.send_template(WhatsAppMessage(
            to=to_phone, body="",
            template_name=template.name,
            template_language=template.language or "en",
            template_components=comps or None))
    except Exception as exc:
        return {"sent": False, "reason": str(exc)[:300], "status": "failed"}

    if not res.provider_message_id:
        return {"sent": False, "reason": "provider did not confirm",
                "status": "failed"}

    # persist the outbound message record
    msg = Message(
        workspace_id=wid, conversation_id=conv.id if conv else None,
        channel=Channel.whatsapp,
        direction=MessageDirection.outbound, status=MessageStatus.sent,
        subject=None,
        body=f"[template:{template.name}]",
        provider_message_id=res.provider_message_id,
        sent_at=_now(), is_ai_draft=False,
    )
    db.add(msg)
    if conv and conv.state == "new":
        conv.state = "contacted"
    if lead.stage in (LeadStage.researched, LeadStage.qualified,
                      LeadStage.discovered):
        lead.stage = LeadStage.contacted
    lead.last_contacted_at = _now()
    db.flush()
    return {"sent": True, "message_id": str(msg.id),
            "provider_message_id": res.provider_message_id,
            "template": template.name}
