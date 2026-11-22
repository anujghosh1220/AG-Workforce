"""Integration configuration status — reports configured/not-configured
WITHOUT exposing any secrets. Worker health endpoints are admin-only."""
import os

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.integrations.email.base import get_email_provider
from app.integrations.llm.base import get_llm
from app.models import UserRole

router = APIRouter(prefix="/system", tags=["system"])
ADMIN = (UserRole.owner, UserRole.admin)


@router.get("/status")
def integration_status(auth: AuthContext = Depends(get_current_auth),
                        db: Session = Depends(get_db)):
    email = get_email_provider()
    llm = get_llm()
    ws_settings = getattr(auth.workspace, "settings", None) or {}
    return {
        "email": {
            "provider": os.environ.get("EMAIL_PROVIDER", "resend"),
            "configured": email is not None,
            "from_address_set": bool(os.environ.get("EMAIL_FROM")),
            "webhook_secret_set": bool(os.environ.get("RESEND_WEBHOOK_SECRET")),
        },
        "llm": {
            "provider": os.environ.get("LLM_PROVIDER", "gemini"),
            "configured": llm is not None,
            "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            "fallback": "heuristic rules (no AI judgement)",
        },
        "workspace": {
            "inbound_email": ws_settings.get("inbound_email"),
            "inbound_configured": bool(ws_settings.get("inbound_email")),
        },
        "calendar": {
            "providers": [
                {"name": p["name"], "configured": p["configured"]}
                for p in __import__(
                    "app.integrations.calendar.registry",
                    fromlist=["available_providers"]).available_providers()
            ],
            "internal_scheduling": "always available",
        },
        "rate_limit": __import__("app.core.ratelimit", fromlist=["x"]).status(),
        "workers": __import__("app.services.workers_health", fromlist=["x"]).health(db),
        "environment": os.environ.get("ENVIRONMENT", "development"),
    }


# ---------------------------------------------------------- worker health


@router.get("/workers")
def list_workers(
    worker_type: str | None = Query(None),
    status: str | None = Query(None),
    stale: bool | None = Query(None),
    auth: AuthContext = Depends(require_roles(*ADMIN)),
    db: Session = Depends(get_db),
):
    from app.services import workers_health
    h = workers_health.health(db, worker_type=worker_type, status=status,
                              stale_only=bool(stale))
    return h


@router.get("/workers/summary")
def worker_summary(auth: AuthContext = Depends(require_roles(*ADMIN)),
                   db: Session = Depends(get_db)):
    from app.services import workers_health
    h = workers_health.health(db)
    return {"counts": h["counts"], "total": len(h["workers"]),
            "stale_after_seconds": h["stale_after_seconds"],
            "checked_at": h["checked_at"]}


@router.get("/workers/{worker_id}")
def worker_detail(worker_id: str,
                  auth: AuthContext = Depends(require_roles(*ADMIN)),
                  db: Session = Depends(get_db)):
    from app.services import workers_health
    h = workers_health.health(db)
    for w in h["workers"]:
        if w["worker_id"] == worker_id:
            return w
    from fastapi import HTTPException
    raise HTTPException(404, "worker not found")
