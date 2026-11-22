import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, UserRole, Workspace
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut, WorkspaceOut
from app.services.helpers import log_activity
from app.models import ActorType

router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_AGENTS = [
    ("lead-researcher", "Lead Researcher", "Discovers and researches cafés",
     "You are the Lead Researcher. You discover cafés, gather business information, "
     "score fit and prepare research summaries for the sales team."),
    ("sales-executive", "Sales Executive", "Prepares personalized sales pitches and follow-ups",
     "You are the Sales Executive. You prepare personalized pitches and follow-up messages "
     "for qualified leads using approved product knowledge."),
    ("communication-manager", "Communication Manager", "Manages email and WhatsApp conversations",
     "You are the Communication Manager. You draft and manage email and WhatsApp "
     "conversations, always routing sensitive sends through human approval."),
    ("executive-assistant", "Executive Assistant", "Coordinates meetings, demos and notifications",
     "You are the Executive Assistant. You coordinate meetings, demos, scheduling and "
     "internal notifications for the team. You check availability, propose time slots, "
     "confirm bookings only after authorization, reschedule and cancel appointments, "
     "schedule reminders and escalate special requests. Never book an unavailable "
     "slot and never claim a meeting is confirmed before the booking succeeds."),
    ("sales-manager", "Sales Manager", "Supervises agents and reports sales performance",
     "You are the Sales Manager. You supervise the other AI employees, review performance "
     "and produce sales performance reports. Monitor agent metrics, detect bottlenecks, "
     "generate evidence-based recommendations and daily/weekly reports. You cannot change "
     "permissions, bypass approvals, override consent, authorize campaigns, change pricing "
     "or claim a sale without verified records. Never fabricate performance data."),
]


def seed_default_agents(db: Session, workspace_id: uuid.UUID) -> None:
    from app.models import Agent

    existing = db.scalar(select(Agent).where(Agent.workspace_id == workspace_id).limit(1))
    if existing:
        return
    for slug, name, title, prompt in DEFAULT_AGENTS:
        db.add(Agent(
            workspace_id=workspace_id,
            name=name,
            slug=slug,
            title=title,
            description=title,
            system_prompt=prompt,
            tools=[],
            permissions={"autonomous": False, "requires_approval": True},
            config={},
            status="idle",
            enabled=True,
        ))


def _registration_allowed(invite_code: str | None) -> bool:
    """Public registration policy.

    - ALLOW_PUBLIC_REGISTRATION env var explicitly enables/disables sign-up.
    - Default: enabled outside production, disabled when ENVIRONMENT=production.
    - REGISTRATION_INVITE_CODE, when set, must be supplied and match.
    """
    env_flag = os.environ.get("ALLOW_PUBLIC_REGISTRATION")
    if env_flag is not None:
        allowed = env_flag.strip().lower() in ("1", "true", "yes")
    else:
        allowed = os.environ.get("ENVIRONMENT", "development").lower() != "production"
    if not allowed:
        return False
    required_code = os.environ.get("REGISTRATION_INVITE_CODE")
    if required_code:
        return invite_code is not None and invite_code == required_code
    return True


def _rate_limit(scope: str, limit: int, window: int, request) -> None:
    import os
    if os.environ.get("ENVIRONMENT") == "test":
        return
    from fastapi import HTTPException, Request, status as st
    from app.core import ratelimit
    key = f"{scope}:{request.client.host if request.client else 'unknown'}"
    wait = ratelimit.hit(key, limit, window)
    if wait:
        raise HTTPException(st.HTTP_429_TOO_MANY_REQUESTS,
                            "rate limit exceeded", headers={"Retry-After": str(wait)})


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    if not _registration_allowed(body.invite_code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is currently closed")
    _rate_limit("register", 10, 300, request)
    email = body.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    slug_base = "".join(c if c.isalnum() else "-" for c in body.workspace_name.lower()).strip("-") or "workspace"
    slug = slug_base
    counter = 1
    while db.scalar(select(Workspace).where(Workspace.slug == slug)):
        counter += 1
        slug = f"{slug_base}-{counter}"

    workspace = Workspace(name=body.workspace_name, slug=slug)
    db.add(workspace)
    db.flush()

    user = User(
        workspace_id=workspace.id,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.owner,
    )
    db.add(user)
    db.flush()

    seed_default_agents(db, workspace.id)
    log_activity(db, workspace.id, "workspace.registered", actor_type=ActorType.user,
                 actor_id=user.id, description=f"Workspace '{workspace.name}' created")
    db.commit()

    return TokenResponse(access_token=create_access_token(user.id, workspace.id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    _rate_limit("login", 10, 60, request)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    return TokenResponse(access_token=create_access_token(user.id, user.workspace_id))


@router.get("/me", response_model=UserOut)
def me(auth: AuthContext = Depends(get_current_auth)):
    return auth.user
