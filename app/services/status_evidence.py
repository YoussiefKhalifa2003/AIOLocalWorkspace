"""Workspace status evidence for LLM /status skill."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Chat,
    ChatMessage,
    FileClaim,
    Job,
    Objective,
    TaskItem,
    User,
    WorkRequest,
)
from app.services.board import checklist_stats_for_objective
from app.services.status import (
    format_team_report,
    format_user_status,
    issues_for_user,
)


def objectives_for_member(
    db: Session, *, tenant_id: int, project_id: int, user_id: int
) -> list[Objective]:
    return (
        db.query(Objective)
        .filter(
            Objective.tenant_id == tenant_id,
            Objective.project_id == project_id,
            (Objective.user_id == user_id) | (Objective.assignee_user_id == user_id),
        )
        .order_by(Objective.sort_order, Objective.id)
        .all()
    )


def _channel_messages_by_user(
    db: Session, *, tenant_id: int, user_id: int, limit: int = 12
) -> list[str]:
    rows = (
        db.query(ChatMessage, Chat)
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .filter(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.sender_user_id == user_id,
            Chat.kind == "channel",
        )
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    lines = []
    for msg, chat in reversed(rows):
        body = (msg.body or "").strip().replace("\n", " ")
        if not body:
            continue
        lines.append(f"#{chat.name}: {body[:240]}")
    return lines


def _recent_jobs_for_user(
    db: Session, *, tenant_id: int, project_id: int, user_id: int, limit: int = 8
) -> list[str]:
    reqs = (
        db.query(WorkRequest)
        .filter(
            WorkRequest.tenant_id == tenant_id,
            WorkRequest.project_id == project_id,
            WorkRequest.user_id == user_id,
        )
        .order_by(WorkRequest.id.desc())
        .limit(6)
        .all()
    )
    if not reqs:
        return []
    req_ids = [r.id for r in reqs]
    jobs = (
        db.query(Job)
        .filter(Job.request_id.in_(req_ids))
        .order_by(Job.id.desc())
        .limit(limit)
        .all()
    )
    lines = []
    for j in jobs:
        art = (
            db.query(Artifact)
            .filter(Artifact.job_id == j.id)
            .order_by(Artifact.id.desc())
            .first()
        )
        snippet = ""
        if art and art.content:
            snippet = (art.content or "").strip().replace("\n", " ")[:160]
        lines.append(
            f"job #{j.id} {j.agent_type} [{j.status}]"
            + (f" — {snippet}" if snippet else "")
        )
    return lines


def build_user_evidence(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user: User,
) -> str:
    """Structured evidence pack (no private-room messages)."""
    base = format_user_status(db, tenant_id=tenant_id, project_id=project_id, user=user)
    objs = objectives_for_member(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    lines = [base, "", "EVIDENCE DETAIL"]
    for obj in objs:
        closed, total = checklist_stats_for_objective(db, objective_id=obj.id)
        st = obj.status or ("done" if obj.done else "todo")
        who = "assignee" if obj.assignee_user_id == user.id else "creator"
        lines.append(f"Objective #{obj.id} [{st}] ({who}): {obj.title}")
        if obj.description:
            lines.append(f"  description: {obj.description[:400]}")
        lines.append(f"  subtasks: {closed}/{total} done")
        for t in (
            db.query(TaskItem)
            .filter(TaskItem.objective_id == obj.id)
            .order_by(TaskItem.id.asc())
            .all()
        ):
            mark = "x" if t.done else " "
            lines.append(f"    [{mark}] {t.title}")
        if obj.github_branch:
            lines.append(f"  branch: {obj.github_branch}")
        if obj.github_pr_url:
            lines.append(f"  pr: {obj.github_pr_url}")
        claims = (
            db.query(FileClaim)
            .filter(FileClaim.objective_id == obj.id, FileClaim.active.is_(True))
            .all()
        )
        for c in claims:
            lines.append(f"  claim: {c.path_pattern}")

    resolved = issues_for_user(
        db, tenant_id=tenant_id, project_id=project_id, user_id=user.id, open_only=False
    )
    resolved_done = [i for i in resolved if i.status == "resolved"][:6]
    if resolved_done:
        lines.append("Recently resolved issues:")
        for i in resolved_done:
            lines.append(f"  #{i.id} {i.title}")

    jobs = _recent_jobs_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    if jobs:
        lines.append("Recent agent jobs:")
        lines.extend(f"  {j}" for j in jobs)

    channel = _channel_messages_by_user(db, tenant_id=tenant_id, user_id=user.id)
    if channel:
        lines.append("Recent team-channel messages (not private rooms):")
        lines.extend(f"  {c}" for c in channel)
    else:
        lines.append("Recent team-channel messages: (none — quiet in channels)")

    return "\n".join(lines)


def build_team_evidence(db: Session, *, tenant_id: int, project_id: int) -> str:
    report = format_team_report(db, tenant_id=tenant_id, project_id=project_id)
    return (
        f"{report}\n\n"
        "Write a short owner briefing: who is moving, who looks stuck or quiet, "
        "and what to check next. Cite board evidence; do not invent work."
    )


def status_system_prompt() -> str:
    return (
        "You are a workplace status analyst for AIO. "
        "Using ONLY the evidence pack, write a concise catch-up for a manager or the member. "
        "Cover: what they've done, what is in progress, blockers, and whether they appear quiet "
        "in chat while still having board activity. "
        "Use short sections and bullets. Do not invent facts not in the evidence. "
        "If evidence is thin, say so clearly."
    )
