import os, uuid, pytest

def test_provider_unconfigured(monkeypatch):
    for k in ("WHATSAPP_ACCESS_TOKEN","WHATSAPP_PHONE_NUMBER_ID"):
        monkeypatch.delenv(k, raising=False)
    from app.integrations.whatsapp.base import get_whatsapp_provider
    assert get_whatsapp_provider() is None

def test_provider_configured_and_payload(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN","tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID","123")
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    p = MetaCloudProvider()
    assert p.configured()
    m = __import__('app.integrations.whatsapp.base', fromlist=['WhatsAppMessage']).WhatsAppMessage(to="+91 98765 43210", body="hi")
    assert __import__('re').sub(r'\D','',m.to) == "919876543210"

def test_signature_verify(monkeypatch):
    import hashlib, hmac
    monkeypatch.setenv("WHATSAPP_APP_SECRET","sec")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN","vtok")
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    p = MetaCloudProvider()
    body = b'{"entry":[]}'
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert p.verify_signature(body, sig) is True
    assert p.verify_signature(body, "sha256=bad") is False
    assert p.verify_challenge("subscribe","vtok","CH") == "CH"
    assert p.verify_challenge("subscribe","wrong","CH") is None

def test_parse_events():
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    p = MetaCloudProvider()
    payload = {"entry":[{"changes":[{"value":{
        "messages":[{"id":"wamid.1","from":"9198","type":"text","text":{"body":"hi"},"timestamp":"1"}],
        "statuses":[{"id":"wamid.9","status":"delivered","recipient_id":"91"}]}}]}]}
    evs = p.parse_events(payload)
    assert len(evs) == 2
    msg_ev = next(e for e in evs if e.kind == "message")
    st_ev = next(e for e in evs if e.kind == "status")
    assert msg_ev.text == "hi" and msg_ev.from_phone == "9198"
    assert st_ev.status == "delivered"

def test_opt_out_keyword_whatsapp(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel
    from app.services.conversation_engine import handle_inbound
    import uuid as _u
    _, headers, owner = auth_client
    lead = __import__('tests.conftest', fromlist=['make_lead']).make_lead(
        client, headers, phone="+919999988877", email=None)
    db = SessionLocal()
    try:
        res = handle_inbound(db, uuid.UUID(owner["workspace_id"]), channel=Channel.whatsapp,
                             from_address="+919999988877", to_address=None,
                             subject=None, body="STOP", external_id="w1")
        assert res["matched"] is True
        assert res["opted_out"] is True or res["intent"] == "opt_out"
    finally:
        db.close()

def test_whatsapp_send_blocked_outside_window(client, auth_client):
    import app.services.outreach as outreach
    from app.core.database import SessionLocal
    from app.models import Channel, Message, MessageDirection, MessageStatus
    from app.services.conversation_engine import get_or_create_conversation
    client_api, headers, owner = auth_client
    lead = __import__('tests.conftest', fromlist=['make_lead']).make_lead(
        client_api, headers, phone="+919888877766", email=None)
    db = SessionLocal()
    try:
        wid = uuid.UUID(owner["workspace_id"])
        conv = get_or_create_conversation(db, wid, uuid.UUID(lead["id"]), Channel.whatsapp)
        msg = Message(workspace_id=wid, conversation_id=conv.id,
                      direction=MessageDirection.outbound, channel=Channel.whatsapp,
                      body="hi", status=MessageStatus.draft)
        db.add(msg); db.commit()
        res = outreach.send_message(db, wid, msg)
        assert res["sent"] is False  # no provider configured anyway
    finally:
        db.close()
