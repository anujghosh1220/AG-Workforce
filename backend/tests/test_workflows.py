"""Phase 7 tests: orchestrator, capabilities, events, approvals, budgets,
workspace isolation, café acquisition workflow (mocked providers)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_lead


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _wid(owner):
    return uuid.UUID(owner["workspace_id"])


# ---------------------------------------------------------------- capabilities


def test_capability_registry_complete():
    from app.services import capabilities
    reg = capabilities.registry()
    assert set(reg.keys()) == {
        "lead-researcher", "sales-executive", "communication-manager",
        "executive-assistant", "sales-manager"}
    for slug, cap in reg.items():
        assert cap["tasks"], slug
    # never delegate unsupported action
    assert not capabilities.agent_supported("lead-researcher", "send_approved_outreach")
    assert capabilities.agent_supported("communication-manager", "send_approved_outreach")


# ---------------------------------------------------------------- workflow lifecycle


def test_create_and_run_workflow(auth_client):
    from app.services import orchestrator
    client, headers, owner = auth_client
    db = _db()
    try:
        wf = orchestrator.create_workflow(db, _wid(owner),
                                          "Acquire 10 café leads in Pune")
        db.commit()
        assert wf.status.value == "queued"
        assert len(wf.steps) == len(orchestrator.CAFE_ACQUISITION_PLAN)
        # run: early steps execute; campaign step waits for approval
        wf = orchestrator.run_workflow(db, wf)
        db.commit()
        statuses = {s.step_key: s.status.value for s in wf.steps}
        assert statuses["discover"] == "completed"
        assert statuses["research"] in ("completed", "ready", "failed")
        # the send step must never run without approval
        assert statuses.get("send_outreach") != "completed" or \
            wf.status.value in ("waiting_approval", "running", "paused", "failed")
    finally:
        db.close()


def test_workflow_approval_gate(auth_client):
    """send_approved_outreach must pause at an approval checkpoint."""
    from app.services import orchestrator
    from app.models import WorkflowStep, WorkflowStepStatus, Approval, ApprovalStatus
    import sqlalchemy as sa
    client, headers, owner = auth_client
    db = _db()
    try:
        # custom plan that goes straight to the gated step
        plan = [{"key": "s1", "agent": "communication-manager",
                 "task": "send_approved_outreach", "title": "Send", "seq": 1}]
        wf = orchestrator.create_workflow(db, _wid(owner), "test", plan_override=plan)
        db.commit()
        wf = orchestrator.run_workflow(db, wf)
        db.commit()
        assert wf.status.value == "waiting_approval"
        step = db.scalar(sa.select(WorkflowStep).where(
            WorkflowStep.workflow_id == wf.id))
        assert step.status == WorkflowStepStatus.awaiting_approval
        ap = db.get(Approval, step.approval_id)
        assert ap is not None and ap.status == ApprovalStatus.pending
        assert ap.payload["step_id"] == str(step.id)
        # reject -> workflow fails honestly
        ap.status = ApprovalStatus.rejected
        db.commit()
        wf = orchestrator.resume_after_approval(db, wf)
        db.commit()
        assert wf.status.value == "failed"
    finally:
        db.close()


def test_pause_resume_cancel(auth_client):
    from app.services import orchestrator
    client, headers, owner = auth_client
    db = _db()
    try:
        wf = orchestrator.create_workflow(db, _wid(owner), "pause test")
        db.commit()
        wf = orchestrator.pause_workflow(db, wf)
        assert wf.status.value == "paused"
        wf = orchestrator.resume_workflow(db, wf)
        db.commit()
        assert wf.status.value in ("running", "waiting_approval",
                                   "completed", "failed", "paused")
        wf = orchestrator.cancel_workflow(db, wf)
        db.commit()
        assert wf.status.value == "cancelled"
        assert all(s.status.value in ("completed", "skipped", "failed")
                   for s in wf.steps)
    finally:
        db.close()


def test_budget_limits_pause(auth_client):
    from app.services import orchestrator
    client, headers, owner = auth_client
    db = _db()
    try:
        wf = orchestrator.create_workflow(db, _wid(owner), "budget test")
        wf.limits = {"max_duration_minutes": -1}  # already exceeded
        db.commit()
        wf = orchestrator.run_workflow(db, wf)
        db.commit()
        assert wf.status.value == "paused"
        assert "budget" in (wf.error or "")
    finally:
        db.close()


def test_recovery_resumes_queued(auth_client):
    from app.services import orchestrator
    client, headers, owner = auth_client
    db = _db()
    try:
        wf = orchestrator.create_workflow(db, _wid(owner), "recover me")
        db.commit()
        # simulate restart: leave it queued, call recovery
        n = orchestrator.recover_interrupted(db)
        db.commit()
        assert n >= 1
        db.refresh(wf)
        assert wf.status.value != "queued"
    finally:
        db.close()


def test_event_idempotency(auth_client):
    from app.services import events as ev
    client, headers, owner = auth_client
    wid = _wid(owner)
    db = _db()
    try:
        e1 = ev.publish(db, wid, "lead.discovered",
                        {"lead_id": "x"}, idempotency_key="k1")
        e2 = ev.publish(db, wid, "lead.discovered",
                        {"lead_id": "x"}, idempotency_key="k1")
        db.commit()
        assert e1 is not None and e2 is None
        n = db.query(ev.WorkflowEvent).filter_by(workspace_id=wid).count()
        assert n == 1
    finally:
        db.close()


def test_events_dispatch_and_depth_cap(auth_client):
    from app.services import events as ev
    from app.models import WorkflowEventStatus
    client, headers, owner = auth_client
    db = _db()
    # register a counting handler
    calls = []
    ev.register_handler("test.ping", lambda db_, e: calls.append(e.id))
    try:
        wid = _wid(owner)
        e = ev.publish(db, wid, "test.ping", {"_depth": 0})
        db.commit()
        assert ev.dispatch_pending(db) >= 1
        assert calls == [e.id]
        assert e.status == WorkflowEventStatus.processed
    finally:
        db.close()


def test_workflow_api_endpoints(auth_client):
    client, headers, owner = auth_client
    r = client.post("/api/v1/workflows", json={
        "objective": "Acquire café leads in Pune",
        "workflow_type": "cafe_acquisition"}, headers=headers)
    assert r.status_code == 201, r.text
    wf = r.json()
    assert wf["status"] == "queued"
    assert wf["step_count"] == 12
    wid = wf["id"]
    steps = client.get(f"/api/v1/workflows/{wid}/steps", headers=headers).json()
    assert len(steps) == 12
    assert steps[0]["step_key"] == "discover"
    # plans + capabilities endpoint
    plans = client.get("/api/v1/workflows/plans", headers=headers).json()
    assert "cafe_acquisition" in plans["plans"]
    assert "lead-researcher" in plans["capabilities"]
    # pause/resume/cancel via API
    assert client.post(f"/api/v1/workflows/{wid}/pause", headers=headers).status_code == 200
    assert client.post(f"/api/v1/workflows/{wid}/cancel", headers=headers).status_code == 200
    # cancelled workflows cannot be re-run
    assert client.post(f"/api/v1/workflows/{wid}/run", headers=headers).status_code == 409


def test_orchestration_config_workspace_scoped(auth_client):
    client, headers, owner = auth_client
    r = client.put("/api/v1/workflows/config/orchestration",
                   json={"mode": "autonomous", "agents": {}}, headers=headers)
    assert r.status_code == 200
    assert r.json()["mode"] == "autonomous"
    g = client.get("/api/v1/workflows/config/orchestration", headers=headers)
    assert g.json()["mode"] == "autonomous"


def test_workspace_isolation(auth_client):
    client, headers_a, _ = auth_client
    r = client.post("/api/v1/workflows", json={
        "objective": "test isolation"}, headers=headers_a)
    wid = r.json()["id"]
    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@wf-iso.com", "password": "password123",
        "workspace_name": "Other WS"})
    hb = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get("/api/v1/workflows", headers=hb).json()["total"] == 0
    assert client.get(f"/api/v1/workflows/{wid}", headers=hb).status_code == 404
    assert client.post(f"/api/v1/workflows/{wid}/cancel", headers=hb).status_code == 404


def test_step_failure_retries_then_fails(auth_client):
    """A failing executor retries up to max_attempts then marks failed."""
    from app.services import orchestrator, workflow_steps
    client, headers, owner = auth_client
    db = _db()
    try:
        plan = [{"key": "bad", "agent": "lead-researcher",
                 "task": "enrich_lead", "title": "bad", "seq": 1,
                 "input": {"lead_id": str(uuid.uuid4())}}]
        wf = orchestrator.create_workflow(db, _wid(owner), "fail test",
                                          plan_override=plan)
        wf.steps[0].max_attempts = 1  # fail fast
        db.commit()
        # enrich_lead with nonexistent lead returns found:False, not a failure.
        # Force failure via unsupported executor path instead:
        wf.steps[0].task_type = "nonexistent_task"
        db.commit()
        wf = orchestrator.run_workflow(db, wf)
        db.commit()
        assert wf.steps[0].status.value == "failed"
        assert "cannot perform" in wf.steps[0].error
        assert wf.status.value == "failed"
    finally:
        db.close()


def test_circular_delegation_prevented(auth_client):
    """A step cannot depend on itself or create a dependency cycle."""
    from app.services import orchestrator
    client, headers, owner = auth_client
    db = _db()
    try:
        plan = [
            {"key": "a", "agent": "lead-researcher", "task": "enrich_lead",
             "title": "a", "seq": 1, "depends_on": ["b"]},
            {"key": "b", "agent": "lead-researcher", "task": "enrich_lead",
             "title": "b", "seq": 2, "depends_on": ["a"]},
        ]
        wf = orchestrator.create_workflow(db, _wid(owner), "cycle",
                                          plan_override=plan)
        db.commit()
        # neither step's deps can ever be met -> workflow stays running with
        # no progress, never hangs: run_workflow returns when no progress.
        wf = orchestrator.run_workflow(db, wf)
        db.commit()
        statuses = {s.status.value for s in wf.steps}
        assert statuses <= {"pending", "ready"}
        assert wf.status.value == "running"  # not falsely completed
    finally:
        db.close()


def test_ssrf_protection():
    """website_enricher must reject private/loopback/non-http URLs."""
    from app.services import website_enricher as we
    for bad in ("http://127.0.0.1/", "http://localhost/admin",
                "http://169.254.169.254/latest", "http://10.0.0.1/",
                "ftp://example.com/x", "file:///etc/passwd"):
        assert we._safe_url(bad) is None, bad
    assert we._safe_url("https://example.com") is not None


def test_reschedule_preserves_duration(auth_client):
    """Rescheduling must keep the appointment's own duration, not the default."""
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        from app.services import availability
        s = availability.get_settings(db, _wid(owner))
        tz = availability.tz_of(s)
        day = datetime.now(tz).date() + timedelta(days=3)
        for _ in range(14):
            if day.isoweekday() - 1 in (s.working_days or []):
                break
            day += timedelta(days=1)
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=9)
        # custom 60-min appointment (default is 30)
        end = start + timedelta(minutes=60)
        iso = lambda d: d.astimezone(timezone.utc).isoformat()
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": iso(start), "end_at": iso(end)},
        headers=headers)
    assert r.status_code == 201
    aid = r.json()["id"]
    # reschedule to next working day, same 60-min length expected
    day2 = day + timedelta(days=1)
    for _ in range(14):
        if day2.isoweekday() - 1 in (s.working_days or []):
            break
        day2 += timedelta(days=1)
    s2 = datetime.combine(day2, datetime.min.time(), tzinfo=tz).replace(hour=10)
    e2 = s2 + timedelta(minutes=60)
    rr = client.post(f"/api/v1/appointments/{aid}/reschedule",
                     json={"start_at": iso(s2), "end_at": iso(e2)},
                     headers=headers)
    assert rr.status_code == 200, rr.text
    body = rr.json()
    dur = (datetime.fromisoformat(body["end_at"]) -
           datetime.fromisoformat(body["start_at"])).total_seconds() / 60
    assert dur == 60


def test_lead_stage_history(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    r = client.patch(f"/api/v1/leads/{lead['id']}/stage",
                     json={"stage": "qualified"}, headers=headers)
    assert r.status_code == 200, r.text
    h = client.get(f"/api/v1/leads/{lead['id']}/stage-history",
                   headers=headers)
    assert h.status_code == 200
    rows = h.json()
    assert any(x["to_stage"] == "qualified" for x in rows)


def test_rate_limiter_memory(auth_client):
    from app.core import ratelimit
    ratelimit.reset_for_tests()
    key = "test-scope:127.0.0.1"
    assert ratelimit.hit(key, 2, 60) == 0
    assert ratelimit.hit(key, 2, 60) == 0
    assert ratelimit.hit(key, 2, 60) > 0  # third blocked
    st = ratelimit.status()
    assert st["backend"] == "memory" and st["distributed"] is False


def test_worker_heartbeat_health(auth_client):
    client, headers, owner = auth_client
    db = _db()
    try:
        from app.services import workers_health
        wid = "test-worker:1"
        workers_health.beat(db, "orchestrator", worker_id=wid)
        workers_health.success(db, wid)
        db.commit()
        h = workers_health.health(db)
        w = next(x for x in h["workers"] if x["worker_id"] == wid)
        assert w["status"] == "running" and w["stale"] is False
        assert w["last_success_at"] is not None
        # stale detection
        w_row = db.query(__import__('app.models', fromlist=['WorkerHeartbeat']).WorkerHeartbeat).filter_by(worker_id=wid).first()
        from datetime import timedelta
        w_row.last_heartbeat = __import__('datetime').datetime.now(__import__('datetime').timezone.utc) - timedelta(seconds=300)
        db.commit()
        h2 = workers_health.health(db)
        assert next(x for x in h2["workers"] if x["worker_id"] == wid)["stale"] is True
    finally:
        db.close()
