import os
import tempfile
import uuid

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"
os.environ["SECRET_KEY"] = "test-secret-key-for-tests-only"
os.environ["ENVIRONMENT"] = "test"
os.environ["AGW_DISABLE_WORKER"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_client(client):
    """Returns (client, headers, user, workspace_id) for a fresh registered user."""
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "password123",
        "full_name": "Test User",
        "workspace_name": f"WS {uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    return client, headers, me.json()


def make_lead(client, headers, **overrides):
    payload = {"business_name": f"Cafe {uuid.uuid4().hex[:6]}", "email": "owner@cafe.com"}
    payload.update(overrides)
    resp = client.post("/api/v1/leads", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
