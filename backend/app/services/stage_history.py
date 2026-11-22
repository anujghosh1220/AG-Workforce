"""Phase 8.5 — persistent lead-stage history.

A SQLAlchemy mapper event records every Lead.stage transition automatically,
covering API changes, agent actions and workflow steps without call-site
edits. Dedupe: skips when stage unchanged.
"""
from __future__ import annotations

import uuid

from sqlalchemy import inspect as sa_inspect, event

from app.models import Lead, LeadStageHistory

_INSTALLED = False


def _stage_val(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


@event.listens_for(Lead, "after_update")
def _on_lead_update(mapper, connection, target):
    hist = sa_inspect(target).attrs.stage.history
    if not hist.has_changes():
        return
    old = _stage_val(hist.deleted[0]) if hist.deleted else ""
    new = _stage_val(hist.added[0]) if hist.added else ""
    if old == new:
        return
    connection.execute(
        LeadStageHistory.__table__.insert().values(
            id=uuid.uuid4(), workspace_id=target.workspace_id,
            lead_id=target.id, from_stage=old or "unknown", to_stage=new,
            actor="system", source="auto", reason=""))


def install() -> None:
    global _INSTALLED
    _INSTALLED = True  # listens_for decorator already registered at import


def record_manual(db, lead: Lead, from_stage, to_stage, actor="user",
                  source="api", reason=""):
    """Explicit history entry for callers that want actor/reason metadata."""
    f, t = _stage_val(from_stage), _stage_val(to_stage)
    if f == t:
        return None
    last = db.query(LeadStageHistory).filter_by(
        lead_id=lead.id, to_stage=t).order_by(
        LeadStageHistory.created_at.desc()).first()
    if last and last.from_stage == f:
        return last
    h = LeadStageHistory(workspace_id=lead.workspace_id, lead_id=lead.id,
                         from_stage=f, to_stage=t, actor=actor,
                         source=source, reason=reason)
    db.add(h)
    return h
