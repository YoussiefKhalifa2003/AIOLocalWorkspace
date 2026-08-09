from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Objective, Project, TaskItem, User, WorkIssue, utcnow
from app.services.auth import AuthContext
from app.services.chat_access import is_workspace_owner
from app.services.file_claims import claims_for_objective

BOARD_COLUMNS = ("todo", "doing", "blocked", "agent_backlog", "in_review", "done")
VALID_STATUSES = set(BOARD_COLUMNS)


def resolve_repo_slug(project: Project | None) -> str | None:
    """`owner/repo` for the project, falling back to the global setting."""
    from app.config import get_settings

    slug = ""
    if project is not None:
        slug = (project.github_repo or "").strip()
    if not slug:
        slug = (get_settings().github_repo or "").strip()
    return slug or None


def repo_url_for(project: Project | None) -> str | None:
    slug = resolve_repo_slug(project)
    return f"https://github.com/{slug}" if slug else None


def sync_objective_done_flags(obj: Objective) -> None:
    if obj.status == "done":
        obj.done = True
        if obj.completed_at is None:
            obj.completed_at = utcnow()
    else:
        obj.done = False
        obj.completed_at = None


def set_objective_status(obj: Objective, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status}")
    obj.status = status
    sync_objective_done_flags(obj)


def owner_id(obj: Objective) -> int:
    return obj.assignee_user_id or obj.user_id


def can_edit_objective(db: Session, auth: AuthContext, obj: Objective) -> bool:
    """Owner edits any card; members edit cards they created or are assigned to."""
    if is_workspace_owner(db, auth):
        return True
    uid = auth.user_id
    return obj.user_id == uid or (obj.assignee_user_id or 0) == uid


def objective_visible_to(db: Session, auth: AuthContext, obj: Objective) -> bool:
    if is_workspace_owner(db, auth):
        return True
    uid = auth.user_id
    return obj.user_id == uid or (obj.assignee_user_id or 0) == uid


def checklist_stats_for_objective(db: Session, *, objective_id: int) -> tuple[int, int]:
    items = (
        db.query(TaskItem)
        .filter(TaskItem.objective_id == objective_id)
        .all()
    )
    total = len(items)
    closed = sum(1 for i in items if i.done)
    return closed, total


def checklist_stats(db: Session, *, tenant_id: int, project_id: int, user_id: int) -> tuple[int, int]:
    """Legacy: all checklist items owned by user (non-objective-scoped). Prefer checklist_stats_for_objective."""
    items = (
        db.query(TaskItem)
        .filter(
            TaskItem.tenant_id == tenant_id,
            TaskItem.project_id == project_id,
            TaskItem.owner_user_id == user_id,
            TaskItem.objective_id.is_(None),
        )
        .all()
    )
    total = len(items)
    closed = sum(1 for i in items if i.done)
    return closed, total


def subtasks_for_objective(db: Session, *, objective_id: int) -> list[TaskItem]:
    return (
        db.query(TaskItem)
        .filter(TaskItem.objective_id == objective_id)
        .order_by(TaskItem.done.asc(), TaskItem.id.asc())
        .all()
    )


def open_issue_count(db: Session, *, tenant_id: int, project_id: int, user_id: int) -> int:
    return (
        db.query(WorkIssue)
        .filter(
            WorkIssue.tenant_id == tenant_id,
            WorkIssue.project_id == project_id,
            WorkIssue.owner_user_id == user_id,
            WorkIssue.status == "open",
        )
        .count()
    )


def build_board(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    auth: AuthContext | None = None,
) -> dict:
    rows = (
        db.query(Objective)
        .filter(Objective.tenant_id == tenant_id, Objective.project_id == project_id)
        .order_by(Objective.sort_order, Objective.id)
        .all()
    )
    if auth is not None and not is_workspace_owner(db, auth):
        uid = auth.user_id
        rows = [
            o
            for o in rows
            if o.user_id == uid or (o.assignee_user_id or 0) == uid
        ]
    users = {
        u.id: u
        for u in db.query(User).filter(User.tenant_id == tenant_id).all()
    }
    project = db.query(Project).filter(Project.id == project_id).one_or_none()
    repo_slug = resolve_repo_slug(project)
    repo_url = repo_url_for(project)
    columns: dict[str, list] = {c: [] for c in BOARD_COLUMNS}
    for obj in rows:
        # migrate legacy rows
        st = obj.status or ("done" if obj.done else "todo")
        if st not in columns:
            st = "done" if obj.done else "todo"
            obj.status = st
        oid = owner_id(obj)
        u = users.get(oid)
        closed, total = checklist_stats_for_objective(db, objective_id=obj.id)
        issues = open_issue_count(db, tenant_id=tenant_id, project_id=project_id, user_id=oid)
        if total > 0:
            pct = int(round(100 * closed / total))
        else:
            pct = 100 if obj.done or st == "done" else 0
        columns[st].append(
            {
                "id": obj.id,
                "title": obj.title,
                "description": obj.description or "",
                "status": st,
                "done": obj.done,
                "user_id": obj.user_id,
                "assignee_user_id": oid,
                "owner_email": u.email if u else None,
                "owner_name": u.name if u else None,
                "open_issue_count": issues,
                "checklist_closed": closed,
                "checklist_total": total,
                "progress_percent": pct,
                "subtasks": [
                    {"id": t.id, "title": t.title, "done": bool(t.done)}
                    for t in subtasks_for_objective(db, objective_id=obj.id)
                ],
                "github_pr_url": obj.github_pr_url,
                "github_branch": obj.github_branch,
                "github_pr_number": obj.github_pr_number,
                "github_merged_at": (
                    obj.github_merged_at.isoformat() if obj.github_merged_at else None
                ),
                "repo_url": repo_url,
                "pr_url": obj.github_pr_url or None,
                "pr_number": obj.github_pr_number or None,
                "branch_url": (
                    f"{repo_url}/tree/{obj.github_branch}"
                    if repo_url and obj.github_branch
                    else None
                ),
                "can_merge": bool(
                    st == "in_review" and obj.github_pr_url and obj.github_pr_number
                ),
                "claimed_paths": claims_for_objective(db, obj.id),
            }
        )
    return {
        "project_id": project_id,
        "github_repo": repo_slug,
        "repo_url": repo_url,
        "columns": [{"id": c, "cards": columns[c]} for c in BOARD_COLUMNS],
    }


def board_text_summary(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    auth: AuthContext | None = None,
) -> str:
    board = build_board(db, tenant_id=tenant_id, project_id=project_id, auth=auth)
    lines = ["BOARD"]
    for col in board["columns"]:
        lines.append(f"{col['id']}: {len(col['cards'])}")
        for card in col["cards"][:8]:
            badge = f" !{card['open_issue_count']}" if card["open_issue_count"] else ""
            lines.append(f"  #{card['id']} {card['title']} ({card['owner_email']}){badge}")
    return "\n".join(lines)


def wipe_project_board(db: Session, *, tenant_id: int, project_id: int) -> dict:
    """Delete all objectives for a project and clean linked jobs/workspaces."""
    import shutil

    from app.db.models import (
        AgentMetric,
        Artifact,
        AuditEvent,
        FileClaim,
        Job,
        Objective,
        RoomMessage,
        TaskItem,
        WorkRequest,
    )
    from app.services.agent_workspace import workspace_path

    objs = (
        db.query(Objective)
        .filter(Objective.tenant_id == tenant_id, Objective.project_id == project_id)
        .all()
    )
    objective_ids = [o.id for o in objs]
    request_ids = [o.request_id for o in objs if o.request_id]
    removed_workspaces = 0

    if not objective_ids:
        return {"deleted_objectives": 0, "deleted_requests": 0, "removed_workspaces": 0}

    db.query(FileClaim).filter(FileClaim.objective_id.in_(objective_ids)).delete(
        synchronize_session=False
    )
    db.query(TaskItem).filter(TaskItem.objective_id.in_(objective_ids)).delete(
        synchronize_session=False
    )

    if request_ids:
        job_ids = [
            j.id
            for j in db.query(Job.id).filter(Job.request_id.in_(request_ids)).all()
        ]
        if job_ids:
            db.query(Artifact).filter(Artifact.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
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
            db.query(Job).filter(Job.parent_job_id.in_(job_ids)).update(
                {Job.parent_job_id: None}, synchronize_session=False
            )
            db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)
        db.query(TaskItem).filter(TaskItem.request_id.in_(request_ids)).update(
            {TaskItem.request_id: None}, synchronize_session=False
        )
        db.query(Objective).filter(Objective.request_id.in_(request_ids)).update(
            {Objective.request_id: None}, synchronize_session=False
        )
        db.query(AuditEvent).filter(AuditEvent.request_id.in_(request_ids)).delete(
            synchronize_session=False
        )
        db.query(WorkRequest).filter(WorkRequest.id.in_(request_ids)).delete(
            synchronize_session=False
        )

    db.query(Objective).filter(Objective.id.in_(objective_ids)).delete(
        synchronize_session=False
    )
    db.flush()

    for oid in objective_ids:
        path = workspace_path(oid)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            removed_workspaces += 1

    return {
        "deleted_objectives": len(objective_ids),
        "deleted_requests": len(set(request_ids)),
        "removed_workspaces": removed_workspaces,
    }
