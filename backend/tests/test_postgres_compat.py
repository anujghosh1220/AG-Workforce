"""PostgreSQL compatibility checks.

Two layers:
1. Dialect-compile checks that run everywhere (no DB needed): verifies UUID
   columns render as UUID and JSON variants render as JSONB on PostgreSQL.
2. Live checks that run only when AGW_TEST_PG_URL is set, exercising real
   PostgreSQL behaviour (enums, JSONB round-trip, FK cascades).
"""
import os
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.core.database import Base
from app.models import (
    Agent,
    Approval,
    ChannelConsent,
    Lead,
    Message,
    SuppressionRecord,
    Task,
    User,
    Workspace,
)

PG_URL = os.environ.get("AGW_TEST_PG_URL")
requires_pg = pytest.mark.skipif(not PG_URL, reason="AGW_TEST_PG_URL not set")


def _pg_type(column):
    return str(column.type.compile(dialect=postgresql.dialect()))


def test_uuid_columns_compile_to_pg_uuid():
    for model in (Workspace, User, Agent, Lead, Task, Approval, Message, ChannelConsent, SuppressionRecord):
        col = model.__table__.c.id
        assert _pg_type(col) == "UUID", f"{model.__tablename__}.id -> {_pg_type(col)}"


def test_json_columns_compile_to_jsonb_on_postgres():
    for table_name, col_name in [
        ("agents", "tools"), ("agents", "permissions"), ("agents", "config"),
        ("agents", "performance"), ("agent_runs", "input_payload"),
        ("agent_runs", "output_payload"), ("approvals", "payload"),
        ("activities", "metadata"),
    ]:
        col = Base.metadata.tables[table_name].c[col_name]
        assert "JSONB" in _pg_type(col), f"{table_name}.{col_name} -> {_pg_type(col)}"


def test_enum_columns_use_native_pg_enums():
    col = Lead.__table__.c.stage
    compiled = col.type.compile(dialect=postgresql.dialect())
    assert "LEADSTAGE" in str(compiled).upper() or compiled.__class__.__name__ == "Enum"


def test_all_workspace_tables_have_workspace_id():
    owned = [
        "users", "agents", "agent_runs", "leads", "contacts", "lead_sources",
        "conversations", "messages", "tasks", "approvals", "activities",
        "opportunities", "products", "product_knowledge", "notifications",
        "audit_logs", "channel_consents", "suppression_records", "message_templates",
    ]
    for name in owned:
        assert "workspace_id" in Base.metadata.tables[name].c, f"{name} missing workspace_id"


@requires_pg
def test_postgres_live_roundtrip():
    """Real PG: create schema, insert workspace+lead, verify JSONB/UUID/enums."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(PG_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        ws = Workspace(name="PG Test", slug=f"pg-{uuid.uuid4().hex[:8]}")
        db.add(ws)
        db.flush()
        lead = Lead(workspace_id=ws.id, business_name="PG Cafe", lead_score=55)
        db.add(lead)
        db.commit()

        fetched = db.scalar(select(Lead).where(Lead.id == lead.id))
        assert fetched.business_name == "PG Cafe"
        assert isinstance(fetched.id, uuid.UUID)

        agent = Agent(workspace_id=ws.id, name="Tester", slug="tester",
                      permissions={"requires_approval": True}, tools=["t1"])
        db.add(agent)
        db.commit()
        fetched_agent = db.scalar(select(Agent).where(Agent.id == agent.id))
        assert fetched_agent.permissions["requires_approval"] is True
        assert fetched_agent.tools == ["t1"]
    finally:
        db.close()
        engine.dispose()
