from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.core.database import get_db
from app.models import UserRole
from app.schemas import WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/current", response_model=WorkspaceOut)
def current_workspace(auth: AuthContext = Depends(get_current_auth)):
    return auth.workspace


@router.patch("/current", response_model=WorkspaceOut)
def update_workspace(body: WorkspaceUpdate,
                     auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
                     db: Session = Depends(get_db)):
    ws = auth.workspace
    if body.name:
        ws.name = body.name
    settings = dict(ws.settings or {})
    if body.inbound_email is not None:
        settings["inbound_email"] = body.inbound_email.strip().lower()
    if body.outbound_from_name is not None:
        settings["outbound_from_name"] = body.outbound_from_name
    ws.settings = settings
    db.commit()
    db.refresh(ws)
    return ws
