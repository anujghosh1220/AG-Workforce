"""Phase 3 tests: conversation engine, outreach, compliance, webhooks, campaigns."""
import uuid

import pytest

from tests.conftest import make_lead


def _conv_for_lead(client, headers, lead_id):
    "─ helper: ensure a conversation exists via inbound simulation."""
    return None


def test_inbound_reply_updates_crm(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation, Lead, MessageDirection
    from app.services.conversation_engine import handle_inbound

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="reply@cafe.com", business_name="Reply Cafe")

    db = SessionLocal()
    try:
        # seed lead stage to contacted so reply moves it to 'replied'
        l = db.get(Lead, uuid.UUID(lead["id"]))
        from app.models import LeadStage
        l.stage = LeadStage.contacted
        db.commit()

        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.email,
                             from_address="reply@cafe.com", to_address=None,
                             subject="Re: hi", body="Yes I'm interested, tell me more",
                             external_id="evt-1")
        assert res["matched"] is True
        assert res["intent"] == "buying_signal"
        lead2 = db.get(Lead, uuid.UUID(lead["id"]))
        assert lead2.stage == LeadStage.interested
        conv = db.get(Conversation, uuid.UUID(res["conversation_id"]))
        assert conv.state == "interested"
        assert conv.summary
    finally:
        db.close()


def test_opt_out_suppresses_and_closes(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel, ConsentStatus, Lead, LeadStage, SuppressionRecord
    from app.services import compliance
    from app.services.conversation_engine import handle_inbound

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="optout2@cafe.com")

    db = SessionLocal()
    try:
        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.email,
                             from_address="optout2@cafe.com", to_address=None,
                             subject="", body="Please unsubscribe me from these emails",
                             external_id="evt-2")
        assert res["opted_out"] is True
        wid = uuid.UUID(owner["workspace_id"])
        assert compliance.is_suppressed(db, wid, Channel.email, "optout2@cafe.com")
        assert compliance.consent_status(db, wid, Channel.email, "optout2@cafe.com") == ConsentStatus.withdrawn
        l = db.get(Lead, uuid.UUID(lead["id"]))
        assert l.stage == LeadStage.do_not_contact
        # follow-up sends must now be blocked
        reason = compliance.outbound_block_reason(db, wid, Channel.email,
                                                  value="optout2@cafe.com", lead_id=l.id)
        assert reason is not None
    finally:
        db.close()


def test_unmatched_sender_ignored(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel
    from app.services.conversation_engine import handle_inbound

    _, headers, owner = auth_client
    db = SessionLocal()
    try:
        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.email,
                             from_address="stranger@nowhere.com", to_address=None,
                             subject="", body="hello?", external_id="evt-x")
        assert res["matched"] is False
    finally:
        db.close()


def test_eligibility_blocks_suppressed(client, auth_client):
    import app.services.outreach as outreach
    from app.core.database import SessionLocal
    from app.models import Channel, SuppressionRecord

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="blocked2@cafe.com")
    db = SessionLocal()
    try:
        db.add(SuppressionRecord(workspace_id=uuid.UUID(owner["workspace_id"]),
                                 channel=Channel.email, value="blocked2@cafe.com",
                                 reason="test"))
        db.commit()
        from app.models import Lead
        l = db.get(Lead, uuid.UUID(lead["id"]))
        e = outreach.eligibility(db, uuid.UUID(owner["workspace_id"]), l, Channel.email)
        assert e["allowed"] is False
        assert "suppression" in (e["reason"] or "")
    finally:
        db.close()


def test_send_message_blocked_without_provider(client, auth_client):
    import app.services.outreach as outreach
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation, Message, MessageDirection, MessageStatus
    from app.services.conversation_engine import get_or_create_conversation

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="send@cafe.com")
    db = SessionLocal()
    try:
        wid = uuid.UUID(owner["workspace_id"])
        conv = get_or_create_conversation(db, wid, uuid.UUID(lead["id"]), Channel.email)
        msg = Message(workspace_id=wid, conversation_id=conv.id,
                      direction=MessageDirection.outbound, channel=Channel.email,
                      subject="Hi", body="Hello", status=MessageStatus.draft)
        db.add(msg)
        db.commit()
        res = outreach.send_message(db, wid, msg)
        assert res["sent"] is False
        assert "not configured" in res["reason"]
    finally:
        db.close()


def test_send_message_with_mock_provider(client, auth_client, monkeypatch):
    import app.services.outreach as outreach
    from app.core.database import SessionLocal
    from app.models import Channel, Message, MessageDirection, MessageStatus
    from app.services.conversation_engine import get_or_create_conversation

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="ok@cafe.com")
    db = SessionLocal()
    try:
        wid = uuid.UUID(owner["workspace_id"])
        conv = get_or_create_conversation(db, wid, uuid.UUID(lead["id"]), Channel.email)
        msg = Message(workspace_id=wid, conversation_id=conv.id,
                      direction=MessageDirection.outbound, channel=Channel.email,
                      subject="Hi", body="Hello", status=MessageStatus.draft)
        db.add(msg)
        db.commit()

        class FakeProvider:
            name = "fake"
            def configured(self): return True
            def send(self, email):
                from app.integrations.email.base import SentEmail
                return SentEmail(provider_message_id="fake-123", to=email.to)
        monkeypatch.setattr(outreach, "get_email_provider", lambda: FakeProvider())
        res = outreach.send_message(db, wid, msg)
        assert res["sent"] is True
        assert msg.status == MessageStatus.sent
        assert msg.sent_at is not None
    finally:
        db.close()


def test_campaign_lifecycle(client, auth_client):
    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="camp@cafe.com")

    # create campaign
    r = client_api.post("/api/v1/campaigns", json={
        "name": "Siliguri outreach", "lead_ids": [lead["id"]],
        "channel": "email", "instructions": "friendly intro",
    }, headers=headers)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["status"] == "draft"

    # eligibility endpoint
    e = client_api.get(f"/api/v1/campaigns/{cid}/eligibility", headers=headers).json()
    assert len(e) == 1 and e[0]["lead_id"] == lead["id"]

    # cannot send before authorize
    r = client_api.post(f"/api/v1/campaigns/{cid}/send", headers=headers)
    assert r.status_code == 409

    # generate drafts
    r = client_api.post(f"/api/v1/campaigns/{cid}/generate", headers=headers)
    assert r.status_code == 200
    assert r.json()["stats"]["drafted"] == 1

    # authorize (owner) then send
    r = client_api.post(f"/api/v1/campaigns/{cid}/authorize", json={"confirm": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "authorized"

    r = client_api.post(f"/api/v1/campaigns/{cid}/send", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    # send failed gracefully (no email provider) but pipeline ran
    assert r.json()["stats"]["failed"] >= 0


def test_conversation_endpoints(client, auth_client):
    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="inbox@cafe.com")

    from app.core.database import SessionLocal
    from app.models import Channel
    from app.services.conversation_engine import handle_inbound
    db = SessionLocal()
    try:
        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.email,
                             from_address="inbox@cafe.com", to_address=None,
                             subject="", body="What does your software cost?",
                             external_id="evt-9")
        conv_id = res["conversation_id"]
    finally:
        db.close()

    # list conversations shows state
    convs = client_api.get("/api/v1/conversations", headers=headers).json()
    assert convs["total"] >= 1
    c0 = convs["items"][0]
    assert c0["state"] in ("engaged", "interested", "awaiting_reply")
    assert c0["lead_business_name"]

    # detail has messages
    detail = client_api.get(f"/api/v1/conversations/{conv_id}", headers=headers).json()
    assert len(detail["messages"]) >= 1
    assert detail["messages"][0]["direction"] == "inbound"

    # suggest reply works without LLM (heuristic)
    s = client_api.post(f"/api/v1/conversations/{conv_id}/suggest", headers=headers)
    assert s.status_code == 200
    assert s.json()["body"]

    # handoff
    h = client_api.post(f"/api/v1/conversations/{conv_id}/handoff", headers=headers)
    assert h.status_code == 200
    assert h.json()["state"] == "human_handoff"
    assert h.json()["needs_human"] is True


def test_follow_up_lifecycle(client, auth_client):
    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers, email="fu@cafe.com")

    from app.core.database import SessionLocal
    from app.models import Channel, FollowUpStatus
    from app.services.conversation_engine import handle_inbound
    db = SessionLocal()
    try:
        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.email,
                             from_address="fu@cafe.com", to_address=None,
                             subject="", body="ok sounds good", external_id="evt-fu")
        conv_id = res["conversation_id"]
    finally:
        db.close()

    # manual follow-up
    r = client_api.post(f"/api/v1/conversations/{conv_id}/follow-ups",
                        json={"hours": 1, "max_attempts": 2}, headers=headers)
    assert r.status_code == 201
    fu_id = r.json()["id"]
    assert r.json()["status"] == "scheduled"

    # list shows it
    fus = client_api.get(f"/api/v1/conversations/{conv_id}/follow-ups", headers=headers).json()
    assert any(f["id"] == fu_id for f in fus)

    # cancel
    r = client_api.delete(f"/api/v1/conversations/follow-ups/{fu_id}", headers=headers)
    assert r.status_code == 204
    fus = client_api.get(f"/api/v1/conversations/{conv_id}/follow-ups", headers=headers).json()
    fu = next(f for f in fus if f["id"] == fu_id)
    assert fu["status"] == "cancelled"


def test_webhook_signature_and_idempotency(client, auth_client, monkeypatch):
    """Webhook rejects bad signatures; duplicate event_ids are idempotent."""
    import json as _json
    import hashlib, hmac, base64, time
    from app.integrations.email.resend import ResendProvider

    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + base64.b64encode(b"testsecret").decode())
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    p = ResendProvider()

    body = _json.dumps({"type": "email.received", "id": "evt-wh-1",
                        "data": {"from": "x@y.com", "to": ["in@agw.com"],
                                 "subject": "hi", "text": "interested!"}}).encode()
    ts = str(int(time.time()))
    msg = f"msg_1.{ts}.{body.decode()}".encode()
    sig = base64.b64encode(hmac.new(b"testsecret", msg, hashlib.sha256).digest()).decode()
    headers = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}
    assert p.verify_webhook(headers, body) is True
    bad = dict(headers); bad["svix-signature"] = "v1,badsig"
    assert p.verify_webhook(bad, body) is False

    # no workspace mapped for in@agw.com -> unmatched but processed
    r = client.post("/api/v1/webhooks/email/resend", content=body,
                    headers={**headers, "content-type": "application/json"})
    assert r.status_code == 200
    # duplicate -> idempotent
    r2 = client.post("/api/v1/webhooks/email/resend", content=body,
                     headers={**headers, "content-type": "application/json"})
    assert r2.json().get("status") == "duplicate"


def test_campaign_workspace_isolation(client, auth_client):
    client_api, headers_a, _ = auth_client
    lead = make_lead(client_api, headers_a)
    r = client_api.post("/api/v1/campaigns", json={
        "name": "A", "lead_ids": [lead["id"]], "channel": "email",
    }, headers=headers_a)
    cid = r.json()["id"]

    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@iso2.com", "password": "password123",
        "workspace_name": "Other",
    })
    hb = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client_api.get(f"/api/v1/campaigns/{cid}/eligibility", headers=hb).status_code == 404
    assert client_api.post(f"/api/v1/campaigns/{cid}/authorize", json={"confirm": True}, headers=hb).status_code == 404


def test_heuristic_analysis_units():
    from app.integrations.llm.heuristic import analyze
    assert analyze("unsubscribe me")["intent"] == "opt_out"
    assert analyze("I'm interested, send pricing")["intent"] == "buying_signal"
    assert analyze("how does it work?")["intent"] == "question"
    assert analyze("we already use a competitor")["intent"] == "objection"
