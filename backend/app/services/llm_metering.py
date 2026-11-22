"""Actual LLM-call metering + per-workflow budgets (Phase 8.8).

Every real provider invocation is recorded in llm_invocations with
workspace/workflow/step/agent attribution, status, duration and token usage
when the provider reports it. Failed calls and retries each count as
attempts. Heuristic fallbacks are NOT provider calls and are never counted.

Budget enforcement: before a provider call inside a workflow context, the
workflow's llm_calls_used counter is atomically incremented
(UPDATE ... WHERE llm_calls_used < max_llm_calls). If the budget is
exhausted the call is refused with LLMBudgetExceeded.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import LLMInvocation, Workflow

_CTX: ContextVar[dict] = ContextVar("llm_meter_ctx", default={})


class LLMBudgetExceeded(Exception):
    pass


@contextmanager
def scope(*, workspace_id=None, workflow_id=None, step_id=None,
          agent_id=None, agent_slug=None):
    """Attribute subsequent LLM calls in this context."""
    token = _CTX.set({"workspace_id": workspace_id, "workflow_id": workflow_id,
                      "step_id": step_id, "agent_id": agent_id,
                      "agent_slug": agent_slug})
    try:
        yield
    finally:
        _CTX.reset(token)


def current_context() -> dict:
    return dict(_CTX.get() or {})


def _budget_check_and_increment(db: Session, workflow_id) -> None:
    """Atomic budget consume. Raises LLMBudgetExceeded when exhausted."""
    wf = db.get(Workflow, workflow_id)
    if wf is None:
        return
    max_calls = int((wf.limits or {}).get("max_llm_calls", 100))
    used = int(getattr(wf, "llm_calls_used", 0) or 0)
    if used >= max_calls:
        raise LLMBudgetExceeded(
            f"workflow LLM call budget exhausted ({used}/{max_calls})")
    # atomic increment guarded by the limit — safe under concurrency
    res = db.execute(
        update(Workflow)
        .where(Workflow.id == workflow_id,
               Workflow.llm_calls_used < max_calls)
        .values(llm_calls_used=Workflow.llm_calls_used + 1))
    if res.rowcount == 0:
        raise LLMBudgetExceeded(
            f"workflow LLM call budget exhausted ({used}/{max_calls})")


class MeteredLLM:
    """Wraps a real provider; records every generate_json invocation."""

    def __init__(self, provider, db: Session | None = None):
        self._p = provider
        self._db = db
        self.name = provider.name

    @property
    def configured(self):
        return self._p.configured()

    def _record(self, db: Session, ctx: dict, status: str,
                started: float, error: str | None = None) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(self._p, "last_usage", None) or {}
        inv = LLMInvocation(
            workspace_id=ctx.get("workspace_id"),
            workflow_id=ctx.get("workflow_id"),
            step_id=ctx.get("step_id"),
            agent_id=ctx.get("agent_id"),
            agent_slug=ctx.get("agent_slug"),
            provider=self._p.name,
            model=usage.get("model") or getattr(self._p, "last_model", None),
            status=status,
            tokens_prompt=int(usage.get("prompt", 0) or 0),
            tokens_completion=int(usage.get("completion", 0) or 0),
            duration_ms=duration_ms,
            error=(error or "")[:300] or None,
        )
        db.add(inv)
        db.flush()

    def generate_json(self, system: str, user: str, schema, model=None):
        ctx = current_context()
        db = self._db
        own_session = db is None
        if own_session:
            from app.core.database import SessionLocal
            db = SessionLocal()
        try:
            if ctx.get("workflow_id"):
                _budget_check_and_increment(db, ctx["workflow_id"])
            started = time.monotonic()
            try:
                out = self._p.generate_json(system, user, schema, model=model)
            except Exception as exc:
                try:
                    self._record(db, ctx, "failed", started,
                                 error=type(exc).__name__)
                    db.commit() if own_session else db.flush()
                except Exception:
                    pass
                raise
            self._record(db, ctx, "success", started)
            db.commit() if own_session else db.flush()
            return out
        finally:
            if own_session:
                db.close()


def count_for_workflow(db: Session, workflow_id) -> int:
    return db.scalar(select(func.count(LLMInvocation.id)).where(
        LLMInvocation.workflow_id == workflow_id)) or 0


def usage_for_workspace(db: Session, workspace_id,
                        days: int | None = None) -> dict:
    q = select(
        func.count(LLMInvocation.id),
        func.coalesce(func.sum(LLMInvocation.tokens_prompt), 0),
        func.coalesce(func.sum(LLMInvocation.tokens_completion), 0),
    ).where(LLMInvocation.workspace_id == workspace_id)
    if days:
        from datetime import datetime, timedelta, timezone
        q = q.where(LLMInvocation.created_at >=
                    datetime.now(timezone.utc) - timedelta(days=days))
    total, tp, tc = db.execute(q).one()
    failed = db.scalar(select(func.count(LLMInvocation.id)).where(
        LLMInvocation.workspace_id == workspace_id,
        LLMInvocation.status == "failed")) or 0
    return {"calls": int(total), "failed_calls": int(failed),
            "tokens_prompt": int(tp), "tokens_completion": int(tc),
            "window_days": days}
