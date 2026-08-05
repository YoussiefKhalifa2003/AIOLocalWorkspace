from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Invite, User, WorkspaceMember
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.seed import accept_invite_by_token, invite_user_by_email

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
        pending = (
            db.query(Invite)
            .filter(Invite.email == email, Invite.status == "pending")
            .first()
        )
        if pending is not None:
            raise HTTPException(
                status_code=403,
                detail="accept the invite email first (click Accept invite), then log in",
            )
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


def _accept_html(*, ok: bool, title: str, body: str) -> HTMLResponse:
    color = "#0f7b3a" if ok else "#b00020"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;line-height:1.5;color:#111}}
  h1{{font-size:1.35rem;color:{color}}}
  code{{background:#f2f2f2;padding:.15rem .35rem;border-radius:4px}}
  a.btn{{display:inline-block;margin-top:1rem;padding:.65rem 1rem;background:#1a73e8;color:#fff;text-decoration:none;border-radius:6px}}
</style></head>
<body>
  <h1>{title}</h1>
  {body}
</body></html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("/invite/accept/{token}", response_class=HTMLResponse)
def accept_invite(token: str, db: Session = Depends(get_db)):
    try:
        result = accept_invite_by_token(db, token)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _accept_html(
            ok=False,
            title="Invite not valid",
            body=f"<p>{exc}</p><p><a class='btn' href='/app'>Go to login</a></p>",
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _accept_html(
            ok=False,
            title="Could not accept invite",
            body=f"<p>{exc}</p><p><a class='btn' href='/app'>Go to login</a></p>",
        )

    already = result.get("already_accepted")
    headline = "You're already in" if already else "Invite accepted"
    body = f"""
  <p>Welcome, <strong>{result['name']}</strong>.</p>
  <p>Log in with:</p>
  <p>Email: <code>{result['email']}</code><br>
  Join key: <code>{result['join_key']}</code></p>
  <p><a class="btn" href="/app">Open AIO</a></p>
"""
    return _accept_html(ok=True, title=headline, body=body)


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
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=f"invite failed: {exc}") from exc


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
def me(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    from app.services.mentions import unread_mentions

    mentions = unread_mentions(db, auth, limit=20)
    return {
        "user_id": auth.user_id,
        "tenant_id": auth.tenant_id,
        "email": auth.user.email,
        "name": auth.user.name,
        "unread_mentions": len(mentions),
        "mentions": mentions,
    }


@router.get("/workspace/mentions")
def list_mentions(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    from app.services.mentions import unread_mentions

    rows = unread_mentions(db, auth)
    return {"unread": len(rows), "mentions": rows}


class MentionsReadIn(BaseModel):
    ids: list[int] | None = None


@router.post("/workspace/mentions/read")
def read_mentions(
    body: MentionsReadIn | None = None,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    from app.services.mentions import mark_mentions_read

    ids = body.ids if body else None
    n = mark_mentions_read(db, auth, ids)
    db.commit()
    return {"status": "ok", "marked": n}
