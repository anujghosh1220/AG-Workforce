import logging
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services import stage_history  # noqa: F401  (registers Lead stage listener)
stage_history.install()

from app.api import (
    activities,
    agents,
    analytics,
    appointments,
    approvals,
    auth,
    campaigns,
    catalog,
    compliance,
    conversations,
    leads,
    manager,
    notifications,
    pipeline,
    research,
    system,
    tasks,
    webhooks,
    whatsapp_webhooks,
    workflows,
    workspaces,
)
from app.core.config import get_settings
from app.core.database import Base, engine

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("agworkforce")

if settings.environment.lower() == "production" and settings.secret_key in (
    "dev-secret-change-me", "change-me-to-a-long-random-string", ""
):
    raise RuntimeError(
        "SECRET_KEY must be set to a strong random value in production. "
        "Refusing to start with a default secret."
    )

app = FastAPI(
    title="AG Workforce API",
    version="1.0.0",
    description="AI Workforce Management Platform — Phase 1.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------- middleware

AUTH_RATE_LIMIT = 20  # requests per minute per IP on /auth endpoints
_auth_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def guard_and_log(request: Request, call_next):
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    rate_limited = (
        settings.environment.lower() != "test"
        and request.method == "POST"
        and path in (f"{settings.api_prefix}/auth/login", f"{settings.api_prefix}/auth/register")
    )
    if rate_limited:
        now = time.time()
        window = _auth_hits[client_ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= AUTH_RATE_LIMIT:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        window.append(now)

    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    # Never log bodies, tokens or credentials — method/path/status/duration only.
    logger.info(
        "%s %s -> %s (%.0fms)", request.method, path, response.status_code, duration_ms
    )
    return response


API = settings.api_prefix
for router in (
    auth.router,
    workspaces.router,
    agents.router,
    leads.router,
    conversations.router,
    pipeline.router,
    tasks.router,
    approvals.router,
    activities.router,
    notifications.router,
    analytics.router,
    compliance.router,
    campaigns.router,
    research.router,
    system.router,
    catalog.products_router,
    catalog.knowledge_router,
    webhooks.router,
    whatsapp_webhooks.router,
    appointments.router,
    appointments.scheduling_router,
    manager.router,
    workflows.router,
):
    app.include_router(router, prefix=API)


@app.exception_handler(IntegrityError)
async def _integrity_conflict(request: Request, exc: IntegrityError):
    """DB-level uniqueness/exclusion violations -> 409, never a 500."""
    return JSONResponse(
        {"detail": "Conflict: the requested change violates a database "
                   "constraint (duplicate or overlapping record)."},
        status_code=409)


@app.on_event("startup")
def _startup() -> None:
    # For production use Alembic migrations; create_all keeps local dev simple.
    # In production it is skipped unless explicitly allowed.
    if settings.environment.lower() != "production" or \
            os.environ.get("AGW_ALLOW_CREATE_ALL") == "1":
        Base.metadata.create_all(bind=engine)
    if (settings.environment.lower() != "test"
            and os.environ.get("AGW_DISABLE_WORKER") != "1"):
        from app.tasks.research_worker import start_worker
        start_worker()
        from app.tasks.comms_worker import start_worker as start_comms
        start_comms()
        from app.tasks.reminder_worker import start_worker as start_reminders
        start_reminders()
        from app.tasks.manager_worker import start_worker as start_manager
        start_manager()
        from app.tasks.orchestrator_worker import start_worker as start_orchestrator
        start_orchestrator()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Database health check failed: %s", type(exc).__name__)
        return JSONResponse({"status": "error", "database": "unreachable"}, status_code=503)
