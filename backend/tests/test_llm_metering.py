"""WS7 — LLM metering + budget tests. Providers are MOCKED; no paid calls."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest




def _mk_ws(db):
    from app.models import Workspace
    ws = Workspace(name="T", slug=f"t-{uuid.uuid4().hex[:8]}")
    db.add(ws); db.flush()
    return ws.id

def _fake_llm():
    """A real MeteredLLM wrapping a stub provider."""
    from app.services.llm_metering import MeteredLLM

    class Stub:
        name = "stub"
        last_usage = {"prompt": 11, "completion": 7, "model": "stub-1"}
        last_model = "stub-1"
        calls = 0

        def configured(self):
            return True

        def generate_json(self, system, user, schema, model=None):
            self.calls += 1
            return schema.model_validate({"ok": True}) if hasattr(
                schema, "model_validate") else {}

    return MeteredLLM(Stub())


class _Schema:
    @staticmethod
    def model_validate(d):
        return d


def test_successful_call_records_invocation():
    from app.core.database import SessionLocal
    from app.services import llm_metering
    from app.models import LLMInvocation
    import sqlalchemy as sa

    llm = _fake_llm()
    db = SessionLocal()
    try:
        wid = _mk_ws(db)
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        with llm_metering.scope(workspace_id=wid, agent_slug="t"):
            llm.generate_json("s", "u", _Schema)
        db.expire_all()
        n = db.scalar(sa.select(sa.func.count(LLMInvocation.id)).where(
            LLMInvocation.agent_slug == "t"))
        assert n == 1
        inv = db.scalar(sa.select(LLMInvocation).where(
            LLMInvocation.agent_slug == "t"))
        assert inv.status == "success"
        assert inv.tokens_prompt == 11 and inv.tokens_completion == 7
        assert inv.provider == "stub" and inv.model == "stub-1"
        assert inv.duration_ms >= 0
    finally:
        db.close()


def test_failed_call_records_failed_invocation():
    from app.core.database import SessionLocal
    from app.services import llm_metering
    from app.models import LLMInvocation
    import sqlalchemy as sa

    class Boom:
        name = "boom"
        last_usage = {}

        def configured(self):
            return True

        def generate_json(self, *a, **k):
            raise RuntimeError("provider down")

    from app.services.llm_metering import MeteredLLM
    llm = MeteredLLM(Boom())
    db = SessionLocal()
    try:
        wid = _mk_ws(db)
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError):
            with llm_metering.scope(workspace_id=wid):
                llm.generate_json("s", "u", _Schema)
        inv = db.scalar(sa.select(LLMInvocation).where(
            LLMInvocation.workspace_id == wid))
        assert inv is not None and inv.status == "failed"
        assert inv.error == "RuntimeError"
    finally:
        db.close()


def test_missing_token_metadata_recorded_as_zero():
    from app.core.database import SessionLocal
    from app.services import llm_metering
    from app.models import LLMInvocation
    import sqlalchemy as sa

    class NoUsage:
        name = "nousage"

        def configured(self):
            return True

        def generate_json(self, *a, **k):
            return {}

    from app.services.llm_metering import MeteredLLM
    llm = MeteredLLM(NoUsage())
    db = SessionLocal()
    try:
        wid = _mk_ws(db)
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        with llm_metering.scope(workspace_id=wid):
            llm.generate_json("s", "u", _Schema)
        inv = db.scalar(sa.select(LLMInvocation).where(
            LLMInvocation.workspace_id == wid))
        assert inv.tokens_prompt == 0 and inv.tokens_completion == 0
    finally:
        db.close()


def _mk_workflow(db, wid, max_calls=2):
    from app.models import Workflow
    wf = Workflow(workspace_id=wid, objective="t", workflow_type="x",
                  status="running", autonomy_mode="manual",
                  correlation_id="c", limits={"max_llm_calls": max_calls},
                  llm_calls_used=0)
    db.add(wf); db.flush()
    return wf


def test_budget_blocks_after_limit():
    from app.core.database import SessionLocal
    from app.services import llm_metering
    from app.models import Workspace
    import sqlalchemy as sa

    db = SessionLocal()
    try:
        ws = Workspace(name="B", slug=f"b-{uuid.uuid4().hex[:6]}")
        db.add(ws); db.flush()
        wf = _mk_workflow(db, ws.id, max_calls=1)
        db.commit()
        wf_id, wid = wf.id, ws.id
    finally:
        db.close()

    llm = _fake_llm()
    # first call consumes budget
    db = SessionLocal()
    try:
        with llm_metering.scope(workspace_id=wid, workflow_id=wf_id):
            llm.generate_json("s", "u", _Schema)
        db.commit()
    finally:
        db.close()
    # second call must be refused before provider executes
    db = SessionLocal()
    try:
        with pytest.raises(llm_metering.LLMBudgetExceeded):
            with llm_metering.scope(workspace_id=wid, workflow_id=wf_id):
                llm.generate_json("s", "u", _Schema)
        wf2 = db.get(__import__('app.models', fromlist=['Workflow']).Workflow, wf_id)
        assert wf2.llm_calls_used == 1
    finally:
        db.close()


def test_concurrent_budget_not_exceeded():
    """Two threads racing for the last budget slot: only one wins."""
    import threading
    from app.core.database import SessionLocal
    from app.services import llm_metering
    from app.models import Workspace, Workflow
    import sqlalchemy as sa

    db = SessionLocal()
    try:
        ws = Workspace(name="C", slug=f"c-{uuid.uuid4().hex[:6]}")
        db.add(ws); db.flush()
        wf = _mk_workflow(db, ws.id, max_calls=1)
        db.commit()
        wf_id, wid = wf.id, ws.id
    finally:
        db.close()

    llm = _fake_llm()
    results = []

    def attempt():
        db2 = SessionLocal()
        try:
            with llm_metering.scope(workspace_id=wid, workflow_id=wf_id):
                llm.generate_json("s", "u", _Schema)
            results.append("ok")
        except llm_metering.LLMBudgetExceeded:
            results.append("budget")
        except Exception as e:
            results.append(type(e).__name__)
        finally:
            db2.close()

    ts = [threading.Thread(target=attempt) for _ in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert results.count("ok") == 1
    assert results.count("budget") == 3


def test_usage_endpoint_totals_and_isolation(client, auth_client):
    _, headers, owner = auth_client
    wid = uuid.UUID(owner["workspace_id"])
    from app.core.database import SessionLocal
    from app.models import LLMInvocation
    import sqlalchemy as sa
    db = SessionLocal()
    try:
        for i in range(3):
            db.add(LLMInvocation(
                workspace_id=wid, provider="gemini", model="g-1",
                status="success" if i < 2 else "failed",
                tokens_prompt=10, tokens_completion=5, duration_ms=1,
                agent_slug="lead-researcher"))
        # another workspace's data must not leak
        from app.models import Workspace
        ow = Workspace(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
        db.add(ow); db.flush()
        other = ow.id
        db.add(LLMInvocation(workspace_id=other, provider="x",
                             status="success", tokens_prompt=999,
                             tokens_completion=999, duration_ms=1))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/v1/analytics/llm-usage?days=30", headers=headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["calls"] == 3 and d["failed_calls"] == 1
    assert d["tokens_prompt"] == 30 and d["tokens_completion"] == 15
    assert d["by_model"][0]["provider"] == "gemini"
    assert any(a["agent"] == "lead-researcher" for a in d["by_agent"])
    assert len(d["daily"]) >= 1
    # other workspace invisible
    assert all(m["provider"] != "x" for m in d["by_model"])


def test_usage_endpoint_empty_and_validation(client, auth_client):
    _, headers, _ = auth_client
    r = client.get("/api/v1/analytics/llm-usage?days=7", headers=headers)
    assert r.status_code == 200
    d = r.json()
    assert d["calls"] == 0 and d["by_model"] == [] and d["daily"] == []
    # invalid days rejected
    r2 = client.get("/api/v1/analytics/llm-usage?days=0", headers=headers)
    assert r2.status_code == 422
    r3 = client.get("/api/v1/analytics/llm-usage?days=9999", headers=headers)
    assert r3.status_code == 422


def test_usage_endpoint_requires_auth(client):
    assert client.get("/api/v1/analytics/llm-usage").status_code in (401, 403)
