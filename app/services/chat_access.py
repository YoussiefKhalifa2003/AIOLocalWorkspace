from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMember, User, WorkspaceMember
from app.services.auth import AuthContext


def is_workspace_owner(db: Session, auth: AuthContext) -> bool:
    row = (
        db.query(WorkspaceMember)
        .filter_by(tenant_id=auth.tenant_id, user_id=auth.user_id)
        .one_or_none()
    )
    return bool(row and row.role == "owner")


def workspace_user_ids(db: Session, tenant_id: int) -> list[int]:
    return [
        m.user_id
        for m in db.query(WorkspaceMember).filter_by(tenant_id=tenant_id).all()
    ]


def ensure_chat_member(db: Session, *, tenant_id: int, chat_id: int, user_id: int) -> None:
    existing = (
        db.query(ChatMember)
        .filter_by(chat_id=chat_id, user_id=user_id)
        .one_or_none()
    )
    if existing is None:
        db.add(ChatMember(tenant_id=tenant_id, chat_id=chat_id, user_id=user_id))
        # Session uses autoflush=False - flush so later ensure_* calls see this row
        db.flush()


def ensure_channel_membership(db: Session, chat: Chat) -> None:
    """Add all workspace members to a shared channel."""
    for uid in workspace_user_ids(db, chat.tenant_id):
        ensure_chat_member(db, tenant_id=chat.tenant_id, chat_id=chat.id, user_id=uid)


def ensure_private_room(
    db: Session,
    *,
    tenant_id: int,
    project_id: int | None,
    user: User,
) -> Chat:
    """Idempotent private room for a workspace member."""
    existing = (
        db.query(Chat)
        .filter(
            Chat.tenant_id == tenant_id,
            Chat.kind == "private",
            Chat.owner_user_id == user.id,
        )
        .one_or_none()
    )
    if existing is not None:
        ensure_chat_member(db, tenant_id=tenant_id, chat_id=existing.id, user_id=user.id)
        return existing
    chat = Chat(
        tenant_id=tenant_id,
        project_id=project_id,
        name=f"private - {user.email}",
        kind="private",
        mode="llm",
        owner_user_id=user.id,
    )
    db.add(chat)
    db.flush()
    ensure_chat_member(db, tenant_id=tenant_id, chat_id=chat.id, user_id=user.id)
    return chat


def _is_member(db: Session, chat_id: int, user_id: int) -> bool:
    return (
        db.query(ChatMember)
        .filter_by(chat_id=chat_id, user_id=user_id)
        .one_or_none()
        is not None
    )


def can_access_chat(db: Session, auth: AuthContext, chat: Chat) -> bool:
    if chat.tenant_id != auth.tenant_id:
        return False
    if chat.kind == "private":
        # Only the private owner (via membership). Lead cannot read others' private rooms.
        return _is_member(db, chat.id, auth.user_id) and chat.owner_user_id == auth.user_id
    # channel: any workspace member
    return (
        db.query(WorkspaceMember)
        .filter_by(tenant_id=auth.tenant_id, user_id=auth.user_id)
        .one_or_none()
        is not None
    )


def require_chat_access(db: Session, auth: AuthContext, chat_id: int) -> Chat:
    chat = (
        db.query(Chat)
        .filter(Chat.id == chat_id, Chat.tenant_id == auth.tenant_id)
        .one_or_none()
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    if not can_access_chat(db, auth, chat):
        raise HTTPException(status_code=403, detail="not allowed to access this chat")
    return chat


def list_visible_chats(db: Session, auth: AuthContext) -> list[Chat]:
    rows = (
        db.query(Chat)
        .filter(Chat.tenant_id == auth.tenant_id)
        .order_by(Chat.id.asc())
        .all()
    )
    return [c for c in rows if can_access_chat(db, auth, c)]


def chat_mode_of(chat: dict | object | None) -> str:
    """Resolve ops|llm for a chat dict or ORM row."""
    if chat is None:
        return "ops"
    if isinstance(chat, dict):
        kind = str(chat.get("kind") or "")
        mode = str(chat.get("mode") or "").strip().lower()
    else:
        kind = str(getattr(chat, "kind", None) or "")
        mode = str(getattr(chat, "mode", None) or "").strip().lower()
    if mode in ("ops", "llm"):
        return mode
    return "llm" if kind == "private" else "ops"


def is_default_private_room(chat: dict | object | None) -> bool:
    """Seeded 'my room' — never user-deletable."""
    if chat is None:
        return False
    if isinstance(chat, dict):
        kind = str(chat.get("kind") or "")
        name = str(chat.get("name") or "").lower()
    else:
        kind = str(getattr(chat, "kind", None) or "")
        name = str(getattr(chat, "name", None) or "").lower()
    return kind == "private" and name.startswith("private -")


def can_delete_chat(
    chat: dict | object | None,
    user_id: int,
    *,
    is_workspace_owner: bool = False,
) -> bool:
    """Creator may delete their chat; general + default private room are protected.

    Workspace owners may also remove legacy public channels with no recorded creator.
    """
    if chat is None or not user_id:
        return False
    if isinstance(chat, dict):
        kind = str(chat.get("kind") or "")
        name = str(chat.get("name") or "")
        owner = int(chat.get("owner_user_id") or 0)
    else:
        kind = str(getattr(chat, "kind", None) or "")
        name = str(getattr(chat, "name", None) or "")
        owner = int(getattr(chat, "owner_user_id", None) or 0)
    if kind == "channel" and name == "general":
        return False
    if is_default_private_room(chat):
        return False
    if owner == int(user_id):
        return True
    # Older public channels sometimes have owner_user_id NULL.
    if is_workspace_owner and kind == "channel" and owner == 0:
        return True
    return False


def chat_to_dict(c: Chat) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "mode": chat_mode_of(c),
        "project_id": c.project_id,
        "owner_user_id": c.owner_user_id,
    }
