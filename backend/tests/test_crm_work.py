import uuid

from tests.conftest import make_lead


def test_default_agents_seeded(auth_client):
    client, headers, _ = auth_client
    resp = client.get("/api/v1/agents", headers=headers)
    assert resp.status_code == 200
    agents = resp.json()["items"]
    assert len(agents) == 5
    slugs = {a["slug"] for a in agents}
    assert {"lead-researcher", "sales-executive", "communication-manager",
            "executive-assistant", "sales-manager"} <= slugs


def test_agent_detail_and_patch(auth_client):
    client, headers, _ = auth_client
    agents = client.get("/api/v1/agents", headers=headers).json()["items"]
    agent = agents[0]
    detail = client.get(f"/api/v1/agents/{agent['id']}", headers=headers)
    assert detail.status_code == 200
    patched = client.patch(f"/api/v1/agents/{agent['id']}",
                           json={"status": "paused"}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"


def test_lead_crud(auth_client):
    client, headers, _ = auth_client
    lead = make_lead(client, headers, business_name="Blue Bottle", city="Austin",
                     lead_score=42, estimated_value="199.00")
    assert lead["stage"] == "discovered"

    got = client.get(f"/api/v1/leads/{lead['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["business_name"] == "Blue Bottle"

    upd = client.patch(f"/api/v1/leads/{lead['id']}",
                       json={"lead_score": 77, "notes": "Called"}, headers=headers)
    assert upd.status_code == 200
    assert upd.json()["lead_score"] == 77

    # filtering
    mine = client.get("/api/v1/leads", params={"q": "Blue Bottle"}, headers=headers).json()
    assert mine["total"] == 1
    filtered = client.get("/api/v1/leads", params={"min_score": 50}, headers=headers).json()
    assert filtered["total"] == 1  # score now 77

    # delete
    assert client.delete(f"/api/v1/leads/{lead['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/leads/{lead['id']}", headers=headers).status_code == 404


def test_lead_pagination_and_sort(auth_client):
    client, headers, _ = auth_client
    for i in range(5):
        make_lead(client, headers, business_name=f"Cafe {i}", lead_score=i)
    page = client.get("/api/v1/leads", params={"page": 1, "page_size": 2,
                                              "sort": "lead_score", "order": "asc"},
                      headers=headers).json()
    assert page["total"] >= 5
    assert page["pages"] >= 3
    scores = [l["lead_score"] for l in page["items"]]
    assert scores == sorted(scores)


def test_pipeline_stage_transition_and_activity(auth_client):
    client, headers, _ = auth_client
    lead = make_lead(client, headers)

    resp = client.patch(f"/api/v1/leads/{lead['id']}/stage",
                        json={"stage": "researched", "note": "Done"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["stage"] == "researched"

    acts = client.get("/api/v1/activities", params={"lead_id": lead["id"]}, headers=headers).json()
    actions = [a["action"] for a in acts["items"]]
    assert "lead.stage_changed" in actions

    pipe = client.get("/api/v1/pipeline", headers=headers).json()
    researched = next(s for s in pipe if s["stage"] == "researched")
    assert researched["count"] >= 1


def test_bulk_stage_update(auth_client):
    client, headers, _ = auth_client
    leads = [make_lead(client, headers) for _ in range(3)]
    ids = [l["id"] for l in leads]
    resp = client.post("/api/v1/pipeline/bulk-stage",
                       json={"lead_ids": ids, "stage": "contacted"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["updated"] == 3


def test_task_lifecycle(auth_client):
    client, headers, user = auth_client
    agents = client.get("/api/v1/agents", headers=headers).json()["items"]
    agent_id = agents[0]["id"]

    task = client.post("/api/v1/tasks", json={
        "title": "Research cafe leads", "assignee_type": "agent",
        "assignee_agent_id": agent_id, "priority": "high",
    }, headers=headers)
    assert task.status_code == 201, task.text
    tid = task.json()["id"]
    assert task.json()["status"] == "pending"

    done = client.patch(f"/api/v1/tasks/{tid}", json={"status": "completed"}, headers=headers)
    assert done.status_code == 200
    assert done.json()["status"] == "completed"

    listing = client.get("/api/v1/tasks", params={"status": "completed"}, headers=headers).json()
    assert listing["total"] >= 1


def test_approval_lifecycle(auth_client):
    client, headers, _ = auth_client
    lead = make_lead(client, headers)

    approval = client.post("/api/v1/approvals", json={
        "action_type": "send_email",
        "title": "Send intro email to Blue Bottle",
        "payload": {"to": "owner@cafe.com", "subject": "Hi"},
        "lead_id": lead["id"],
    }, headers=headers)
    assert approval.status_code == 201, approval.text
    aid = approval.json()["id"]
    assert approval.json()["status"] == "pending"

    # notification created
    notes = client.get("/api/v1/notifications", headers=headers).json()
    assert any(n["type"] == "pending_approval" for n in notes["items"])

    # approve
    ok = client.patch(f"/api/v1/approvals/{aid}", json={"status": "approved"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    # cannot re-decide
    again = client.patch(f"/api/v1/approvals/{aid}", json={"status": "rejected"}, headers=headers)
    assert again.status_code == 409

    # filter by status
    pending = client.get("/api/v1/approvals", params={"status": "pending"}, headers=headers).json()
    assert all(a["id"] != aid for a in pending["items"])


def test_notification_mark_read(auth_client):
    client, headers, _ = auth_client
    make_lead(client, headers, qualification_status="qualified")
    notes = client.get("/api/v1/notifications", params={"unread_only": True}, headers=headers).json()
    assert notes["total"] >= 1
    nid = notes["items"][0]["id"]
    resp = client.patch(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True
    read_all = client.post("/api/v1/notifications/read-all", headers=headers)
    assert read_all.status_code == 200


def test_analytics_uses_real_records(auth_client):
    client, headers, _ = auth_client
    make_lead(client, headers, estimated_value="100.00")
    make_lead(client, headers, estimated_value="50.00", qualification_status="qualified")

    data = client.get("/api/v1/analytics/overview", headers=headers).json()
    assert data["total_leads"] >= 2
    assert data["qualified_leads"] >= 1
    assert data["pipeline_value"] >= 150
    assert "leads_by_stage" in data and "leads_over_time" in data


def test_products_and_knowledge(auth_client):
    client, headers, _ = auth_client
    prod = client.post("/api/v1/products", json={
        "name": "CaféFlow", "slug": "cafeflow", "description": "Cafe management software",
    }, headers=headers)
    assert prod.status_code == 201, prod.text

    know = client.post("/api/v1/knowledge", json={
        "product_id": prod.json()["id"], "category": "pricing",
        "title": "Starter plan", "content": "$29/mo",
    }, headers=headers)
    assert know.status_code == 201

    listing = client.get("/api/v1/knowledge", params={"category": "pricing"}, headers=headers).json()
    assert listing["total"] >= 1
