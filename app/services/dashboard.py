"""Owner / project-manager dashboard aggregates."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AgentMetric, Job, Objective, User, WorkRequest, WorkspaceMember
from app.services.auth import AuthContext
from app.services.board import owner_id
from app.services.chat_access import is_workspace_owner


def build_owner_dashboard(db: Session, auth: AuthContext, *, project_id: int) -> dict:
    if not is_workspace_owner(db, auth):
        raise PermissionError("owner only")

    members = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.tenant_id == auth.tenant_id)
        .order_by(User.email.asc())
        .all()
    )

    metrics = (
        db.query(AgentMetric)
        .filter(AgentMetric.tenant_id == auth.tenant_id, AgentMetric.project_id == project_id)
        .all()
    )
    jobs = (
        db.query(Job)
        .filter(Job.tenant_id == auth.tenant_id, Job.project_id == project_id)
        .all()
    )
    # Backfill user_id from work request when missing on older rows
    req_user: dict[int, int] = {}
    req_ids = {j.request_id for j in jobs if j.request_id}
    if req_ids:
        for rid, uid in (
            db.query(WorkRequest.id, WorkRequest.user_id)
            .filter(WorkRequest.id.in_(req_ids))
            .all()
        ):
            req_user[rid] = uid
    job_user: dict[int, int] = {}
    for j in jobs:
        if j.request_id and j.request_id in req_user:
            job_user[j.id] = req_user[j.request_id]

    objectives = (
        db.query(Objective)
        .filter(Objective.tenant_id == auth.tenant_id, Objective.project_id == project_id)
        .order_by(Objective.id.desc())
        .all()
    )
    open_objs = [o for o in objectives if (o.status or "") != "done" and not o.done]

    user_by_id = {u.id: u for _, u in members}
    people_stats: dict[int, dict] = {}
    for _, u in members:
        people_stats[u.id] = {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": next(m.role for m, uu in members if uu.id == u.id),
            "jobs": 0,
            "tokens": 0,
            "models": set(),
        }

    for m in metrics:
        uid = m.user_id or (job_user.get(m.job_id) if m.job_id else None)
        if uid is None or uid not in people_stats:
            continue
        slot = people_stats[uid]
        slot["jobs"] += 1
        slot["tokens"] += int(m.tokens or 0)
        if m.model:
            slot["models"].add(m.model)

    # Also count jobs without metrics under the requester
    metric_job_ids = {m.job_id for m in metrics if m.job_id}
    for j in jobs:
        if j.id in metric_job_ids:
            continue
        uid = job_user.get(j.id)
        if uid in people_stats:
            people_stats[uid]["jobs"] += 1
            if j.model_used:
                people_stats[uid]["models"].add(j.model_used)

    people = []
    for slot in people_stats.values():
        models = sorted(slot["models"])
        people.append(
            {
                "user_id": slot["user_id"],
                "email": slot["email"],
                "name": slot["name"],
                "role": slot["role"],
                "jobs": slot["jobs"],
                "tokens": slot["tokens"],
                "models": models,
                "model_count": len(models),
            }
        )
    people.sort(key=lambda r: (-r["tokens"], -r["jobs"], r["email"]))

    by_model: dict[str, dict] = {}
    for m in metrics:
        key = m.model or "(none)"
        slot = by_model.setdefault(
            key,
            {
                "model": key,
                "backend": m.backend or "",
                "runs": 0,
                "tokens": 0,
                "success": 0,
                "fail": 0,
            },
        )
        slot["runs"] += 1
        slot["tokens"] += int(m.tokens or 0)
        if m.success:
            slot["success"] += 1
        else:
            slot["fail"] += 1
        if m.backend and not slot["backend"]:
            slot["backend"] = m.backend
    models = sorted(by_model.values(), key=lambda r: (-r["tokens"], -r["runs"], r["model"]))

    tokens_total = sum(int(m.tokens or 0) for m in metrics)
    email_by_id = {u.id: u.email for u in user_by_id.values()}

    open_tasks = [
        {
            "id": o.id,
            "title": o.title,
            "status": o.status or ("done" if o.done else "todo"),
            "assignee_user_id": owner_id(o),
            "assignee_email": email_by_id.get(owner_id(o) or -1),
        }
        for o in open_objs[:40]
    ]

    return {
        "project_id": project_id,
        "summary": {
            "members": len(members),
            "open_tasks": len(open_objs),
            "jobs_total": len(jobs),
            "jobs_done": sum(1 for j in jobs if j.status == "done"),
            "jobs_failed": sum(1 for j in jobs if j.status == "failed"),
            "tokens_total": tokens_total,
            "model_count": len(models),
        },
        "people": people,
        "models": models,
        "open_tasks": open_tasks,
    }
