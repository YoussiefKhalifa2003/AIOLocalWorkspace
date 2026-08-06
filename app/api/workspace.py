from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Tenant, User, WorkspaceMember
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.passwords import verify_password
from app.services.seed import register_via_invite_token
from app.services.workspace_invite import mint_invite_link, tenant_by_invite_token

router = APIRouter(tags=["workspace"])


class LoginIn(BaseModel):
    email: str
    password: str


class LoginOut(BaseModel):
    user_id: int
    tenant_id: int
    email: str
    name: str
    api_key: str


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=4, max_length=128)
    name: str = Field(min_length=1, max_length=120)


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    password = body.password
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return LoginOut(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        name=user.name,
        api_key=user.api_key,
    )


def _join_page(*, token: str, error: str = "") -> HTMLResponse:
    err = f'<p class="err">{error}</p>' if error else ""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join AIO</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:22rem;margin:3rem auto;padding:0 1rem;line-height:1.45;color:#111;background:#f7f7f5}}
  h1{{font-size:1.4rem;margin:0 0 .35rem}}
  .sub{{color:#666;font-size:.9rem;margin:0 0 1.25rem}}
  label{{display:block;margin:.65rem 0 .25rem;font-size:.85rem;color:#444}}
  input{{width:100%;box-sizing:border-box;padding:.55rem .65rem;border:1px solid #ccc;border-radius:6px;font-size:1rem}}
  button{{margin-top:1rem;width:100%;padding:.7rem;background:#1a1a1a;color:#fff;border:0;border-radius:6px;font-size:1rem;cursor:pointer}}
  .err{{color:#b00020;font-size:.9rem}}
  a{{color:#1a73e8}}
</style></head>
<body>
  <h1>Join AIO</h1>
  <p class="sub">Create your account. This link works for one person only. Your name is how teammates will @ you.</p>
  {err}
  <form method="post" action="/join/{token}/register" id="reg">
    <label>Name (required - your @handle)</label>
    <input name="name" type="text" required minlength="1" maxlength="40" autocomplete="nickname" placeholder="e.g. Yousef" pattern="[A-Za-z][A-Za-z0-9_.+-]*" title="Letters/numbers; no spaces" />
    <label>Email</label>
    <input name="email" type="email" required autocomplete="email" />
    <label>Password</label>
    <input name="password" type="password" required minlength="4" autocomplete="new-password" />
    <label>Confirm password</label>
    <input name="password2" type="password" required minlength="4" autocomplete="new-password" />
    <button type="submit">Create account</button>
  </form>
  <p class="sub" style="margin-top:1.25rem">Already have an account? <a href="/app">Log in</a></p>
  <script>
  document.getElementById('reg').addEventListener('submit', function(e) {{
    var p = this.password.value, p2 = this.password2.value;
    if (p !== p2) {{ e.preventDefault(); alert('Passwords do not match'); }}
  }});
  </script>
</body></html>"""
    return HTMLResponse(html)


def _join_success(result: dict) -> HTMLResponse:
    import json as _json

    email = result["email"]
    api_key = result["api_key"]
    name = result["name"]
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Welcome</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:22rem;margin:3rem auto;padding:0 1rem;line-height:1.5}}
  h1{{font-size:1.35rem;color:#0f7b3a}}
</style></head>
<body>
  <h1>Welcome, {_escape(name)}</h1>
  <p>Account created. Opening AIO…</p>
  <script>
  localStorage.setItem('aio_email', {_json.dumps(email)});
  localStorage.setItem('aio_key', {_json.dumps(api_key)});
  localStorage.setItem('aio_uid', {_json.dumps(str(result['user_id']))});
  location.replace('/app');
  </script>
  <p><a href="/app">Open AIO</a></p>
</body></html>"""
    return HTMLResponse(html)


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@router.get("/join/{token}", response_class=HTMLResponse)
def join_page(token: str, db: Session = Depends(get_db)):
    if tenant_by_invite_token(db, token) is None:
        return HTMLResponse(
            "<html><body><h1>Invite not valid</h1>"
            "<p>This link is used up, expired, or was replaced. Ask a teammate for a new invite.</p>"
            "<p><a href='/app'>Go to login</a></p></body></html>",
            status_code=400,
        )
    return _join_page(token=token)


@router.post("/join/{token}/register", response_class=HTMLResponse)
def join_register_form(
    token: str,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(""),
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    if password != password2:
        return _join_page(token=token, error="Passwords do not match")
    try:
        result = register_via_invite_token(
            db, token=token, email=email, password=password, name=name
        )
        db.commit()
        return _join_success(result)
    except ValueError as exc:
        db.rollback()
        return _join_page(token=token, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _join_page(token=token, error=f"Could not register: {exc}")


@router.post("/join/{token}/register.json", response_model=LoginOut)
def join_register_json(token: str, body: RegisterIn, db: Session = Depends(get_db)):
    try:
        result = register_via_invite_token(
            db,
            token=token,
            email=body.email,
            password=body.password,
            name=body.name,
        )
        db.commit()
        return LoginOut(
            user_id=result["user_id"],
            tenant_id=result["tenant_id"],
            email=result["email"],
            name=result["name"],
            api_key=result["api_key"],
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=f"register failed: {exc}") from exc


@router.get("/workspace/invite-link")
@router.post("/workspace/invite-link")
def get_invite_link(
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    max_uses: int = 1,
):
    """Mint invite link with max_uses seats (default 1). Previous unused link is invalidated."""
    tenant = db.query(Tenant).filter(Tenant.id == auth.tenant_id).one()
    data = mint_invite_link(db, tenant, max_uses=max_uses)
    db.commit()
    return data


@router.post("/workspace/invite-link/rotate")
def rotate_link(
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    max_uses: int = 1,
):
    tenant = db.query(Tenant).filter(Tenant.id == auth.tenant_id).one()
    data = mint_invite_link(db, tenant, max_uses=max_uses)
    db.commit()
    return data


@router.get("/workspace/members")
def members(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
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
        }
        for m, u in rows
    ]


class MemberPatchIn(BaseModel):
    name: str | None = None
    role: str | None = None


def _require_owner(db: Session, auth: AuthContext) -> None:
    from app.services.chat_access import is_workspace_owner

    if not is_workspace_owner(db, auth):
        raise HTTPException(status_code=403, detail="owner only")


def _owner_count(db: Session, tenant_id: int) -> int:
    return (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.tenant_id == tenant_id, WorkspaceMember.role == "owner")
        .count()
    )


@router.patch("/workspace/members/{user_id}")
def patch_member(
    user_id: int,
    body: MemberPatchIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    _require_owner(db, auth)
    row = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.tenant_id == auth.tenant_id, WorkspaceMember.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="member not found")
    wm, user = row

    if body.name is not None:
        raise HTTPException(status_code=403, detail="member rename is disabled")

    if body.role is not None:
        role = body.role.strip().lower()
        if role not in ("owner", "member"):
            raise HTTPException(status_code=400, detail="role must be owner or member")
        if wm.role == "owner" and role == "member" and _owner_count(db, auth.tenant_id) <= 1:
            raise HTTPException(status_code=400, detail="cannot demote the last owner")
        wm.role = role

    db.commit()
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": wm.role,
    }


@router.delete("/workspace/members/{user_id}")
def delete_member(
    user_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    _require_owner(db, auth)
    if user_id == auth.user_id:
        raise HTTPException(status_code=400, detail="cannot delete yourself")

    row = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.tenant_id == auth.tenant_id, WorkspaceMember.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="member not found")
    wm, user = row

    if wm.role == "owner" and _owner_count(db, auth.tenant_id) <= 1:
        raise HTTPException(status_code=400, detail="cannot delete the last owner")

    from app.services.seed import delete_user_by_email

    result = delete_user_by_email(db, user.email)
    db.commit()
    return {"status": "ok", **result}


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
