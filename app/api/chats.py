from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatAttachment, ChatMember, ChatMessage, User, WorkspaceMember, utcnow
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.attachments import MAX_ATTACHMENTS_PER_MESSAGE, delete_file
from app.services.chat_access import (
    chat_to_dict,
    ensure_channel_membership,
    ensure_chat_member,
    ensure_private_room,
    list_visible_chats,
    require_chat_access,
)
from app.services.orchestrator import handle_chat_message
from app.services.chat_visibility import _iso_utc, message_to_dict, visible_messages_filter
from app.services.mentions import record_mentions

router = APIRouter(tags=["chats"])


class ChatIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "channel"
    project_id: int | None = 1


class ChatPatch(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MessageIn(BaseModel):
    body: str = ""
    speak: bool = False
    attachment_ids: list[int] = Field(default_factory=list)


class MessagePatch(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class ChatMemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


def _delete_chat(db: Session, chat_id: int) -> None:
    db.query(ChatMessage).filter(ChatMessage.chat_id == chat_id).delete()
    db.query(ChatMember).filter(ChatMember.chat_id == chat_id).delete()
    db.query(Chat).filter(Chat.id == chat_id).delete()


@router.get("/chats")
def list_chats(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    rows = list_visible_chats(db, auth)
    return [chat_to_dict(c) for c in rows]


@router.post("/chats")
def create_chat(
    body: ChatIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    kind = (body.kind or "channel").strip().lower()
    if kind not in ("channel", "private"):
        raise HTTPException(status_code=400, detail="kind must be channel or private")

    if kind == "private":
        user = db.query(User).filter(User.id == auth.user_id).one()
        chat = ensure_private_room(
            db,
            tenant_id=auth.tenant_id,
            project_id=body.project_id,
            user=user,
        )
        db.commit()
        return chat_to_dict(chat)

    chat = Chat(
        tenant_id=auth.tenant_id,
        project_id=body.project_id,
        name=body.name.strip(),
        kind="channel",
        owner_user_id=None,
    )
    db.add(chat)
    db.flush()
    ensure_channel_membership(db, chat)
    db.commit()
    return chat_to_dict(chat)


@router.patch("/chats/{chat_id}")
def rename_chat(
    chat_id: int,
    body: ChatPatch,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    raise HTTPException(status_code=403, detail="chat rename is disabled")


@router.delete("/chats/{chat_id}")
def delete_chat(
    chat_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    chat = require_chat_access(db, auth, chat_id)
    if chat.kind == "private" and chat.owner_user_id != auth.user_id:
        raise HTTPException(status_code=403, detail="cannot delete another user's private room")
    if chat.name == "general" and chat.kind == "channel":
        raise HTTPException(status_code=400, detail="cannot delete general channel")
    name = chat.name
    _delete_chat(db, chat_id)
    db.commit()
    return {"status": "ok", "id": chat_id, "name": name}


@router.post("/chats/{chat_id}/members")
def add_chat_member(
    chat_id: int,
    body: ChatMemberIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    chat = require_chat_access(db, auth, chat_id)
    if chat.kind == "private":
        raise HTTPException(status_code=400, detail="private rooms cannot add members")
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None or user.tenant_id != auth.tenant_id:
        raise HTTPException(
            status_code=404,
            detail="user not found in workspace - invite them first",
        )
    ensure_chat_member(db, tenant_id=auth.tenant_id, chat_id=chat.id, user_id=user.id)
    db.commit()
    return {"status": "ok", "user_id": user.id, "email": user.email}


@router.get("/chats/{chat_id}/members")
def list_chat_members(
    chat_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    chat = require_chat_access(db, auth, chat_id)
    if chat.kind == "private":
        rows = (
            db.query(User)
            .join(ChatMember, ChatMember.user_id == User.id)
            .filter(ChatMember.chat_id == chat_id)
            .all()
        )
    else:
        rows = (
            db.query(User)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .filter(WorkspaceMember.tenant_id == auth.tenant_id)
            .order_by(User.email.asc())
            .all()
        )
    return [{"user_id": u.id, "email": u.email, "name": u.name} for u in rows]


@router.get("/chats/{chat_id}/messages")
def list_messages(
    chat_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    after_id: int = 0,
    since: str | None = None,
    limit: int = 100,
):
    require_chat_access(db, auth, chat_id)
    from app.services.chat_clear import cleared_before_id

    floor = cleared_before_id(db, chat_id=chat_id, user_id=auth.user_id)
    effective_after = max(after_id, floor)
    q = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.tenant_id == auth.tenant_id,
            ChatMessage.id > effective_after,
            visible_messages_filter(auth.user_id),
            ChatMessage.deleted_at.is_(None),
        )
        .order_by(ChatMessage.id.asc())
        .limit(min(limit, 200))
    )
    rows = list(q.all())
    seen = {m.id for m in rows}

    # Sync edits/deletes for messages the client already has
    if since and after_id > 0:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since_dt = None
        if since_dt is not None:
            mutated = (
                db.query(ChatMessage)
                .filter(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.tenant_id == auth.tenant_id,
                    ChatMessage.id > floor,
                    ChatMessage.id <= after_id,
                    visible_messages_filter(auth.user_id),
                    or_(
                        ChatMessage.edited_at > since_dt,
                        ChatMessage.deleted_at > since_dt,
                    ),
                )
                .order_by(ChatMessage.id.asc())
                .limit(200)
                .all()
            )
            for m in mutated:
                if m.id not in seen:
                    rows.append(m)
                    seen.add(m.id)
            rows.sort(key=lambda m: m.id)
    return [message_to_dict(db, m) for m in rows]


def _own_user_message(
    db: Session, auth: AuthContext, chat_id: int, message_id: int
) -> ChatMessage:
    require_chat_access(db, auth, chat_id)
    msg = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.tenant_id == auth.tenant_id,
        )
        .one_or_none()
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="message not found")
    if msg.agent_slug or msg.sender_user_id != auth.user_id:
        raise HTTPException(status_code=403, detail="can only change your own messages")
    if msg.deleted_at is not None:
        raise HTTPException(status_code=400, detail="message already deleted")
    return msg


def _soft_delete_message(db: Session, msg: ChatMessage) -> None:
    arts = (
        db.query(ChatAttachment)
        .filter(ChatAttachment.message_id == msg.id)
        .all()
    )
    for a in arts:
        delete_file(a.storage_path)
        db.delete(a)
    msg.body = ""
    msg.audio_url = None
    msg.deleted_at = utcnow()


def _truncate_messages_after(
    db: Session, *, chat_id: int, tenant_id: int, after_id: int
) -> list[int]:
    """Soft-delete every message after after_id in this chat. Returns removed ids."""
    later = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.id > after_id,
            ChatMessage.deleted_at.is_(None),
        )
        .order_by(ChatMessage.id.asc())
        .all()
    )
    removed: list[int] = []
    for m in later:
        removed.append(m.id)
        _soft_delete_message(db, m)
    return removed


def _delete_following_agent_replies(
    db: Session, *, chat_id: int, tenant_id: int, after_id: int
) -> list[int]:
    """Soft-delete consecutive agent replies right after after_id (until next user msg)."""
    later = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.id > after_id,
            ChatMessage.deleted_at.is_(None),
        )
        .order_by(ChatMessage.id.asc())
        .all()
    )
    removed: list[int] = []
    for m in later:
        if not m.agent_slug:
            break
        removed.append(m.id)
        _soft_delete_message(db, m)
    return removed


@router.patch("/chats/{chat_id}/messages/{message_id}")
def edit_message(
    chat_id: int,
    message_id: int,
    body: MessagePatch,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Edit own message (ChatGPT-style): truncate everything after it, then re-process."""
    chat = require_chat_access(db, auth, chat_id)
    msg = _own_user_message(db, auth, chat_id, message_id)
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="body required")

    removed_ids = _truncate_messages_after(
        db, chat_id=chat_id, tenant_id=auth.tenant_id, after_id=msg.id
    )
    msg.body = text
    msg.edited_at = utcnow()
    db.flush()

    replies: list[ChatMessage] = []
    # Re-process as if newly sent (skip /clear - editing should not wipe the room)
    skip_rerun = text.startswith("/") and text[1:].strip().lower().startswith(
        ("clear", "clear chat", "clear messages")
    )
    if not skip_rerun:
        replies, _, _, _ = handle_chat_message(
            db,
            auth=auth,
            chat=chat,
            user_message=msg,
            speak=False,
        )

    db.commit()
    db.refresh(msg)
    for r in replies:
        db.refresh(r)

    return {
        "message": message_to_dict(db, msg),
        "removed_ids": removed_ids,
        "replies": [message_to_dict(db, r) for r in replies],
    }


@router.delete("/chats/{chat_id}/messages/{message_id}")
def delete_message(
    chat_id: int,
    message_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    msg = _own_user_message(db, auth, chat_id, message_id)
    # Drop the LLM/agent replies that followed this ask (stop at next user message)
    removed_ids = _delete_following_agent_replies(
        db, chat_id=chat_id, tenant_id=auth.tenant_id, after_id=msg.id
    )
    _soft_delete_message(db, msg)
    db.commit()
    db.refresh(msg)
    return {
        "message": message_to_dict(db, msg),
        "removed_ids": removed_ids,
    }


@router.post("/chats/{chat_id}/messages")
def post_message(
    chat_id: int,
    body: MessageIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    chat = require_chat_access(db, auth, chat_id)
    text = (body.body or "").strip()
    attachment_ids = list(dict.fromkeys(body.attachment_ids or []))  # stable unique
    if not text and not attachment_ids:
        raise HTTPException(status_code=400, detail="message body or attachments required")
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(
            status_code=400,
            detail=f"at most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
        )

    linked: list[ChatAttachment] = []
    if attachment_ids:
        linked = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.id.in_(attachment_ids),
                ChatAttachment.tenant_id == auth.tenant_id,
                ChatAttachment.chat_id == chat_id,
                ChatAttachment.uploader_user_id == auth.user_id,
                ChatAttachment.message_id.is_(None),
            )
            .all()
        )
        if len(linked) != len(attachment_ids):
            raise HTTPException(
                status_code=400,
                detail="invalid attachment_ids (must be yours, unlinked, same chat)",
            )

    user_msg = ChatMessage(
        tenant_id=auth.tenant_id,
        chat_id=chat_id,
        sender_user_id=auth.user_id,
        agent_slug=None,
        body=text,
        visibility="public",
    )
    db.add(user_msg)
    db.flush()
    for row in linked:
        row.message_id = user_msg.id
    db.flush()

    replies, created_chat_id, deleted_chat_id, cleared = handle_chat_message(
        db,
        auth=auth,
        chat=chat,
        user_message=user_msg,
        speak=body.speak,
    )
    if (user_msg.visibility or "public") == "public" and not cleared and text:
        record_mentions(
            db,
            tenant_id=auth.tenant_id,
            chat_id=chat_id,
            message_id=user_msg.id,
            from_user_id=auth.user_id,
            body=user_msg.body,
        )
    db.commit()
    return {
        "user_message_id": None if deleted_chat_id == chat_id or cleared else user_msg.id,
        "created_chat_id": created_chat_id,
        "deleted_chat_id": deleted_chat_id,
        "cleared": cleared,
        "replies": [
            {
                "id": r.id,
                "agent": r.agent_slug,
                "body": r.body,
                "audio_url": r.audio_url,
                "visibility": getattr(r, "visibility", None) or "public",
                "created_at": _iso_utc(r.created_at),
            }
            for r in replies
        ],
    }
