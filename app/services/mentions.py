"""@people pings - channel/general only (never private rooms)."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMention, ChatMessage, User, WorkspaceMember, utcnow
from app.services.auth import AuthContext
from app.services.status import resolve_member

MENTION_RE = re.compile(r"@([A-Za-z0-9_.+-]+)\b")


def collect_mention_user_ids(
    db: Session, *, tenant_id: int, sender_user_id: int, body: str
) -> list[int]:
    ids: set[int] = set()
    for m in MENTION_RE.finditer(body or ""):
        raw = m.group(1)
        key = raw.lower()
        if key == "team":
            members = (
                db.query(WorkspaceMember.user_id)
                .filter(WorkspaceMember.tenant_id == tenant_id)
                .all()
            )
            for (uid,) in members:
                if uid != sender_user_id:
                    ids.add(uid)
            continue
        user = resolve_member(db, tenant_id, raw)
        if user is not None and user.id != sender_user_id:
            ids.add(user.id)
    return sorted(ids)


def record_mentions(
    db: Session,
    *,
    tenant_id: int,
    chat_id: int,
    message_id: int,
    from_user_id: int,
    body: str,
) -> list[ChatMention]:
    chat = db.query(Chat).filter(Chat.id == chat_id).one_or_none()
    # Only notify for team channels (e.g. #general) - never private rooms
    if chat is None or (chat.kind or "channel") != "channel":
        return []

    out: list[ChatMention] = []
    for uid in collect_mention_user_ids(
        db, tenant_id=tenant_id, sender_user_id=from_user_id, body=body
    ):
        row = ChatMention(
            tenant_id=tenant_id,
            chat_id=chat_id,
            message_id=message_id,
            mentioned_user_id=uid,
            from_user_id=from_user_id,
        )
        db.add(row)
        out.append(row)
    if out:
        db.flush()
    return out


def unread_mentions(db: Session, auth: AuthContext, *, limit: int = 50) -> list[dict]:
    rows = (
        db.query(ChatMention)
        .filter(
            ChatMention.tenant_id == auth.tenant_id,
            ChatMention.mentioned_user_id == auth.user_id,
            ChatMention.read_at.is_(None),
        )
        .order_by(ChatMention.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        chat = db.query(Chat).filter(Chat.id == r.chat_id).one_or_none()
        # Drop stale private-room pings if any exist
        if chat is not None and (chat.kind or "channel") != "channel":
            continue
        frm = None
        if r.from_user_id:
            u = db.query(User).filter(User.id == r.from_user_id).one_or_none()
            frm = ((u.name or "").strip() or u.email) if u else str(r.from_user_id)
        msg = db.query(ChatMessage).filter(ChatMessage.id == r.message_id).one_or_none()
        snippet = (msg.body or "")[:160] if msg else ""
        out.append(
            {
                "id": r.id,
                "chat_id": r.chat_id,
                "chat_name": chat.name if chat else f"#{r.chat_id}",
                "message_id": r.message_id,
                "from": frm,
                "snippet": snippet,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


def mark_mentions_read(db: Session, auth: AuthContext, mention_ids: list[int] | None = None) -> int:
    q = db.query(ChatMention).filter(
        ChatMention.tenant_id == auth.tenant_id,
        ChatMention.mentioned_user_id == auth.user_id,
        ChatMention.read_at.is_(None),
    )
    if mention_ids:
        q = q.filter(ChatMention.id.in_(mention_ids))
    n = 0
    for row in q.all():
        row.read_at = utcnow()
        n += 1
    return n
