import uuid

from tests.conftest import make_lead


def test_register_and_login(client):
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post("/api/v1/auth/register", json={
        "email": email, "password": "password123", "full_name": "A", "workspace_name": "WS",
    })
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    assert token

    login = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_login_invalid_credentials(client):
    resp = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    assert resp.status_code == 401

    email = f"{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email, "password": "password123", "workspace_name": "WS",
    })
    bad = client.post("/api/v1/auth/login", json={"email": email, "password": "wrongpass"})
    assert bad.status_code == 401


def test_register_duplicate_email(client):
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "password": "password123", "workspace_name": "WS"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert client.post("/api/v1/auth/register", json=body).status_code == 409


def test_protected_route_requires_auth(client):
    assert client.get("/api/v1/leads").status_code == 401
    assert client.get("/api/v1/agents").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_and_workspace(auth_client):
    client, headers, user = auth_client
    assert user["email"]
    ws = client.get("/api/v1/workspaces/current", headers=headers)
    assert ws.status_code == 200
    assert ws.json()["id"] == user["workspace_id"]


def test_workspace_isolation(auth_client, client):
    """Resources created by workspace A must not be visible to workspace B."""
    _, headers_a, _ = auth_client
    lead = make_lead(client, headers_a)

    # second workspace
    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password": "password123",
        "workspace_name": "Other WS",
    })
    headers_b = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    assert client.get(f"/api/v1/leads/{lead['id']}", headers=headers_b).status_code == 404
    leads_b = client.get("/api/v1/leads", headers=headers_b).json()
    assert leads_b["total"] == 0
    assert client.delete(f"/api/v1/leads/{lead['id']}", headers=headers_b).status_code == 404
    assert client.patch(f"/api/v1/leads/{lead['id']}/stage",
                        json={"stage": "won"}, headers=headers_b).status_code == 404
