from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Objective, TaskItem, User, WorkIssue, utcnow
from app.services.auth import AuthContext
from app.services.chat_access import is_workspace_owner
from app.services.file_claims import claims_for_objective

BOARD_COLUMNS = ("todo", "doing", "blocked", "agent_backlog", "in_review", "done")
VALID_STATUSES = set(BOARD_COLUMNS)


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
    if is_workspace_owner(db, auth):
        return True
    return owner_id(obj) == auth.user_id


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


def build_board(db: Session, *, tenant_id: int, project_id: int) -> dict:
    rows = (
        db.query(Objective)
        .filter(Objective.tenant_id == tenant_id, Objective.project_id == project_id)
        .order_by(Objective.sort_order, Objective.id)
        .all()
    )
    users = {
        u.id: u
        for u in db.query(User).filter(User.tenant_id == tenant_id).all()
    }
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
                "claimed_paths": claims_for_objective(db, obj.id),
            }
        )
    return {
        "project_id": project_id,
        "columns": [{"id": c, "cards": columns[c]} for c in BOARD_COLUMNS],
    }


def board_text_summary(db: Session, *, tenant_id: int, project_id: int) -> str:
    board = build_board(db, tenant_id=tenant_id, project_id=project_id)
    lines = ["BOARD"]
    for col in board["columns"]:
        lines.append(f"{col['id']}: {len(col['cards'])}")
        for card in col["cards"][:8]:
            badge = f" !{card['open_issue_count']}" if card["open_issue_count"] else ""
            lines.append(f"  #{card['id']} {card['title']} ({card['owner_email']}){badge}")
    return "\n".join(lines)
