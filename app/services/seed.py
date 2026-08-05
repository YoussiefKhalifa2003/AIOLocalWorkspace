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
from app.services.passwords import DEMO_PASSWORD, hash_password
from app.services.rooms import ensure_project_rooms
from app.services.workspace_invite import rotate_invite_token


def _set_demo_password(user: User) -> None:
    if not user.password_hash:
        user.password_hash = hash_password(DEMO_PASSWORD)


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
        rotate_invite_token(db, t1)

        u1 = db.query(User).filter_by(api_key=settings.demo_api_key).one_or_none()
        if u1 is None:
            u1 = User(
                tenant_id=t1.id,
                name="Demo User A",
                email="a@local.test",
                api_key=settings.demo_api_key,
                password_hash=hash_password(DEMO_PASSWORD),
            )
            db.add(u1)
            db.flush()
        else:
            if not u1.email or (u1.email.startswith("user") and u1.email.endswith("@local.test")):
                conflict = db.query(User).filter(User.email == "a@local.test", User.id != u1.id).first()
                if conflict is None:
                    u1.email = "a@local.test"
            _set_demo_password(u1)

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

        # Second project in same workspace so the owner dashboard has a project picker
        p1b = (
            db.query(Project)
            .filter_by(tenant_id=t1.id, name="ops")
            .one_or_none()
        )
        if p1b is None:
            p1b = Project(tenant_id=t1.id, name="ops", github_repo=None)
            db.add(p1b)
            db.flush()
        ensure_project_rooms(db, t1.id, p1b.id)

        # Second member in same workspace for hybrid demos
        u_member = db.query(User).filter_by(email="omar@local.test").one_or_none()
        if u_member is None:
            u_member = User(
                tenant_id=t1.id,
                name="Omar",
                email="omar@local.test",
                api_key=f"u_{secrets.token_urlsafe(12)}",
                password_hash=hash_password(DEMO_PASSWORD),
            )
            db.add(u_member)
            db.flush()
        else:
            u_member.tenant_id = t1.id
            _set_demo_password(u_member)
        _ensure_member(db, t1.id, u_member.id, role="member")

        u_sara = db.query(User).filter_by(email="sara@local.test").one_or_none()
        if u_sara is None:
            u_sara = User(
                tenant_id=t1.id,
                name="Sara",
                email="sara@local.test",
                api_key=f"u_{secrets.token_urlsafe(12)}",
                password_hash=hash_password(DEMO_PASSWORD),
            )
            db.add(u_sara)
            db.flush()
        else:
            u_sara.tenant_id = t1.id
            _set_demo_password(u_sara)
        _ensure_member(db, t1.id, u_sara.id, role="member")

        t2 = db.query(Tenant).filter_by(name="demo-tenant-b").one_or_none()
        if t2 is None:
            t2 = Tenant(name="demo-tenant-b")
            db.add(t2)
            db.flush()
        rotate_invite_token(db, t2)
        u2 = db.query(User).filter_by(api_key=settings.demo_api_key_b).one_or_none()
        if u2 is None:
            u2 = User(
                tenant_id=t2.id,
                name="Demo User B",
                email="b@local.test",
                api_key=settings.demo_api_key_b,
                password_hash=hash_password(DEMO_PASSWORD),
            )
            db.add(u2)
            db.flush()
        else:
            if not u2.email or u2.email.startswith("user"):
                conflict = db.query(User).filter(User.email == "b@local.test", User.id != u2.id).first()
                if conflict is None:
                    u2.email = "b@local.test"
            _set_demo_password(u2)
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

        from app.services.workspace_invite import invite_link_for_tenant

        db.commit()
        inv = invite_link_for_tenant(db, t1)
        db.commit()
        return {
            "tenant_a": t1.id,
            "user_a": u1.id,
            "email_a": u1.email,
            "project_a": p1.id,
            "project_ops": p1b.id,
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
            "invite_token": inv["token"],
            "invite_url": inv["invite_url"],
            "demo_password": DEMO_PASSWORD,
        }
    finally:
        if close:
            db.close()


def register_via_invite_token(
    db: Session,
    *,
    token: str,
    email: str,
    password: str,
    name: str,
) -> dict:
    """Register a new member via the workspace invite link."""
    import re

    from app.services.passwords import hash_password
    from app.services.workspace_invite import tenant_by_invite_token

    tenant = tenant_by_invite_token(db, token)
    if tenant is None:
        raise ValueError("invalid or expired invite link")

    email_norm = email.strip().lower().strip("<>\"'.,;:!?)(")
    if "@" not in email_norm or "." not in email_norm.split("@")[-1]:
        raise ValueError("invalid email")
    if len(password or "") < 4:
        raise ValueError("password must be at least 4 characters")

    display = (name or "").strip()
    if not display:
        raise ValueError("name is required - teammates will @ you by this name")
    if " " in display or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{0,39}", display):
        raise ValueError(
            "name must be one word (letters/numbers, no spaces) - this is your @handle"
        )

    existing = db.query(User).filter(User.email == email_norm).one_or_none()
    if existing is not None:
        raise ValueError("email already registered - log in instead")

    # Unique @handle within workspace
    taken = (
        db.query(User)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .filter(
            WorkspaceMember.tenant_id == tenant.id,
            User.name == display,
        )
        .first()
    )
    if taken is not None:
        raise ValueError(f"name @{display} is already taken - pick another")

    user, priv_id = _provision_accepted_user(
        db,
        tenant_id=tenant.id,
        email=email_norm,
        name=display,
        password_hash=hash_password(password),
    )
    from app.services.workspace_invite import consume_invite_token

    consume_invite_token(db, tenant, token)
    return {
        "user_id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "name": user.name,
        "api_key": user.api_key,
        "private_chat_id": priv_id,
        "status": "registered",
    }


def _provision_accepted_user(
    db: Session,
    *,
    tenant_id: int,
    email: str,
    name: str | None = None,
    password_hash: str | None = None,
) -> tuple[User, int]:
    """Create user + memberships + private room. Returns (user, private_chat_id)."""
    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant_id,
            name=name or email_norm.split("@")[0],
            email=email_norm,
            api_key=f"u_{secrets.token_urlsafe(16)}",
            password_hash=password_hash,
        )
        db.add(user)
        db.flush()
    else:
        if user.tenant_id != tenant_id:
            user.tenant_id = tenant_id
        if password_hash and not user.password_hash:
            user.password_hash = password_hash
        if name:
            user.name = name

    _ensure_member(db, tenant_id, user.id, role="member")
    db.flush()

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
        _ensure_general(db, tenant_id, project_id)

    channels = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant_id, Chat.kind == "channel")
        .all()
    )
    for ch in channels:
        ensure_channel_membership(db, ch)

    priv = ensure_private_room(db, tenant_id=tenant_id, project_id=project_id, user=user)
    db.flush()
    return user, priv.id


def delete_user_by_email(db: Session, email: str) -> dict:
    """Remove a user and related rows so they can re-register cleanly."""
    from app.db.models import (
        AgentMetric,
        Artifact,
        AuditEvent,
        ChatMention,
        ChatMessage,
        FileClaim,
        Invite,
        Job,
        Objective,
        RoomMessage,
        TaskItem,
        WorkIssue,
        WorkRequest,
    )

    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).one_or_none()
    deleted_invite_rows = (
        db.query(Invite).filter(Invite.email == email_norm).delete(synchronize_session=False)
    )
    if user is None:
        return {"email": email_norm, "deleted": False, "invites_cleared": deleted_invite_rows}

    uid = user.id

    # Private rooms owned by this user
    priv_chats = (
        db.query(Chat)
        .filter(Chat.owner_user_id == uid, Chat.kind == "private")
        .all()
    )
    for ch in priv_chats:
        db.query(ChatMention).filter(ChatMention.chat_id == ch.id).delete(synchronize_session=False)
        db.query(ChatMessage).filter(ChatMessage.chat_id == ch.id).delete(synchronize_session=False)
        db.query(ChatMember).filter(ChatMember.chat_id == ch.id).delete(synchronize_session=False)
        db.query(WorkIssue).filter(WorkIssue.source_chat_id == ch.id).update(
            {WorkIssue.source_chat_id: None}, synchronize_session=False
        )
        db.delete(ch)
    db.flush()

    db.query(ChatMention).filter(
        (ChatMention.mentioned_user_id == uid) | (ChatMention.from_user_id == uid)
    ).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.sender_user_id == uid).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.whisper_user_id == uid).update(
        {ChatMessage.whisper_user_id: None}, synchronize_session=False
    )
    db.query(ChatMember).filter(ChatMember.user_id == uid).delete(synchronize_session=False)

    db.query(FileClaim).filter(FileClaim.user_id == uid).delete(synchronize_session=False)
    db.query(Objective).filter(
        (Objective.user_id == uid) | (Objective.assignee_user_id == uid)
    ).delete(synchronize_session=False)
    db.query(WorkIssue).filter(WorkIssue.owner_user_id == uid).delete(synchronize_session=False)
    db.query(TaskItem).filter(TaskItem.owner_user_id == uid).update(
        {TaskItem.owner_user_id: None}, synchronize_session=False
    )
    db.query(AgentMetric).filter(AgentMetric.user_id == uid).update(
        {AgentMetric.user_id: None}, synchronize_session=False
    )

    # Work requests and their jobs/artifacts/tasks
    req_ids = [
        r.id
        for r in db.query(WorkRequest.id).filter(WorkRequest.user_id == uid).all()
    ]
    if req_ids:
        job_ids = [
            j.id
            for j in db.query(Job.id).filter(Job.request_id.in_(req_ids)).all()
        ]
        if job_ids:
            db.query(Artifact).filter(Artifact.job_id.in_(job_ids)).delete(synchronize_session=False)
            db.query(AgentMetric).filter(AgentMetric.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
            db.query(AuditEvent).filter(AuditEvent.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
            db.query(RoomMessage).filter(RoomMessage.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
            db.query(TaskItem).filter(TaskItem.job_id.in_(job_ids)).update(
                {TaskItem.job_id: None}, synchronize_session=False
            )
            # Clear parent refs then delete jobs
            db.query(Job).filter(Job.parent_job_id.in_(job_ids)).update(
                {Job.parent_job_id: None}, synchronize_session=False
            )
            db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)
        db.query(TaskItem).filter(TaskItem.request_id.in_(req_ids)).update(
            {TaskItem.request_id: None}, synchronize_session=False
        )
        db.query(Objective).filter(Objective.request_id.in_(req_ids)).update(
            {Objective.request_id: None}, synchronize_session=False
        )
        db.query(AuditEvent).filter(AuditEvent.request_id.in_(req_ids)).delete(
            synchronize_session=False
        )
        db.query(WorkRequest).filter(WorkRequest.id.in_(req_ids)).delete(synchronize_session=False)

    db.query(Invite).filter(Invite.created_by_user_id == uid).delete(synchronize_session=False)
    db.query(WorkspaceMember).filter(WorkspaceMember.user_id == uid).delete(synchronize_session=False)

    db.delete(user)
    db.flush()
    return {"email": email_norm, "deleted": True, "user_id": uid, "invites_cleared": deleted_invite_rows}
