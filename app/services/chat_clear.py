"""Clear chat transcript — personal in channels, full wipe in private rooms."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatAttachment, ChatClearCursor, ChatMention, ChatMessage, utcnow
from app.services.attachments import delete_file
from app.services.auth import AuthContext


def cleared_before_id(db: Session, *, chat_id: int, user_id: int) -> int:
    row = (
        db.query(ChatClearCursor)
        .filter(ChatClearCursor.chat_id == chat_id, ChatClearCursor.user_id == user_id)
        .one_or_none()
    )
    return int(row.cleared_before_id) if row else 0


def set_clear_cursor(
    db: Session, *, tenant_id: int, chat_id: int, user_id: int, before_id: int
) -> None:
    row = (
        db.query(ChatClearCursor)
        .filter(ChatClearCursor.chat_id == chat_id, ChatClearCursor.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        row = ChatClearCursor(
            tenant_id=tenant_id,
            chat_id=chat_id,
            user_id=user_id,
            cleared_before_id=before_id,
        )
        db.add(row)
    else:
        row.cleared_before_id = max(int(row.cleared_before_id or 0), before_id)
        row.updated_at = utcnow()
    db.flush()


def _delete_attachments_for_chat(db: Session, *, chat_id: int) -> None:
    rows = db.query(ChatAttachment).filter(ChatAttachment.chat_id == chat_id).all()
    for row in rows:
        delete_file(row.storage_path)
        db.delete(row)
    db.flush()


def wipe_chat_messages(db: Session, *, chat_id: int) -> int:
    """Hard-delete all messages in a chat (FK-safe). Returns deleted count."""
    msg_ids = [
        mid
        for (mid,) in db.query(ChatMessage.id).filter(ChatMessage.chat_id == chat_id).all()
    ]
    _delete_attachments_for_chat(db, chat_id=chat_id)
    if msg_ids:
        db.query(ChatMention).filter(ChatMention.message_id.in_(msg_ids)).delete(
            synchronize_session=False
        )
    db.query(ChatMention).filter(ChatMention.chat_id == chat_id).delete(
        synchronize_session=False
    )
    deleted = (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_id == chat_id)
        .delete(synchronize_session=False)
    )
    db.query(ChatClearCursor).filter(ChatClearCursor.chat_id == chat_id).delete(
        synchronize_session=False
    )
    db.flush()
    return int(deleted or 0)


def clear_chat_for_user(db: Session, auth: AuthContext, chat: Chat) -> tuple[str, bool]:
    """Clear chat for the acting user.

    Channels: personal watermark only (others unchanged).
    Private rooms: wipe the whole transcript (room is personal).

    Returns (reply_text, cleared_flag).
    """
    is_channel = (chat.kind or "channel") == "channel"
    max_id = (
        db.query(func.max(ChatMessage.id)).filter(ChatMessage.chat_id == chat.id).scalar()
    )
    before = int(max_id or 0)

    if is_channel:
        set_clear_cursor(
            db,
            tenant_id=auth.tenant_id,
            chat_id=chat.id,
            user_id=auth.user_id,
            before_id=before,
        )
        return "Cleared for you only — teammates still see the channel history.", True

    wipe_chat_messages(db, chat_id=chat.id)
    return "Private room cleared.", True
