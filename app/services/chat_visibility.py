"""Chat message visibility helpers (public vs whisper)."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import ChatMessage


def mark_whisper(msg: ChatMessage, user_id: int) -> ChatMessage:
    msg.visibility = "whisper"
    msg.whisper_user_id = user_id
    return msg


def visible_messages_filter(auth_user_id: int):
    """SQLAlchemy filter: public OR whisper owned by auth user."""
    return or_(
        ChatMessage.visibility == "public",
        ChatMessage.visibility.is_(None),  # legacy rows
        (ChatMessage.visibility == "whisper") & (ChatMessage.whisper_user_id == auth_user_id),
    )


def message_to_dict(db: Session, m: ChatMessage) -> dict:
    from app.db.models import User

    sender = None
    if m.sender_user_id:
        u = db.query(User).filter(User.id == m.sender_user_id).one_or_none()
        sender = u.email if u else str(m.sender_user_id)
    vis = m.visibility or "public"
    return {
        "id": m.id,
        "sender": sender,
        "agent": m.agent_slug,
        "body": m.body,
        "audio_url": m.audio_url,
        "visibility": vis,
        "whisper_user_id": m.whisper_user_id if vis == "whisper" else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
