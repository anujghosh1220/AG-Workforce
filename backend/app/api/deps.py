import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User, UserRole, Workspace

bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user: User
    workspace: Workspace


def get_current_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(creds.credentials)
        user_id = uuid.UUID(payload["sub"])
        workspace_id = uuid.UUID(payload["wid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Workspace not found")
    return AuthContext(user=user, workspace=workspace)


def get_current_user(auth: AuthContext = Depends(get_current_auth)) -> User:
    return auth.user


def require_roles(*roles: UserRole):
    def _checker(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if auth.user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return auth

    return _checker
