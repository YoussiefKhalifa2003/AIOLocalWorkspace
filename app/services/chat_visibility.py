"""Chat message visibility helpers (public vs whisper)."""

from __future__ import annotations

from datetime import timezone

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


def _iso_utc(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def message_to_dict(db: Session, m: ChatMessage) -> dict:
    from app.db.models import ChatAttachment, User
    from app.services.attachments import attachment_url

    sender = None
    sender_email = None
    if m.sender_user_id:
        u = db.query(User).filter(User.id == m.sender_user_id).one_or_none()
        if u is not None:
            sender = (u.name or "").strip() or u.email
            sender_email = u.email
        else:
            sender = str(m.sender_user_id)
    vis = m.visibility or "public"
    arts = (
        db.query(ChatAttachment)
        .filter(ChatAttachment.message_id == m.id)
        .order_by(ChatAttachment.id.asc())
        .all()
    )
    return {
        "id": m.id,
        "sender": sender,
        "sender_email": sender_email,
        "sender_user_id": m.sender_user_id,
        "agent": m.agent_slug,
        "body": "" if m.deleted_at else m.body,
        "audio_url": None if m.deleted_at else m.audio_url,
        "visibility": vis,
        "whisper_user_id": m.whisper_user_id if vis == "whisper" else None,
        "created_at": _iso_utc(m.created_at),
        "edited_at": _iso_utc(m.edited_at),
        "deleted_at": _iso_utc(m.deleted_at),
        "attachments": []
        if m.deleted_at
        else [
            {
                "id": a.id,
                "filename": a.filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "url": attachment_url(a.id),
            }
            for a in arts
        ],
    }
