"""Phase 6 tests: Sales Manager metrics, funnel, bottlenecks, recommendations,
reports, workspace isolation, API endpoints. LLM calls are mocked/heuristic."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_lead


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _wid(owner):
    return uuid.UUID(owner["workspace_id"])


def test_manager_endpoints(auth_client):
    client, headers, owner = auth_client
    for path in ("/manager/overview", "/manager/agents/performance",
                 "/manager/funnel", "/manager/intelligence", "/manager/issues",
                 "/manager/recommendations", "/manager/reports"):
        r = client.get(f"/api/v1{path}", headers=headers)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


def test_performance_metrics_real_records(auth_client):
    from app.services import manager_metrics
    client, headers, owner = auth_client
    make_lead(client, headers)
    db = _db()
    try:
        perf = manager_metrics.agent_performance(db, _wid(owner))
        assert set(perf.keys()) == {
            "lead-researcher", "sales-executive", "communication-manager",
            "executive-assistant", "sales-manager"}
        lr = perf["lead-researcher"]["metrics"]
        assert lr["businesses_discovered"]["quality"] == "exact"
        # all five agents have metric dicts with quality flags
        for slug, p in perf.items():
            assert p["metrics"], slug
            for k, m in p["metrics"].items():
                assert "value" in m and "quality" in m
    finally:
        db.close()


def test_funnel_counts_and_rates(auth_client):
    from app.services import manager_metrics
    client, headers, owner = auth_client
    make_lead(client, headers)
    db = _db()
    try:
        f = manager_metrics.funnel(db, _wid(owner))
        stages = {s["stage"]: s for s in f["stages"]}
        assert stages["discovered"]["count"] >= 1
        assert "definitions" in f and "notes" in f
        # conversion denominators documented
        assert stages["qualified"]["conversion_from_previous"] is None or \
            stages["qualified"]["conversion_from_previous"] >= 0
    finally:
        db.close()


def test_bottleneck_detection_and_dedupe(auth_client):
    from app.services import manager_engine
    from app.models import SalesIssue, IssueStatus
    import sqlalchemy as sa
    client, headers, owner = auth_client
    wid = _wid(owner)
    db = _db()
    try:
        first = manager_engine.detect_bottlenecks(db, wid)
        db.commit()
        # provider-unavailable informational issues exist (no creds in tests)
        types = {i.issue_type for i in first}
        assert "email_provider_unavailable" in types
        n1 = len(db.scalars(sa.select(SalesIssue).where(
            SalesIssue.workspace_id == wid)).all())
        # second run must not duplicate open issues
        manager_engine.detect_bottlenecks(db, wid)
        db.commit()
        n2 = len(db.scalars(sa.select(SalesIssue).where(
            SalesIssue.workspace_id == wid)).all())
        assert n1 == n2
    finally:
        db.close()


def test_recommendation_generation_and_accept(auth_client):
    from app.services import manager_engine
    client, headers, owner = auth_client
    wid = _wid(owner)
    db = _db()
    try:
        manager_engine.detect_bottlenecks(db, wid)
        recs = manager_engine.generate_recommendations(db, wid)
        db.commit()
        assert len(recs) > 0
        # idempotent
        again = manager_engine.generate_recommendations(db, wid)
        assert len(again) == 0
    finally:
        db.close()
    # accept via API creates a task for the related agent
    r = client.get("/api/v1/manager/recommendations?status=new&page_size=5",
                   headers=headers).json()
    assert r["total"] > 0
    rec_id = r["items"][0]["id"]
    ra = client.post(f"/api/v1/manager/recommendations/{rec_id}/accept",
                     headers=headers)
    assert ra.status_code == 200
    body = ra.json()
    assert body["recommendation"]["status"] == "accepted"


def test_issue_lifecycle(auth_client):
    client, headers, owner = auth_client
    r = client.get("/api/v1/manager/issues?page_size=10", headers=headers).json()
    if not r["items"]:
        pytest.skip("no issues in fresh workspace")
    iid = r["items"][0]["id"]
    for action in ("acknowledge", "resolve"):
        rr = client.post(f"/api/v1/manager/issues/{iid}/{action}", headers=headers)
        assert rr.status_code == 200
        assert rr.json()["status"] in ("acknowledged", "resolved")


def test_daily_and_weekly_reports(auth_client):
    client, headers, owner = auth_client
    rd = client.post("/api/v1/manager/reports/generate",
                     json={"report_type": "daily"}, headers=headers)
    assert rd.status_code == 201, rd.text
    payload = rd.json()["payload"]
    for k in ("new_leads", "outbound_sent", "inbound_replies", "demos_booked",
              "conversions", "period"):
        assert k in payload
    # idempotent: same period returns existing
    rd2 = client.post("/api/v1/manager/reports/generate",
                      json={"report_type": "daily"}, headers=headers)
    assert rd2.status_code == 201
    rw = client.post("/api/v1/manager/reports/generate",
                     json={"report_type": "weekly"}, headers=headers)
    assert rw.status_code == 201
    assert "funnel" in rw.json()["payload"]
    assert "previous_period" in rw.json()["payload"]


def test_worker_idempotency(auth_client):
    from app.services import manager_engine
    client, headers, owner = auth_client
    wid = _wid(owner)
    db = _db()
    try:
        r1 = manager_engine.run_analysis(db, wid, kind="scan")
        db.commit()
        r2 = manager_engine.run_analysis(db, wid, kind="scan")
        db.commit()
        assert r2.get("skipped") is True
    finally:
        db.close()


def test_workspace_isolation(auth_client):
    client, headers_a, _ = auth_client
    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@mgr-iso.com", "password": "password123",
        "workspace_name": "Other WS"})
    hb = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get("/api/v1/manager/issues", headers=hb).json()["total"] == 0
    assert client.get("/api/v1/manager/reports", headers=hb).json()["total"] == 0
    assert client.get("/api/v1/manager/overview", headers=hb).json()["total_leads"] == 0


def test_intelligence_heuristic_fallback(auth_client):
    """No LLM configured in tests -> deterministic heuristic insights."""
    client, headers, owner = auth_client
    r = client.get("/api/v1/manager/intelligence", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "heuristic"
    assert "observations" in body and "data_gaps" in body
    # evidence must cite real aggregated metrics
    assert "funnel" in body["evidence"] and "agent_metrics" in body["evidence"]


def test_analytics_audit_fields(auth_client):
    """Analytics uses appointment records for meetings and real revenue."""
    client, headers, owner = auth_client
    r = client.get("/api/v1/analytics/overview", headers=headers)
    assert r.status_code == 200
    body = r.json()
    for k in ("meetings_booked", "meetings_completed", "revenue_won"):
        assert k in body


def test_sales_manager_cannot_grant_permissions(auth_client):
    """No endpoint allows an agent to modify permissions — verified by absence."""
    client, headers, _ = auth_client
    # there is no /manager route that writes agent config; confirm 404
    r = client.post("/api/v1/manager/agents/lead-researcher/permissions",
                    json={"autonomous": True}, headers=headers)
    assert r.status_code in (404, 405)
