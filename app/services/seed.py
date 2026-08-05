from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Chat,
    ChatMember,
    Project,
    Tenant,
    User,
    WorkspaceMember,
)
from app.db.session import SessionLocal, init_db
from app.services.chat_access import ensure_channel_membership, ensure_private_room
from app.services.rooms import ensure_project_rooms


def _ensure_member(db: Session, tenant_id: int, user_id: int, role: str = "member") -> None:
    existing = (
        db.query(WorkspaceMember)
        .filter_by(tenant_id=tenant_id, user_id=user_id)
        .one_or_none()
    )
    if existing is None:
        db.add(WorkspaceMember(tenant_id=tenant_id, user_id=user_id, role=role))


def _ensure_general(db: Session, tenant_id: int, project_id: int) -> Chat:
    chat = (
        db.query(Chat)
        .filter_by(tenant_id=tenant_id, name="general", kind="channel")
        .one_or_none()
    )
    if chat is None:
        chat = Chat(
            tenant_id=tenant_id,
            project_id=project_id,
            name="general",
            kind="channel",
            owner_user_id=None,
        )
        db.add(chat)
        db.flush()
    ensure_channel_membership(db, chat)
    return chat


def seed_demo_data(db: Session | None = None) -> dict:
    close = False
    if db is None:
        init_db()
        db = SessionLocal()
        close = True
    settings = get_settings()
    try:
        t1 = db.query(Tenant).filter_by(name="demo-tenant-a").one_or_none()
        if t1 is None:
            t1 = Tenant(name="demo-tenant-a")
            db.add(t1)
            db.flush()

        u1 = db.query(User).filter_by(api_key=settings.demo_api_key).one_or_none()
        if u1 is None:
            u1 = User(
                tenant_id=t1.id,
                name="Demo User A",
                email="a@local.test",
                api_key=settings.demo_api_key,
            )
            db.add(u1)
            db.flush()
        else:
            if not u1.email or (u1.email.startswith("user") and u1.email.endswith("@local.test")):
                conflict = db.query(User).filter(User.email == "a@local.test", User.id != u1.id).first()
                if conflict is None:
                    u1.email = "a@local.test"

        p1 = (
            db.query(Project)
            .filter_by(tenant_id=t1.id, name="demo-project")
            .one_or_none()
        )
        if p1 is None:
            p1 = Project(
                tenant_id=t1.id,
                name="demo-project",
                github_repo=settings.github_repo.strip() or "example/demo-project",
            )
            db.add(p1)
            db.flush()
        else:
            # Keep token/repo from env for local demos
            if settings.github_repo.strip():
                p1.github_repo = settings.github_repo.strip()
            if settings.github_token.strip():
                p1.github_token = settings.github_token.strip()
        ensure_project_rooms(db, t1.id, p1.id)
        _ensure_member(db, t1.id, u1.id, role="owner")

        # Second member in same workspace for hybrid demos
        u_member = db.query(User).filter_by(email="omar@local.test").one_or_none()
        if u_member is None:
            u_member = User(
                tenant_id=t1.id,
                name="Omar",
                email="omar@local.test",
                api_key=f"u_{secrets.token_urlsafe(12)}",
            )
            db.add(u_member)
            db.flush()
        else:
            u_member.tenant_id = t1.id
        _ensure_member(db, t1.id, u_member.id, role="member")

        u_sara = db.query(User).filter_by(email="sara@local.test").one_or_none()
        if u_sara is None:
            u_sara = User(
                tenant_id=t1.id,
                name="Sara",
                email="sara@local.test",
                api_key=f"u_{secrets.token_urlsafe(12)}",
            )
            db.add(u_sara)
            db.flush()
        else:
            u_sara.tenant_id = t1.id
        _ensure_member(db, t1.id, u_sara.id, role="member")

        t2 = db.query(Tenant).filter_by(name="demo-tenant-b").one_or_none()
        if t2 is None:
            t2 = Tenant(name="demo-tenant-b")
            db.add(t2)
            db.flush()
        u2 = db.query(User).filter_by(api_key=settings.demo_api_key_b).one_or_none()
        if u2 is None:
            u2 = User(
                tenant_id=t2.id,
                name="Demo User B",
                email="b@local.test",
                api_key=settings.demo_api_key_b,
            )
            db.add(u2)
            db.flush()
        else:
            if not u2.email or u2.email.startswith("user"):
                conflict = db.query(User).filter(User.email == "b@local.test", User.id != u2.id).first()
                if conflict is None:
                    u2.email = "b@local.test"
        p2 = (
            db.query(Project)
            .filter_by(tenant_id=t2.id, name="other-project")
            .one_or_none()
        )
        if p2 is None:
            p2 = Project(tenant_id=t2.id, name="other-project", github_repo=None)
            db.add(p2)
            db.flush()
        ensure_project_rooms(db, t2.id, p2.id)
        _ensure_member(db, t2.id, u2.id, role="owner")

        general = _ensure_general(db, t1.id, p1.id)
        priv_a = ensure_private_room(db, tenant_id=t1.id, project_id=p1.id, user=u1)
        priv_omar = ensure_private_room(db, tenant_id=t1.id, project_id=p1.id, user=u_member)
        priv_sara = ensure_private_room(db, tenant_id=t1.id, project_id=p1.id, user=u_sara)
        _ensure_general(db, t2.id, p2.id)
        ensure_private_room(db, tenant_id=t2.id, project_id=p2.id, user=u2)

        # Sample owned work for Lead catch-up / board demos
        from app.db.models import Objective, WorkIssue

        if (
            db.query(Objective)
            .filter(Objective.tenant_id == t1.id, Objective.user_id == u_member.id)
            .count()
            == 0
        ):
            db.add(
                Objective(
                    tenant_id=t1.id,
                    project_id=p1.id,
                    user_id=u_member.id,
                    assignee_user_id=u_member.id,
                    title="Research Dubai metro tips",
                    status="doing",
                    sort_order=1,
                )
            )
            db.add(
                Objective(
                    tenant_id=t1.id,
                    project_id=p1.id,
                    user_id=u_member.id,
                    assignee_user_id=u_member.id,
                    title="Fix app/api/chats.py auth edge case",
                    status="todo",
                    sort_order=2,
                )
            )
            db.add(
                WorkIssue(
                    tenant_id=t1.id,
                    project_id=p1.id,
                    owner_user_id=u_member.id,
                    title="Waiting on station map source",
                    detail="Need official RTA PDF",
                    status="open",
                    source_chat_id=priv_omar.id,
                )
            )
        if (
            db.query(Objective)
            .filter(Objective.tenant_id == t1.id, Objective.user_id == u_sara.id)
            .count()
            == 0
        ):
            db.add(
                Objective(
                    tenant_id=t1.id,
                    project_id=p1.id,
                    user_id=u_sara.id,
                    assignee_user_id=u_sara.id,
                    title="Draft writing brief",
                    status="done",
                    done=True,
                    sort_order=1,
                )
            )
            db.add(
                Objective(
                    tenant_id=t1.id,
                    project_id=p1.id,
                    user_id=u_sara.id,
                    assignee_user_id=u_sara.id,
                    title="Blocked on design review",
                    status="blocked",
                    sort_order=2,
                )
            )

        # Drop legacy shared "work" channel if present (optional cleanup)
        legacy = (
            db.query(Chat)
            .filter(Chat.tenant_id == t1.id, Chat.name == "work", Chat.kind == "channel")
            .all()
        )
        for ch in legacy:
            db.query(ChatMember).filter(ChatMember.chat_id == ch.id).delete()
            db.delete(ch)

        db.commit()
        return {
            "tenant_a": t1.id,
            "user_a": u1.id,
            "email_a": u1.email,
            "project_a": p1.id,
            "api_key_a": u1.api_key,
            "chat_general": general.id,
            "chat_private_a": priv_a.id,
            "user_omar": u_member.id,
            "email_omar": u_member.email,
            "api_key_omar": u_member.api_key,
            "chat_private_omar": priv_omar.id,
            "user_sara": u_sara.id,
            "email_sara": u_sara.email,
            "api_key_sara": u_sara.api_key,
            "chat_private_sara": priv_sara.id,
            "tenant_b": t2.id,
            "user_b": u2.id,
            "email_b": u2.email,
            "project_b": p2.id,
            "api_key_b": u2.api_key,
            "join_key": settings.workspace_join_key or settings.demo_api_key,
        }
    finally:
        if close:
            db.close()


def invite_user_by_email(
    db: Session,
    *,
    tenant_id: int,
    inviter_user_id: int,
    email: str,
    name: str | None = None,
) -> dict:
    email_norm = email.strip().lower()
    # Strip wrapping punctuation from chat paste
    email_norm = email_norm.strip("<>\"'.,;:!?)(")
    if "@" not in email_norm or "." not in email_norm.split("@")[-1]:
        raise ValueError("invalid email — use something like name@gmail.com")
    join_key = get_settings().workspace_join_key or get_settings().demo_api_key
    inviter = db.query(User).filter(User.id == inviter_user_id).one_or_none()
    inviter_email = inviter.email if inviter else "a teammate"

    user = db.query(User).filter(User.email == email_norm).one_or_none()
    is_new = False
    if user is None:
        is_new = True
        user = User(
            tenant_id=tenant_id,
            name=name or email_norm.split("@")[0],
            email=email_norm,
            api_key=f"u_{secrets.token_urlsafe(16)}",
        )
        db.add(user)
        db.flush()
    elif user.tenant_id != tenant_id:
        user.tenant_id = tenant_id
    _ensure_member(db, tenant_id, user.id, role="member")
    db.flush()
    from app.db.models import Invite

    invite = Invite(
        tenant_id=tenant_id,
        email=email_norm,
        token=secrets.token_urlsafe(12),
        status="accepted",
        created_by_user_id=inviter_user_id,
    )
    db.add(invite)

    project = (
        db.query(Project)
        .filter(Project.tenant_id == tenant_id)
        .order_by(Project.id.asc())
        .first()
    )
    project_id = project.id if project else 1

    general = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant_id, Chat.name == "general", Chat.kind == "channel")
        .order_by(Chat.id.asc())
        .first()
    )
    if general is None:
        general = _ensure_general(db, tenant_id, project_id)

    # Add invitee to all existing channels (idempotent)
    channels = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant_id, Chat.kind == "channel")
        .all()
    )
    for ch in channels:
        ensure_channel_membership(db, ch)

    priv = ensure_private_room(db, tenant_id=tenant_id, project_id=project_id, user=user)
    db.flush()

    from app.services.invite_email import send_invite_email

    emailed, mail_detail = send_invite_email(
        to_email=email_norm,
        inviter_email=inviter_email,
        join_key=join_key,
    )
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "api_key_issued": join_key,
        "is_new": is_new,
        "private_chat_id": priv.id,
        "email_sent": emailed,
        "email_detail": mail_detail,
        "note": "They log in with their email + the shared workspace key.",
    }
