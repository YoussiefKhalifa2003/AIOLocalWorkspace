from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Artifact, Objective, Project, WorkIssue
from app.services.auth import AuthContext
from app.services.board import owner_id, set_objective_status
from app.services.file_claims import auto_claim_from_objective, extract_paths, find_collisions
from app.services.github_notify import post_general
from app.services.github_pr import create_pr_from_artifact
from app.services.work_requests import create_work_request
from app.worker import drain_queue


def enqueue_agent_backlog(db: Session, auth: AuthContext, obj: Objective) -> None:
    """Phase E: coding job + optional GitHub PR (or manual fallback message)."""
    paths = extract_paths(obj.title)
    collisions = find_collisions(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        user_id=owner_id(obj),
        paths=paths,
    )
    if collisions:
        issue = WorkIssue(
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            owner_user_id=owner_id(obj),
            title=f"File claim conflict for objective #{obj.id}",
            detail=", ".join(c.path_pattern for c in collisions),
            status="open",
        )
        db.add(issue)
        set_objective_status(obj, "blocked")
        return

    req, job_ids, _ = create_work_request(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        user_id=owner_id(obj),
        text=f"Implement objective #{obj.id}: {obj.title}",
    )
    obj.request_id = req.id
    db.flush()
    db.commit()
    drain_queue(max_jobs=30)

    arts = (
        db.query(Artifact)
        .filter(Artifact.job_id.in_(job_ids or [-1]))
        .order_by(Artifact.id)
        .all()
    )
    content = arts[0].content if arts else "(no coding output)"
    project = db.query(Project).filter(Project.id == obj.project_id).one()
    pr = create_pr_from_artifact(
        project=project,
        objective_id=obj.id,
        title=obj.title,
        body=(
            f"AIO generated for objective #{obj.id}\n\n#obj-{obj.id}\n\n"
            f"```\n{content[:50000]}\n```"
        ),
    )
    obj = db.query(Objective).filter(Objective.id == obj.id).one()
    if pr.get("ok"):
        obj.github_pr_url = pr.get("pr_url")
        obj.github_pr_number = pr.get("pr_number")
        obj.github_branch = pr.get("branch")
        set_objective_status(obj, "in_review")
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=f"Agent PR for objective #{obj.id}: {obj.github_pr_url}",
            agent_slug="coding",
        )
    else:
        set_objective_status(obj, "doing")
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=(
                f"Agent finished objective #{obj.id} (manual PR):\n\n"
                f"{pr.get('message', content)[:4000]}"
            ),
            agent_slug="coding",
        )
    auto_claim_from_objective(db, obj)
