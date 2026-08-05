from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import User, WorkspaceMember
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.seed import invite_user_by_email

router = APIRouter(tags=["workspace"])


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str | None = None


class LoginIn(BaseModel):
    email: str
    api_key: str


class LoginOut(BaseModel):
    user_id: int
    tenant_id: int
    email: str
    name: str
    api_key: str


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    from app.config import get_settings

    email = body.email.strip().lower()
    api_key = body.api_key.strip()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or api key")
    join_key = get_settings().workspace_join_key or get_settings().demo_api_key
    if api_key not in (user.api_key, join_key):
        raise HTTPException(status_code=401, detail="invalid email or api key")
    # Always hand back the shared join key for X-API-Key so reseed doesn't break localStorage.
    # Identity is email via X-User-Email (see get_auth).
    return LoginOut(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        name=user.name,
        api_key=join_key,
    )


@router.post("/workspace/invite")
def invite(
    body: InviteIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        result = invite_user_by_email(
            db,
            tenant_id=auth.tenant_id,
            inviter_user_id=auth.user_id,
            email=body.email,
            name=body.name,
        )
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspace/members")
def members(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    from app.config import get_settings

    join_key = get_settings().workspace_join_key or get_settings().demo_api_key
    rows = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.tenant_id == auth.tenant_id)
        .order_by(User.email.asc())
        .all()
    )
    return [
        {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": m.role,
            "api_key": join_key,
        }
        for m, u in rows
    ]


@router.get("/auth/me")
def me(auth: AuthContext = Depends(get_auth)):
    return {
        "user_id": auth.user_id,
        "tenant_id": auth.tenant_id,
        "email": auth.user.email,
        "name": auth.user.name,
    }
