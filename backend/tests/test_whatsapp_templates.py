"""Workstream 4 — WhatsApp template lifecycle tests.

ALL Meta Graph API calls are MOCKED (httpx monkeypatch / fake provider).
No live Meta validation is claimed. ENVIRONMENT=test disables auth
rate limits; external creds absent -> not_configured paths tested.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _env(monkeypatch, **kw):
    for k in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
              "WHATSAPP_BUSINESS_ACCOUNT_ID", "WHATSAPP_APP_SECRET",
              "WHATSAPP_VERIFY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def _mk_template(db, wid, **kw):
    from app.models import Channel, MessageTemplate
    t = MessageTemplate(workspace_id=wid, channel=Channel.whatsapp,
                        name=kw.get("name", "hello_world"),
                        body="", language=kw.get("language", "en"),
                        provider_template_id=kw.get("provider_template_id", "pt_1"),
                        provider_status=kw.get("provider_status", "approved"),
                        provider_category=kw.get("provider_category", "UTILITY"),
                        components=kw.get("components", {}),
                        rejection_reason=kw.get("rejection_reason"),
                        last_synced_at=kw.get("last_synced_at"))
    db.add(t)
    db.flush()
    return t


# ------------------------------------------------ Meta adapter (mocked)


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._p


def test_list_templates_paginated(monkeypatch):
    """Mocked: two pages of templates are both returned."""
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba")
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if "after" not in str(params):
            return _FakeResp({"data": [
                {"id": "t1", "name": "a", "language": "en",
                 "category": "UTILITY", "status": "APPROVED",
                 "components": []},
            ], "paging": {"next": "https://graph.facebook.com/v21.0/waba/message_templates?after=CUR"}})
        return _FakeResp({"data": [
            {"id": "t2", "name": "b", "language": "en",
             "category": "MARKETING", "status": "PENDING",
             "components": []},
        ]})

    monkeypatch.setattr("httpx.get", fake_get)
    p = MetaCloudProvider()
    res = p.list_templates()
    assert res["count"] == 2
    assert {t["name"] for t in res["templates"]} == {"a", "b"}
    assert len(calls) == 2  # pagination followed


def test_list_templates_not_configured(monkeypatch):
    _env(monkeypatch)
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    with pytest.raises(RuntimeError, match="not_configured"):
        MetaCloudProvider().list_templates()


def test_list_templates_error_and_malformed(monkeypatch):
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba")
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider

    monkeypatch.setattr("httpx.get",
                        lambda *a, **k: _FakeResp({}, status=500))
    with pytest.raises(RuntimeError, match="500"):
        MetaCloudProvider().list_templates()

    monkeypatch.setattr("httpx.get",
                        lambda *a, **k: _FakeResp({"unexpected": 1}))
    with pytest.raises(RuntimeError, match="malformed"):
        MetaCloudProvider().list_templates()


# ------------------------------------------------ sync service


def test_sync_templates_status_mapping(client, auth_client, monkeypatch):
    """Mocked Meta -> local upsert with exact status mapping."""
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba")
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.services import whatsapp_templates as wt

    payload = {"data": [
        {"id": "p1", "name": "t_ok", "language": "en", "category": "UTILITY",
         "status": "APPROVED", "components": [{"type": "body"}]},
        {"id": "p2", "name": "t_rej", "language": "en", "category": "MARKETING",
         "status": "REJECTED", "rejected_reason": "INVALID_FORMAT",
         "components": []},
        {"id": "p3", "name": "t_pen", "language": "en", "category": "UTILITY",
         "status": "PENDING", "components": []},
        {"id": "p4", "name": "t_pause", "language": "en", "category": "UTILITY",
         "status": "PAUSED", "components": []},
        {"id": "p5", "name": "t_dis", "language": "en", "category": "UTILITY",
         "status": "DISABLED", "components": []},
    ]}
    monkeypatch.setattr("httpx.get",
                        lambda *a, **k: _FakeResp(payload))
    db = SessionLocal()
    try:
        res = wt.sync_templates(db, wid)
        db.commit()
        assert res["synced"] == 5 and res["created"] == 5
        from app.models import MessageTemplate
        rows = {r.name: r for r in db.scalars(
            __import__('sqlalchemy').select(MessageTemplate).where(
                MessageTemplate.workspace_id == wid)).all()}
        assert rows["t_ok"].provider_status == "approved"
        assert rows["t_ok"].provider_template_id == "p1"
        assert rows["t_rej"].provider_status == "rejected"
        assert rows["t_rej"].rejection_reason == "INVALID_FORMAT"
        assert rows["t_pen"].provider_status == "pending"
        assert rows["t_pause"].provider_status == "paused"
        assert rows["t_dis"].provider_status == "disabled"
        # re-sync -> idempotent, no duplicates
        res2 = wt.sync_templates(db, wid)
        db.commit()
        assert res2["created"] == 0 and res2["synced"] == 5
        n = len(db.scalars(__import__('sqlalchemy').select(MessageTemplate).where(
            MessageTemplate.workspace_id == wid)).all())
        assert n == 5
    finally:
        db.close()


def test_sync_not_configured_raises(client, auth_client, monkeypatch):
    _env(monkeypatch)  # no creds
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.services import whatsapp_templates as wt
    db = SessionLocal()
    try:
        with pytest.raises(wt.TemplateError) as ei:
            wt.sync_templates(db, wid)
        assert ei.value.code == "not_configured"
    finally:
        db.close()


# ------------------------------------------------ send validation


def _seed_lead_conv(db, wid):
    from tests.conftest import make_lead
    import tests.conftest as cf
    return None  # helper replaced below


def test_send_template_approved_mocked(client, auth_client, monkeypatch):
    """Mocked Meta send_template -> provider_message_id persisted."""
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba")
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    # create lead with phone via API
    lead = client.post("/api/v1/leads", json={
        "business_name": "Cafe X", "phone": "+919876543210",
        "source": "manual"}, headers=headers)
    assert lead.status_code == 201, lead.text
    lead_id = lead.json()["id"]
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation, MessageTemplate
    db = SessionLocal()
    try:
        t = _mk_template(db, wid, name="hello_world",
                         provider_status="approved",
                         provider_template_id="pt_1")
        # conversation so compliance sees context
        from app.models import Lead
        ld = db.get(Lead, uuid.UUID(lead_id))
        conv = Conversation(workspace_id=wid, lead_id=ld.id,
                            channel=Channel.whatsapp, state="new")
        db.add(conv); db.flush()
        # fake provider send
        import app.integrations.whatsapp.base as wbase
        class FakeProv:
            name = "meta_cloud"
            def configured(self): return True
            def send_template(self, m):
                from app.integrations.whatsapp.base import WhatsAppResult
                return WhatsAppResult(provider_message_id="wamid.TPL1")
        monkeypatch.setattr("app.services.whatsapp_templates.get_whatsapp_provider",
                            lambda: FakeProv())
        from app.services import whatsapp_templates as wt
        res = wt.send_approved_template(
            db, wid, lead=ld, to_phone=ld.phone, template=t, conv=conv)
        assert res["sent"] is True
        assert res["provider_message_id"] == "wamid.TPL1"
        db.commit()
        from app.models import Message
        m = db.scalar(__import__('sqlalchemy').select(Message).where(
            Message.provider_message_id == "wamid.TPL1"))
        assert m is not None and m.status.value == "sent"
    finally:
        db.close()


def test_send_unapproved_blocked(client, auth_client, monkeypatch):
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    client.post("/api/v1/leads", json={
        "business_name": "Cafe Y", "phone": "+919876543211",
        "source": "manual"}, headers=headers)
    from app.core.database import SessionLocal
    from app.models import Channel, Lead, MessageTemplate
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        t = _mk_template(db, wid, name="pend_tpl",
                         provider_status="pending",
                         provider_template_id="pt_2")
        ld = db.scalar(sa.select(Lead).where(Lead.workspace_id == wid))
        from app.services import whatsapp_templates as wt
        with pytest.raises(wt.TemplateError) as ei:
            wt.send_approved_template(db, wid, lead=ld,
                                      to_phone=ld.phone, template=t)
        assert ei.value.code == "template_not_approved"
        # rejected also blocked
        t.provider_status = "rejected"
        db.flush()
        with pytest.raises(wt.TemplateError):
            wt.send_approved_template(db, wid, lead=ld,
                                      to_phone=ld.phone, template=t)
    finally:
        db.close()


def test_send_invalid_components_blocked(client, auth_client, monkeypatch):
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    client.post("/api/v1/leads", json={
        "business_name": "Cafe Z", "phone": "+919876543212",
        "source": "manual"}, headers=headers)
    from app.core.database import SessionLocal
    from app.models import Channel, Lead, MessageTemplate
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        t = _mk_template(db, wid, provider_status="approved")
        ld = db.scalar(sa.select(Lead).where(Lead.workspace_id == wid))
        from app.services import whatsapp_templates as wt
        with pytest.raises(wt.TemplateError) as ei:
            wt.send_approved_template(
                db, wid, lead=ld, to_phone=ld.phone, template=t,
                components=[{"type": "evil", "parameters": []}])
        assert ei.value.code == "invalid_components"
        with pytest.raises(wt.TemplateError) as ei2:
            wt.send_approved_template(
                db, wid, lead=ld, to_phone=ld.phone, template=t,
                components=[{"type": "body",
                             "parameters": [{"type": "text"}]}])
        assert ei2.value.code == "invalid_parameters"
    finally:
        db.close()


def test_cross_workspace_template_blocked(client, auth_client):
    """A template from workspace B cannot be sent for workspace A's lead."""
    _, h1, o1 = auth_client
    c2, h2, o2 = client, None, None
    # second workspace via register
    reg = client.post("/api/v1/auth/register", json={
        "email": "other@test.com", "password": "password123",
        "full_name": "Other", "workspace_name": "Other WS"})
    assert reg.status_code == 201
    tok2 = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    wid1 = uuid.UUID(o1["workspace_id"])
    wid2 = uuid.UUID(reg.json() and
                     client.get("/api/v1/auth/me", headers=tok2).json()["workspace_id"])
    client.post("/api/v1/leads", json={
        "business_name": "Cafe W", "phone": "+919876543213",
        "source": "manual"}, headers=h1)
    from app.core.database import SessionLocal
    from app.models import Channel, Lead, MessageTemplate
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        t2 = _mk_template(db, wid2, name="foreign_tpl",
                          provider_status="approved")
        ld1 = db.scalar(sa.select(Lead).where(Lead.workspace_id == wid1))
        from app.services import whatsapp_templates as wt
        # send using ws1 context but ws2's template -> not_found
        with pytest.raises(wt.TemplateError) as ei:
            wt.send_approved_template(db, wid1, lead=ld1,
                                      to_phone=ld1.phone, template=t2)
        assert ei.value.code == "not_found"
    finally:
        db.close()


# ------------------------------------------------ webhook template_status


def _signed_post(client, payload: dict, secret: str):
    import hashlib, hmac, json
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/v1/webhooks/whatsapp", content=body,
                       headers={"x-hub-signature-256": sig,
                                "content-type": "application/json"})


def test_template_status_webhook_mapping(client, auth_client, monkeypatch):
    """Mocked webhook: APPROVED event updates matching template only."""
    secret = "whsec"
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba",
         WHATSAPP_APP_SECRET=secret, WHATSAPP_VERIFY_TOKEN="vt")
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.models import Channel, MessageTemplate
    db = SessionLocal()
    try:
        t = _mk_template(db, wid, name="wh_tpl",
                         provider_status="pending",
                         provider_template_id="pt_w1")
        db.commit()
    finally:
        db.close()
    payload = {"entry": [{"changes": [{"value": {
        "message_template_status_update": [{
            "message_template_id": "pt_w1",
            "message_template_name": "wh_tpl",
            "event": "APPROVED",
            "timestamp": "1700000000"}]}}]}]}
    r = _signed_post(client, payload, secret)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == "template_update"
    db = SessionLocal()
    try:
        t = db.scalar(__import__('sqlalchemy').select(MessageTemplate).where(
            MessageTemplate.provider_template_id == "pt_w1"))
        assert t.provider_status == "approved" and t.is_active is True
        assert t.last_synced_at is not None
    finally:
        db.close()


def test_template_status_webhook_bad_signature(client, monkeypatch):
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_APP_SECRET="real",
         WHATSAPP_VERIFY_TOKEN="vt")
    payload = {"entry": [{"changes": [{"value": {
        "message_template_status_update": [{
            "message_template_id": "x", "event": "APPROVED"}]}}]}]}
    r = _signed_post(client, payload, "wrong-secret")
    assert r.status_code == 401


def test_template_status_webhook_duplicate_and_out_of_order(
        client, auth_client, monkeypatch):
    """Duplicate event_id is idempotent; out-of-order can't revert APPROVED."""
    secret = "whsec"
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_BUSINESS_ACCOUNT_ID="waba",
         WHATSAPP_APP_SECRET=secret, WHATSAPP_VERIFY_TOKEN="vt")
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.models import MessageTemplate
    db = SessionLocal()
    try:
        _mk_template(db, wid, name="oo_tpl", provider_status="approved",
                     provider_template_id="pt_oo")
        db.commit()
    finally:
        db.close()
    # PENDING event arriving after APPROVED must NOT revert (rank guard)
    payload = {"entry": [{"changes": [{"value": {
        "message_template_status_update": [{
            "message_template_id": "pt_oo", "message_template_name": "oo_tpl",
            "event": "PENDING", "timestamp": "1700000001"}]}}]}]}
    r = _signed_post(client, payload, secret)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        t = db.scalar(__import__('sqlalchemy').select(MessageTemplate).where(
            MessageTemplate.provider_template_id == "pt_oo"))
        assert t.provider_status == "approved"  # not reverted
    finally:
        db.close()
    # same payload again -> duplicate (event_id = tpl_id+event, distinct per event)
    r2 = _signed_post(client, payload, secret)
    assert r2.status_code == 200
    assert r2.json()["results"][0]["status"] == "duplicate"


def test_template_status_webhook_unmatched_quarantined(client, monkeypatch):
    secret = "whsec"
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid", WHATSAPP_APP_SECRET=secret,
         WHATSAPP_VERIFY_TOKEN="vt")
    payload = {"entry": [{"changes": [{"value": {
        "message_template_status_update": [{
            "message_template_id": "pt_UNKNOWN",
            "message_template_name": "nope",
            "event": "APPROVED"}]}}]}]}
    r = _signed_post(client, payload, secret)
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "quarantined"


# ------------------------------------------------ outreach outside-window


def test_outside_window_template_required(client, auth_client, monkeypatch):
    """Free-form WhatsApp outside 24h -> template_required, no send."""
    _env(monkeypatch, WHATSAPP_ACCESS_TOKEN="tok",
         WHATSAPP_PHONE_NUMBER_ID="pid")
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    client.post("/api/v1/leads", json={
        "business_name": "Cafe OW", "phone": "+919876543214",
        "source": "manual"}, headers=headers)
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation, Lead, Message
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        ld = db.scalar(sa.select(Lead).where(Lead.workspace_id == wid))
        conv = Conversation(workspace_id=wid, lead_id=ld.id,
                            channel=Channel.whatsapp, state="contacted")
        db.add(conv); db.flush()
        msg = Message(workspace_id=wid, conversation_id=conv.id,
                      channel=Channel.whatsapp,
                      direction=__import__('app.models', fromlist=['MessageDirection']).MessageDirection.outbound,
                      status=__import__('app.models', fromlist=['MessageStatus']).MessageStatus.pending_approval,
                      body="hello")
        db.add(msg); db.commit()
        from app.services import outreach
        res = outreach.send_message(db, wid, msg)  # no template_name
        assert res["sent"] is False
        assert res.get("status") == "template_required"
    finally:
        db.close()
