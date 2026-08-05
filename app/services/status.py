from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Objective, TaskItem, User, WorkIssue, WorkspaceMember
from app.services.auth import AuthContext
from app.services.chat_access import is_workspace_owner


def resolve_member(db: Session, tenant_id: int, token: str) -> User | None:
    """Resolve @Omar / email local-part / email / name to a workspace user."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    members = (
        db.query(User)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .filter(WorkspaceMember.tenant_id == tenant_id)
        .all()
    )
    for u in members:
        local = u.email.split("@")[0].lower()
        if u.email.lower() == raw or local == raw or u.name.lower() == raw:
            return u
    # unique prefix match on local part / name
    hits = []
    for u in members:
        local = u.email.split("@")[0].lower()
        if local.startswith(raw) or u.name.lower().startswith(raw):
            hits.append(u)
    return hits[0] if len(hits) == 1 else None


def objectives_for_user(
    db: Session, *, tenant_id: int, project_id: int, user_id: int
) -> list[Objective]:
    return (
        db.query(Objective)
        .filter(
            Objective.tenant_id == tenant_id,
            Objective.project_id == project_id,
            Objective.user_id == user_id,
        )
        .order_by(Objective.sort_order, Objective.id)
        .all()
    )


def checklist_for_user(
    db: Session, *, tenant_id: int, project_id: int, user_id: int
) -> list[TaskItem]:
    return (
        db.query(TaskItem)
        .filter(
            TaskItem.tenant_id == tenant_id,
            TaskItem.project_id == project_id,
            TaskItem.owner_user_id == user_id,
        )
        .order_by(TaskItem.done.asc(), TaskItem.id.asc())
        .all()
    )


def issues_for_user(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user_id: int,
    open_only: bool = True,
) -> list[WorkIssue]:
    q = db.query(WorkIssue).filter(
        WorkIssue.tenant_id == tenant_id,
        WorkIssue.project_id == project_id,
        WorkIssue.owner_user_id == user_id,
    )
    if open_only:
        q = q.filter(WorkIssue.status == "open")
    return q.order_by(WorkIssue.id.asc()).all()


def can_view_user_status(db: Session, auth: AuthContext, target_user_id: int) -> bool:
    if auth.user_id == target_user_id:
        return True
    return is_workspace_owner(db, auth)


def format_user_status(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user: User,
) -> str:
    objs = objectives_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    checks = checklist_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    issues = issues_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    odone = sum(1 for o in objs if o.done)
    cdone = sum(1 for c in checks if c.done)
    lines = [
        f"STATUS for {user.name} <{user.email}>",
        f"Objectives: {odone}/{len(objs)} done",
    ]
    for o in objs:
        mark = "x" if o.done else " "
        lines.append(f"  [{mark}] #{o.id} {o.title}")
    if not objs:
        lines.append("  (none)")
    lines.append(f"Checklist: {cdone}/{len(checks)} done")
    open_checks = [c for c in checks if not c.done]
    for c in open_checks[:12]:
        lines.append(f"  [ ] #{c.id} {c.title}")
    if not open_checks:
        lines.append("  (no open items)")
    lines.append(f"Open issues: {len(issues)}")
    for i in issues:
        lines.append(f"  ! #{i.id} {i.title}" + (f" — {i.detail}" if i.detail else ""))
    if not issues:
        lines.append("  (none)")
    remaining = [o.title for o in objs if not o.done] + [c.title for c in open_checks[:5]]
    if remaining:
        lines.append("Summary: remaining → " + "; ".join(remaining[:8]))
    elif issues:
        lines.append("Summary: objectives/checklist clear; open issues need attention.")
    else:
        lines.append("Summary: caught up — no remaining owned work or open issues.")
    return "\n".join(lines)


def format_team_report(db: Session, *, tenant_id: int, project_id: int) -> str:
    members = (
        db.query(User, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .filter(WorkspaceMember.tenant_id == tenant_id)
        .order_by(User.email.asc())
        .all()
    )
    lines = ["TEAM REPORT"]
    for user, wm in members:
        objs = objectives_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
        checks = checklist_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
        issues = issues_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
        odone = sum(1 for o in objs if o.done)
        copen = sum(1 for c in checks if not c.done)
        lines.append(
            f"- {user.name} ({wm.role}): objectives {odone}/{len(objs)}, "
            f"checklist open {copen}, issues {len(issues)}"
        )
    return "\n".join(lines)
