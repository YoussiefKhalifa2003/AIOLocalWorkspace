"""Team presence: online heartbeat, active room, channel typing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Chat, User, UserPresence, WorkspaceMember, utcnow
from app.services.auth import AuthContext
from app.services.chat_access import require_chat_access

ONLINE_SECONDS = 12
TYPING_SECONDS = 4


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_or_create(db: Session, auth: AuthContext) -> UserPresence:
    row = db.query(UserPresence).filter(UserPresence.user_id == auth.user_id).one_or_none()
    if row is None:
        row = UserPresence(user_id=auth.user_id, tenant_id=auth.tenant_id, last_seen=utcnow())
        db.add(row)
        db.flush()
    elif row.tenant_id != auth.tenant_id:
        row.tenant_id = auth.tenant_id
    return row


def mark_offline(db: Session, auth: AuthContext) -> UserPresence:
    """Force offline immediately (logout / app exit)."""
    row = _get_or_create(db, auth)
    row.last_seen = utcnow() - timedelta(seconds=ONLINE_SECONDS + 5)
    row.active_chat_id = None
    row.typing_chat_id = None
    row.typing_until = None
    return row


def upsert_heartbeat(db: Session, auth: AuthContext, chat_id: int | None) -> UserPresence:
    """Refresh last_seen and optionally set active_chat_id."""
    row = _get_or_create(db, auth)
    row.last_seen = utcnow()
    if chat_id is None:
        row.active_chat_id = None
    else:
        require_chat_access(db, auth, chat_id)
        row.active_chat_id = int(chat_id)
    return row


def set_typing(db: Session, auth: AuthContext, chat_id: int, typing: bool) -> UserPresence:
    """Set or clear typing — shared channels only."""
    chat = require_chat_access(db, auth, chat_id)
    if chat.kind != "channel":
        raise HTTPException(status_code=400, detail="typing only allowed in shared channels")
    row = _get_or_create(db, auth)
    row.last_seen = utcnow()
    row.active_chat_id = int(chat_id)
    if typing:
        row.typing_chat_id = int(chat_id)
        row.typing_until = utcnow() + timedelta(seconds=TYPING_SECONDS)
    else:
        if row.typing_chat_id == int(chat_id):
            row.typing_chat_id = None
            row.typing_until = None
    return row


def clear_typing(db: Session, auth: AuthContext) -> UserPresence:
    row = _get_or_create(db, auth)
    row.last_seen = utcnow()
    row.typing_chat_id = None
    row.typing_until = None
    return row


def list_presence(db: Session, auth: AuthContext) -> list[dict]:
    """Roster with online + typing (no room/channel location)."""
    now = utcnow()
    online_cut = now - timedelta(seconds=ONLINE_SECONDS)

    members = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.tenant_id == auth.tenant_id)
        .order_by(User.name.asc(), User.email.asc())
        .all()
    )
    presence_rows = {
        p.user_id: p
        for p in db.query(UserPresence)
        .filter(UserPresence.tenant_id == auth.tenant_id)
        .all()
    }
    typing_chat_ids = {
        p.typing_chat_id
        for p in presence_rows.values()
        if p.typing_chat_id is not None
    }
    chats: dict[int, Chat] = {}
    if typing_chat_ids:
        for c in (
            db.query(Chat)
            .filter(Chat.id.in_(typing_chat_ids), Chat.tenant_id == auth.tenant_id)
            .all()
        ):
            chats[c.id] = c

    out: list[dict] = []
    for m, u in members:
        p = presence_rows.get(u.id)
        last_seen = _aware(p.last_seen) if p else None
        online = bool(last_seen and last_seen >= online_cut)
        typing_chat_id: int | None = None

        if online and p and p.typing_chat_id and p.typing_until:
            until = _aware(p.typing_until)
            if until and until > now:
                tchat = chats.get(int(p.typing_chat_id))
                if tchat is not None and tchat.kind == "channel":
                    typing_chat_id = tchat.id

        out.append(
            {
                "user_id": u.id,
                "name": u.name,
                "email": u.email,
                "role": m.role,
                "online": online,
                "typing_chat_id": typing_chat_id,
            }
        )

    # Online first, then name
    out.sort(key=lambda r: (0 if r["online"] else 1, (r.get("name") or "").lower()))
    return out
