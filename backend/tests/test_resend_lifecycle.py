"""Workstream 5 — Resend email lifecycle tests.

All webhook calls are SIGNED with a test svix secret (mocked provider —
no live Resend validation). Covers delivery/bounce/complaint/failure
events, duplicates, bad signatures, unknown IDs, cross-workspace safety,
inbound quarantine and dedupe.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest

SECRET = "whsec_" + base64.b64encode(b"testsecret").decode()


def _sign(body: bytes, svix_id: str = "msg_x"):
    ts = str(int(time.time()))
    msg = f"{svix_id}.{ts}.{body.decode()}".encode()
    sig = base64.b64encode(
        hmac.new(b"testsecret", msg, hashlib.sha256).digest()).decode()
    return {"svix-id": svix_id, "svix-timestamp": ts,
            "svix-signature": f"v1,{sig}",
            "content-type": "application/json"}


def _post(client, payload: dict, svix_id: str = "msg_x", bad_sig: bool = False):
    body = json.dumps(payload).encode()
    h = _sign(body, svix_id)
    if bad_sig:
        h["svix-signature"] = "v1,badsig"
    return client.post("/api/v1/webhooks/email/resend", content=body, headers=h)


def _env(monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")


def _outbound_msg(db, wid, provider_id="re_123"):
    """Create a sent outbound email Message with provider id."""
    from app.models import (Channel, Conversation, Lead, Message,
                            MessageDirection, MessageStatus)
    lead = Lead(workspace_id=wid, business_name="L", email="l@x.com",
                source="manual")
    db.add(lead); db.flush()
    conv = Conversation(workspace_id=wid, lead_id=lead.id,
                        channel=Channel.email, state="contacted")
    db.add(conv); db.flush()
    m = Message(workspace_id=wid, conversation_id=conv.id,
                channel=Channel.email, direction=MessageDirection.outbound,
                status=MessageStatus.sent, subject="hi", body="hello",
                provider_message_id=provider_id)
    db.add(m); db.flush()
    return m


def _event(etype, email_id="re_123", eid="evt_1", **data):
    d = {"email_id": email_id, "from": "us@agw.com",
         "to": ["lead@x.com"], "subject": "s"}
    d.update(data)
    return {"type": etype, "id": eid, "data": d}


def test_delivered_event_updates_message(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        m = _outbound_msg(db, wid)
        mid = m.id
        db.commit()
    finally:
        db.close()
    r = _post(client, _event("email.delivered"))
    assert r.status_code == 200 and r.json()["status"] == "processed"
    db = SessionLocal()
    try:
        m = db.get(__import__('app.models', fromlist=['Message']).Message, mid)
        assert m.status.value == "delivered"
        assert m.provider_delivered_at is not None
        conv = db.get(__import__('app.models', fromlist=['Conversation']).Conversation,
                      m.conversation_id)
        assert conv.state == "engaged"
    finally:
        db.close()


def test_bounced_event_suppresses(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.models import SuppressionRecord
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        m = _outbound_msg(db, wid, provider_id="re_b1")
        mid = m.id
        db.commit()
    finally:
        db.close()
    r = _post(client, _event("email.bounced", email_id="re_b1", eid="evt_b1"))
    assert r.json()["status"] == "processed"
    db = SessionLocal()
    try:
        m = db.get(__import__('app.models', fromlist=['Message']).Message, mid)
        assert m.status.value == "failed" and m.provider_error == "bounced"
        sup = db.scalar(sa.select(SuppressionRecord).where(
            SuppressionRecord.workspace_id == wid,
            SuppressionRecord.value == "l@x.com"))
        assert sup is not None and "bounced" in sup.reason
    finally:
        db.close()


def test_complained_event_suppresses_and_flags(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        m = _outbound_msg(db, wid, provider_id="re_c1")
        conv_id = m.conversation_id
        db.commit()
    finally:
        db.close()
    r = _post(client, _event("email.complained", email_id="re_c1", eid="evt_c1"))
    assert r.json()["matched"] is True
    db = SessionLocal()
    try:
        conv = db.get(__import__('app.models', fromlist=['Conversation']).Conversation,
                      conv_id)
        assert conv.needs_human is True and conv.state == "complained"
    finally:
        db.close()


def test_failed_event_marks_failed(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        m = _outbound_msg(db, wid, provider_id="re_f1")
        mid = m.id
        db.commit()
    finally:
        db.close()
    _post(client, _event("email.failed", email_id="re_f1", eid="evt_f1"))
    db = SessionLocal()
    try:
        m = db.get(__import__('app.models', fromlist=['Message']).Message, mid)
        assert m.status.value == "failed"
    finally:
        db.close()


def test_unknown_provider_id_safe(client, auth_client, monkeypatch):
    _env(monkeypatch)
    r = _post(client, _event("email.delivered", email_id="re_UNKNOWN",
                             eid="evt_u1"))
    assert r.status_code == 200
    assert r.json()["matched"] is False  # unknown id -> no crash, honest


def test_duplicate_webhook_delivery(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        _outbound_msg(db, wid, provider_id="re_d1")
        db.commit()
    finally:
        db.close()
    payload = _event("email.delivered", email_id="re_d1", eid="evt_dup")
    r1 = _post(client, payload, svix_id="m1")
    r2 = _post(client, payload, svix_id="m1")  # same svix id+body
    assert r1.json().get("status") in ("processed", "received")
    assert r2.json().get("status") == "duplicate"


def test_invalid_signature_rejected(client, auth_client, monkeypatch):
    _env(monkeypatch)
    r = _post(client, _event("email.delivered", eid="evt_sig"), bad_sig=True)
    assert r.status_code == 401


def test_cross_workspace_message_not_matched(client, auth_client, monkeypatch):
    """A lifecycle event for ws A's provider id must not touch ws B."""
    _env(monkeypatch)
    _, h1, o1 = auth_client
    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@x.com", "password": "password123",
        "workspace_name": "WS2"})
    h2 = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    wid2 = uuid.UUID(client.get("/api/v1/auth/me", headers=h2).json()["workspace_id"])
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        m2 = _outbound_msg(db, wid2, provider_id="re_ws2")
        mid2 = m2.id
        db.commit()
    finally:
        db.close()
    # event arrives — lookup is global by provider id (provider ids are
    # globally unique per Resend account); ws attribution comes from the
    # stored Message row, never from the payload.
    _post(client, _event("email.bounced", email_id="re_ws2", eid="evt_xw"))
    db = SessionLocal()
    try:
        m2 = db.get(__import__('app.models', fromlist=['Message']).Message, mid2)
        assert m2.status.value == "failed"  # correct ws's message updated
        # ws1 has no such message -> nothing leaked
        from app.models import Message
        n1 = len(db.scalars(__import__('sqlalchemy').select(Message).where(
            Message.workspace_id == uuid.UUID(o1["workspace_id"]))).all())
        assert all(getattr(x, "provider_message_id", None) != "re_ws2"
                   for x in db.scalars(__import__('sqlalchemy').select(Message).where(
                       Message.workspace_id == uuid.UUID(o1["workspace_id"]))).all())
    finally:
        db.close()


def test_inbound_quarantine_when_workspace_unmapped(client, auth_client, monkeypatch):
    """Inbound email to an address no workspace owns -> quarantined."""
    _env(monkeypatch)
    payload = {"type": "email.received", "id": "evt_q1",
               "data": {"from": "prospect@cafe.com",
                        "to": ["nobody-configured@agw.com"],
                        "subject": "hi", "text": "interested"}}
    r = _post(client, payload, svix_id="mq1")
    assert r.status_code == 200
    assert r.json()["status"] == "quarantined"


def test_inbound_duplicate_guard(client, auth_client, monkeypatch):
    """Same provider message id cannot create two conversation messages."""
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    # configure inbound address for this workspace
    r = client.patch("/api/v1/workspaces/current",
                     json={"inbound_email": "in@agw.com"}, headers=headers)
    assert r.status_code == 200, r.text
    from app.core.database import SessionLocal
    from tests.conftest import make_lead
    # create lead matching sender so handle_inbound matches
    client.post("/api/v1/leads", json={
        "business_name": "Cafe In", "email": "prospect@cafe.com",
        "source": "manual"}, headers=headers)
    payload = {"type": "email.received", "id": "evt_in1",
               "data": {"email_id": "re_in_1", "from": "prospect@cafe.com",
                        "to": ["in@agw.com"], "subject": "hi",
                        "text": "yes interested"}}
    r1 = _post(client, payload, svix_id="mi1")
    assert r1.status_code == 200
    assert r1.json().get("matched") is True
    # different webhook event id, SAME provider email id -> dedupe guard
    payload2 = dict(payload); payload2["id"] = "evt_in2"
    r2 = _post(client, payload2, svix_id="mi2")
    assert r2.json().get("status") == "duplicate_inbound"
    db = SessionLocal()
    try:
        from app.models import Message
        n = len(db.scalars(__import__('sqlalchemy').select(Message).where(
            Message.workspace_id == wid,
            Message.provider_message_id == "re_in_1")).all())
        assert n == 1
    finally:
        db.close()


def test_campaign_stats_bounce_rollup(client, auth_client, monkeypatch):
    _env(monkeypatch)
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    # campaign containing the lead
    lead = client.post("/api/v1/leads", json={
        "business_name": "Cafe C", "email": "c@x.com",
        "source": "manual"}, headers=headers).json()
    camp = client.post("/api/v1/campaigns", json={
        "name": "Camp", "lead_ids": [lead["id"]], "channel": "email"},
        headers=headers)
    assert camp.status_code in (200, 201), camp.text
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        # link message's conversation lead to campaign lead
        from app.models import Conversation, Lead, Message
        import sqlalchemy as sa
        ld = db.get(Lead, uuid.UUID(lead["id"]))
        conv = Conversation(workspace_id=wid, lead_id=ld.id,
                            channel=__import__('app.models', fromlist=['Channel']).Channel.email,
                            state="contacted")
        db.add(conv); db.flush()
        m = Message(workspace_id=wid, conversation_id=conv.id,
                    channel=__import__('app.models', fromlist=['Channel']).Channel.email,
                    direction=__import__('app.models', fromlist=['MessageDirection']).MessageDirection.outbound,
                    status=__import__('app.models', fromlist=['MessageStatus']).MessageStatus.sent,
                    body="x", provider_message_id="re_cmp")
        db.add(m); db.commit()
    finally:
        db.close()
    rr = _post(client, _event("email.bounced", email_id="re_cmp", eid="evt_cmp"))
    assert rr.json().get("matched") is True, rr.json()
    db = SessionLocal()
    try:
        from app.models import Campaign
        c = db.scalar(__import__('sqlalchemy').select(Campaign).where(
            Campaign.workspace_id == wid))
        assert (c.stats or {}).get("bounced") == 1
    finally:
        db.close()
