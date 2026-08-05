from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMessage, Objective


def get_general_chat(db: Session, *, tenant_id: int, project_id: int) -> Chat | None:
    return (
        db.query(Chat)
        .filter(
            Chat.tenant_id == tenant_id,
            Chat.project_id == project_id,
            Chat.name == "general",
            Chat.kind == "channel",
        )
        .one_or_none()
    )


def post_general(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    body: str,
    agent_slug: str = "lead",
) -> ChatMessage | None:
    chat = get_general_chat(db, tenant_id=tenant_id, project_id=project_id)
    if chat is None:
        return None
    msg = ChatMessage(
        tenant_id=tenant_id,
        chat_id=chat.id,
        sender_user_id=None,
        agent_slug=agent_slug,
        body=body,
        audio_url=None,
    )
    db.add(msg)
    db.flush()
    return msg


def confirm_footer_for_objective(obj: Objective) -> str:
    return (
        f"\n\nLinked objective #{obj.id}: {obj.title}\n"
        f"[[confirm:{obj.id}]]\n"
        f"Click Yes to mark done, or type: yes {obj.id}"
    )
