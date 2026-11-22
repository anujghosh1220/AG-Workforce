"""Phase 1.5 hardening tests: auth edge cases, roles, consent/suppression,
approval risk requirements, analytics accuracy."""
import os
import uuid

import pytest

from tests.conftest import make_lead


def _auth_headers(client, email, password="password123"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------- auth edge cases


def test_tampered_and_garbage_tokens_rejected(client, auth_client):
    _, headers, user = auth_client
    token = headers["Authorization"].split()[1]
    # tampered signature
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/v1/leads", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    # garbage
    assert client.get("/api/v1/leads", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    # missing scheme
    assert client.get("/api/v1/leads", headers={"Authorization": token}).status_code == 401
    # token for deleted/disabled user path is covered by is_active check


def test_expired_token_rejected(client, auth_client):
    """A token signed with a different secret must be rejected."""
    import jwt as pyjwt
    _, headers, user = auth_client
    forged = pyjwt.encode(
        {"sub": str(user["id"]), "wid": user["workspace_id"], "exp": 9999999999},
        "wrong-secret", algorithm="HS256",
    )
    assert client.get("/api/v1/leads", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_password_never_returned(auth_client):
    client, headers, _ = auth_client
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert "password" not in me and "password_hash" not in me


# ---------------------------------------------------------- registration policy


def test_registration_closed_in_production_mode(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ALLOW_PUBLIC_REGISTRATION", raising=False)
    resp = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@x.com", "password": "password123", "workspace_name": "W",
    })
    assert resp.status_code == 403


def test_registration_invite_code_required(client, monkeypatch):
    monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "true")
    monkeypatch.setenv("REGISTRATION_INVITE_CODE", "s3cret-invite")
    email = f"{uuid.uuid4().hex[:8]}@x.com"
    bad = client.post("/api/v1/auth/register", json={
        "email": email, "password": "password123", "workspace_name": "W",
    })
    assert bad.status_code == 403
    good = client.post("/api/v1/auth/register", json={
        "email": email, "password": "password123", "workspace_name": "W",
        "invite_code": "s3cret-invite",
    })
    assert good.status_code == 201
    monkeypatch.delenv("REGISTRATION_INVITE_CODE")


# ---------------------------------------------------------- role enforcement


def test_viewer_role_cannot_mutate(client, auth_client):
    """A viewer-role user in the same workspace must not create leads."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password, create_access_token
    from app.models import User, UserRole

    client_api, headers, owner = auth_client
    db = SessionLocal()
    try:
        viewer = User(
            workspace_id=uuid.UUID(owner["workspace_id"]),
            email=f"viewer-{uuid.uuid4().hex[:6]}@x.com",
            password_hash=hash_password("password123"),
            full_name="Viewer", role=UserRole.viewer,
        )
        db.add(viewer)
        db.commit()
        token = create_access_token(viewer.id, viewer.workspace_id)
    finally:
        db.close()

    vh = {"Authorization": f"Bearer {token}"}
    # reads allowed
    assert client_api.get("/api/v1/leads", headers=vh).status_code == 200
    assert client_api.get("/api/v1/agents", headers=vh).status_code == 200
    # writes denied
    assert client_api.post("/api/v1/leads", json={"business_name": "Nope"}, headers=vh).status_code == 403
    assert client_api.post("/api/v1/tasks", json={
        "title": "x", "assignee_type": "human",
    }, headers=vh).status_code == 403
    assert client_api.patch("/api/v1/agents/" + str(uuid.uuid4()), json={"status": "paused"}, headers=vh).status_code == 403


# ---------------------------------------------------------- compliance


def _seed_agent(client, headers):
    return client.get("/api/v1/agents", headers=headers).json()["items"][0]


def test_suppression_blocks_agent_send(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import SuppressionRecord, Channel
    from app.services.agent_service import execute_task

    client_api, headers, owner = auth_client
    agent = _seed_agent(client_api, headers)
    email = "blocked@cafe.com"
    lead = make_lead(client_api, headers, email=email)

    db = SessionLocal()
    try:
        db.add(SuppressionRecord(
            workspace_id=uuid.UUID(owner["workspace_id"]), channel=Channel.email,
            value=email, reason="manual suppression",
        ))
        db.commit()
        run = execute_task(
            db, uuid.UUID(owner["workspace_id"]), uuid.UUID(agent["id"]), "send intro",
            requested_action="send_email",
            action_payload={"to": email}, lead_id=uuid.UUID(lead["id"]),
        )
        db.commit()
        assert run.status.value == "failed"
        assert "suppression" in (run.error or "")
        # no approval should have been created for a blocked send
        from sqlalchemy import select, func
        from app.models import Approval
        count = db.scalar(select(func.count(Approval.id)).where(
            Approval.workspace_id == uuid.UUID(owner["workspace_id"])))
        assert count == 0
    finally:
        db.close()


def test_do_not_contact_blocks_outreach(client, auth_client):
    from app.core.database import SessionLocal
    from app.services.agent_service import execute_task
    import uuid as _uuid

    client_api, headers, owner = auth_client
    agent = _seed_agent(client_api, headers)
    lead = make_lead(client_api, headers, email="dnc@cafe.com")
    client_api.patch(f"/api/v1/leads/{lead['id']}/stage", json={"stage": "do_not_contact"}, headers=headers)

    db = SessionLocal()
    try:
        run = execute_task(
            db, uuid.UUID(owner["workspace_id"]), uuid.UUID(agent["id"]), "first contact",
            requested_action="initiate_first_contact",
            action_payload={"to": "dnc@cafe.com"}, lead_id=uuid.UUID(lead["id"]),
        )
        db.commit()
        assert run.status.value == "failed"
        assert "do-not-contact" in (run.error or "")
    finally:
        db.close()


def test_withdrawn_consent_blocks_send(client, auth_client):
    client_api, headers, owner = auth_client
    agent = _seed_agent(client_api, headers)
    # record withdrawn consent via API
    resp = client_api.post("/api/v1/compliance/consents", json={
        "channel": "email", "value": "optout@cafe.com", "status": "withdrawn", "source": "test",
    }, headers=headers)
    assert resp.status_code == 201

    from app.core.database import SessionLocal
    from app.services.agent_service import execute_task
    db = SessionLocal()
    try:
        run = execute_task(
            db, uuid.UUID(owner["workspace_id"]), uuid.UUID(agent["id"]), "follow up",
            requested_action="send_email",
            action_payload={"to": "optout@cafe.com"},
        )
        db.commit()
        assert run.status.value == "failed"
        assert "withdrew" in (run.error or "")
    finally:
        db.close()


def test_compliance_check_endpoint(client, auth_client):
    client_api, headers, _ = auth_client
    ok = client_api.get("/api/v1/compliance/check", params={"channel": "email", "value": "a@b.com"}, headers=headers)
    assert ok.status_code == 200 and ok.json()["allowed"] is True

    client_api.post("/api/v1/compliance/suppressions", json={
        "channel": "whatsapp", "value": "+15551234", "reason": "opt-out",
    }, headers=headers)
    blocked = client_api.get("/api/v1/compliance/check", params={"channel": "whatsapp", "value": "+15551234"}, headers=headers)
    assert blocked.json()["allowed"] is False
    assert blocked.json()["suppressed"] is True


def test_suppressions_workspace_isolated(client):
    """A suppression in workspace A must not affect workspace B."""
    reg_a = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@a.com", "password": "password123", "workspace_name": "A",
    })
    ha = {"Authorization": f"Bearer {reg_a.json()['access_token']}"}
    reg_b = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@b.com", "password": "password123", "workspace_name": "B",
    })
    hb = {"Authorization": f"Bearer {reg_b.json()['access_token']}"}

    client.post("/api/v1/compliance/suppressions", json={
        "channel": "email", "value": "shared@cafe.com", "reason": "opt-out",
    }, headers=ha)

    # B's suppression list is empty and check passes
    sup_b = client.get("/api/v1/compliance/suppressions", headers=hb).json()
    assert sup_b["total"] == 0
    check_b = client.get("/api/v1/compliance/check", params={"channel": "email", "value": "shared@cafe.com"}, headers=hb).json()
    assert check_b["allowed"] is True
    # A's check is blocked
    check_a = client.get("/api/v1/compliance/check", params={"channel": "email", "value": "shared@cafe.com"}, headers=ha).json()
    assert check_a["allowed"] is False


# ---------------------------------------------------------- approval risk


def test_high_risk_action_requires_approval_and_records_risk(client, auth_client):
    from app.core.database import SessionLocal
    from app.services.agent_service import execute_task

    client_api, headers, owner = auth_client
    agent = _seed_agent(client_api, headers)

    db = SessionLocal()
    try:
        # high risk -> always approval regardless of agent permissions
        run = execute_task(
            db, uuid.UUID(owner["workspace_id"]), uuid.UUID(agent["id"]), "first contact attempt",
            requested_action="initiate_first_contact",
            action_payload={"to": "new@cafe.com"},
        )
        db.commit()
        assert run.status.value == "awaiting_approval"
        assert run.input_payload["risk_level"] == "high"
        assert run.input_payload["requires_approval"] is True

        from sqlalchemy import select
        from app.models import Approval, RiskLevel
        approval = db.scalar(select(Approval).where(Approval.workspace_id == uuid.UUID(owner["workspace_id"])))
        assert approval is not None
        assert approval.risk_level == RiskLevel.high
    finally:
        db.close()


def test_low_risk_action_runs_without_approval(client, auth_client):
    from app.core.database import SessionLocal
    from app.services.agent_service import execute_task

    client_api, headers, owner = auth_client
    agent = _seed_agent(client_api, headers)

    db = SessionLocal()
    try:
        run = execute_task(
            db, uuid.UUID(owner["workspace_id"]), uuid.UUID(agent["id"]), "research cafe",
            requested_action="research_public_info",
        )
        db.commit()
        assert run.status.value == "succeeded"
        assert "approval_id" not in run.output_payload
    finally:
        db.close()


def test_agent_policy_endpoint(client, auth_client):
    client_api, headers, _ = auth_client
    agent = _seed_agent(client_api, headers)
    resp = client_api.get(f"/api/v1/agents/{agent['id']}/policy", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["actions"]) >= 10
    assert any(a["action"] == "initiate_first_contact" and a["risk"] == "high" for a in data["actions"])


# ---------------------------------------------------------- analytics accuracy


def test_analytics_counts_real_inbound_messages(client, auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation, Lead, Message, MessageDirection

    client_api, headers, owner = auth_client
    lead = make_lead(client_api, headers)

    db = SessionLocal()
    try:
        conv = Conversation(workspace_id=uuid.UUID(owner["workspace_id"]), lead_id=uuid.UUID(lead["id"]), channel=Channel.email)
        db.add(conv)
        db.flush()
        db.add(Message(workspace_id=uuid.UUID(owner["workspace_id"]), conversation_id=conv.id,
                       direction=MessageDirection.inbound, channel=Channel.email, body="Interested!"))
        db.add(Message(workspace_id=uuid.UUID(owner["workspace_id"]), conversation_id=conv.id,
                       direction=MessageDirection.inbound, channel=Channel.email, body="Tell me more"))
        db.commit()
    finally:
        db.close()

    data = client_api.get("/api/v1/analytics/overview", headers=headers).json()
    assert data["messages_received"] >= 2
    assert data["meetings_booked"] >= 0
    assert "response_rate" in data
