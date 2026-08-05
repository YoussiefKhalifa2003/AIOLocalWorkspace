from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMember, ChatMessage, User, WorkspaceMember
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.chat_access import (
    chat_to_dict,
    ensure_channel_membership,
    ensure_chat_member,
    ensure_private_room,
    list_visible_chats,
    require_chat_access,
)
from app.services.orchestrator import handle_chat_message
from app.services.chat_visibility import message_to_dict, visible_messages_filter
from app.services.mentions import record_mentions

router = APIRouter(tags=["chats"])


class ChatIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "channel"
    project_id: int | None = 1


class MessageIn(BaseModel):
    body: str = Field(min_length=1)
    speak: bool = False


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
            detail="user not found in workspace — invite them first",
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
    limit: int = 100,
):
    require_chat_access(db, auth, chat_id)
    q = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.tenant_id == auth.tenant_id,
            ChatMessage.id > after_id,
            visible_messages_filter(auth.user_id),
        )
        .order_by(ChatMessage.id.asc())
        .limit(min(limit, 200))
    )
    rows = q.all()
    return [message_to_dict(db, m) for m in rows]


@router.post("/chats/{chat_id}/messages")
def post_message(
    chat_id: int,
    body: MessageIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    chat = require_chat_access(db, auth, chat_id)
    user_msg = ChatMessage(
        tenant_id=auth.tenant_id,
        chat_id=chat_id,
        sender_user_id=auth.user_id,
        agent_slug=None,
        body=body.body.strip(),
        visibility="public",
    )
    db.add(user_msg)
    db.flush()
    replies, created_chat_id, deleted_chat_id, cleared = handle_chat_message(
        db,
        auth=auth,
        chat=chat,
        user_message=user_msg,
        speak=body.speak,
    )
    if (user_msg.visibility or "public") == "public" and not cleared:
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
            }
            for r in replies
        ],
    }
