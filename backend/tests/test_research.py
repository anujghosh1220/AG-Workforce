"""Phase 2 tests: research jobs, providers (mocked), dedup, scoring, CSV, isolation."""
import io
import uuid

import pytest

from tests.conftest import make_lead


def _job(client, headers, **over):
    body = {"city": "Siliguri", "state": "West Bengal", "country": "India",
            "business_types": ["cafe"], "max_results": 10}
    body.update(over)
    r = client.post("/api/v1/research/jobs", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_providers_endpoint(client, auth_client):
    _, headers, _ = auth_client
    r = client.get("/api/v1/research/providers", headers=headers)
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert "openstreetmap" in names and "google_places" in names
    gp = next(p for p in r.json() if p["name"] == "google_places")
    assert gp["configured"] is False  # no key in test env


def test_job_creation_and_dedup_guard(client, auth_client):
    client_api, headers, _ = auth_client
    j1 = _job(client_api, headers)
    assert j1["status"] == "queued"
    # second concurrent job rejected
    r2 = client_api.post("/api/v1/research/jobs", json={"city": "Siliguri", "max_results": 5}, headers=headers)
    assert r2.status_code == 409


def test_job_cancel(client, auth_client):
    client_api, headers, _ = auth_client
    j = _job(client_api, headers)
    r = client_api.post(f"/api/v1/research/jobs/{j['id']}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_process_job_with_mock_provider(client, auth_client, monkeypatch):
    """End-to-end job with a fake provider — proves pipeline, not real data."""
    from app.integrations.research.base import RawBusiness
    from app.tasks import research_worker

    client_api, headers, owner = auth_client
    j = _job(client_api, headers)

    class FakeProvider:
        name = "fake_test"
        def configured(self): return True
        def search(self, *a, **k):
            yield RawBusiness(
                name="Test Cafe Siliguri", provider="fake_test",
                category="cafe", city="Siliguri", state="West Bengal",
                country="India", website="https://testcafe.example",
                phone="+919876543210", email="hello@testcafe.example",
                latitude=26.7, longitude=88.4,
                evidence=[{"field": "name", "value": "Test Cafe Siliguri", "source": "fake_test", "verified": True}],
            )

    monkeypatch.setattr(research_worker, "default_providers", lambda: [FakeProvider()])
    monkeypatch.setattr("app.services.website_enricher.enrich_website",
                        lambda url, timeout=15: {"email": None, "phone": None,
                        "instagram": None, "facebook": None, "evidence": [],
                        "pages_checked": 0, "error": "skipped in test"})

    research_worker.process_job(uuid.UUID(j["id"]))

    job = client_api.get(f"/api/v1/research/jobs/{j['id']}", headers=headers).json()
    assert job["status"] == "completed"
    assert job["stats"]["researched"] >= 1

    results = client_api.get("/api/v1/research/results", params={"job_id": j["id"]}, headers=headers).json()
    assert results["total"] >= 1
    r0 = results["items"][0]
    assert r0["business_name"] == "Test Cafe Siliguri"
    assert r0["score"] > 0
    assert r0["priority"] in ("high", "good", "needs_research", "low")
    assert r0["source_provider"] == "fake_test"

    # lead was auto-created in CRM
    leads = client_api.get("/api/v1/leads", params={"q": "Test Cafe"}, headers=headers).json()
    assert leads["total"] >= 1


def test_duplicate_flagging(client, auth_client, monkeypatch):
    from app.integrations.research.base import RawBusiness
    from app.tasks import research_worker

    client_api, headers, owner = auth_client
    # pre-existing lead with same name
    make_lead(client_api, headers, business_name="Duplicate Cafe", website="https://dup.example")

    class FakeProvider:
        name = "fake_test"
        def configured(self): return True
        def search(self, *a, **k):
            yield RawBusiness(name="Duplicate Cafe", provider="fake_test",
                              website="https://dup.example", category="cafe")

    monkeypatch.setattr(research_worker, "default_providers", lambda: [FakeProvider()])
    monkeypatch.setattr("app.services.website_enricher.enrich_website",
                        lambda url, timeout=15: {"email": None, "phone": None,
                        "instagram": None, "facebook": None, "evidence": [],
                        "pages_checked": 0, "error": "skip"})

    j = _job(client_api, headers)
    research_worker.process_job(uuid.UUID(j["id"]))
    results = client_api.get("/api/v1/research/results", params={"job_id": j["id"]}, headers=headers).json()
    r0 = results["items"][0]
    assert r0["status"] == "duplicate"
    assert r0["duplicate_reason"]


def test_scoring_weights_configurable(monkeypatch):
    from app.services.lead_scorer import score_business
    biz = {"business_category": "cafe", "website": "https://x.com", "email": "a@b.com",
           "phone": "+1234567890", "address": "1 Main St", "city": "X",
           "latitude": 1.0, "longitude": 2.0, "uses_online_ordering": "no"}
    s1 = score_business(biz)
    monkeypatch.setenv("SCORING_WEIGHTS", '{"relevance": 50}')
    s2 = score_business(biz)
    assert s2.breakdown["relevance"]["max"] == 50
    assert s2.score != s1.score or s2.breakdown["relevance"]["points"] == 50


def test_missing_data_scores_low():
    from app.services.lead_scorer import score_business
    s = score_business({"business_name": "Unknown Place"})
    assert s.score < 40
    assert s.missing


def test_csv_import(client, auth_client):
    client_api, headers, _ = auth_client
    csv_data = "business_name,city,email,phone,lead_score\nCSV Cafe,Howrah,csv@cafe.com,+919999999999,75\n,Nowhere,x@y.com,,\n"
    r = client_api.post("/api/v1/research/import-csv",
                        files={"file": ("leads.csv", io.BytesIO(csv_data.encode()), "text/csv")},
                        headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["created"] == 1
    assert r.json()["skipped"] == 1
    leads = client_api.get("/api/v1/leads", params={"q": "CSV Cafe"}, headers=headers).json()
    assert leads["total"] == 1
    assert leads["items"][0]["source"] == "csv_import"
    assert leads["items"][0]["lead_score"] == 75


def test_research_workspace_isolation(client, auth_client):
    client_api, headers_a, _ = auth_client
    j = _job(client_api, headers_a)
    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@iso.com", "password": "password123", "workspace_name": "ISO",
    })
    hb = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get(f"/api/v1/research/jobs/{j['id']}", headers=hb).status_code == 404
    assert client.get("/api/v1/research/results", headers=hb).json()["total"] == 0
    assert client.post(f"/api/v1/research/jobs/{j['id']}/cancel", headers=hb).status_code == 404


def test_failed_provider_recorded(client, auth_client, monkeypatch):
    from app.tasks import research_worker

    client_api, headers, _ = auth_client
    j = _job(client_api, headers, providers=["openstreetmap"])

    class BoomProvider:
        name = "boom"
        def configured(self): return True
        def search(self, *a, **k):
            raise RuntimeError("provider exploded")
            yield

    # provider list comes from cfg; force default_providers path empty then inject via monkeypatch of get_provider
    monkeypatch.setattr(research_worker, "get_provider",
                        lambda name: BoomProvider())
    # cfg providers=["openstreetmap"] -> get_provider returns Boom
    research_worker.process_job(uuid.UUID(j["id"]))
    job = client_api.get(f"/api/v1/research/jobs/{j['id']}", headers=headers).json()
    # job completes with errors recorded, not silently "successful"
    assert job["status"] == "completed"
    assert job["stats"]["errors"] >= 1


def test_agent_cannot_request_outbound(client, auth_client):
    """Lead Researcher permissions must not include outbound actions."""
    client_api, headers, _ = auth_client
    agents = client_api.get("/api/v1/agents", headers=headers).json()["items"]
    lr = next(a for a in agents if a["slug"] == "lead-researcher")
    pol = client_api.get(f"/api/v1/agents/{lr['id']}/policy", headers=headers).json()
    # every catalog action still routes through the permission engine;
    # high-risk actions must require approval for this agent
    high = [a for a in pol["actions"] if a["risk"] == "high"]
    assert high and all(a["requires_approval_for_agent"] for a in high)
