from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User
from app.db.session import get_db


@dataclass
class AuthContext:
    user: User
    tenant_id: int
    user_id: int


def get_auth(
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
) -> AuthContext:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key required")

    join_key = (get_settings().workspace_join_key or get_settings().demo_api_key or "").strip()
    email = (x_user_email or "").strip().lower()

    # Shared join key must be paired with email (LAN demo login).
    if join_key and x_api_key == join_key:
        if not email:
            raise HTTPException(
                status_code=401,
                detail="X-User-Email required with shared join key (log in again)",
            )
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="invalid email or api key")
        return AuthContext(user=user, tenant_id=user.tenant_id, user_id=user.id)

    user = db.query(User).filter(User.api_key == x_api_key).one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="invalid API key - log in again with your email + password",
        )
    return AuthContext(user=user, tenant_id=user.tenant_id, user_id=user.id)
