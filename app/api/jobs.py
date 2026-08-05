from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Artifact, Job
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.isolation import IsolationError, artifacts_for_project, get_artifact, get_job, jobs_for_project

router = APIRouter(tags=["jobs"])


class JobOut(BaseModel):
    id: int
    project_id: int
    request_id: int
    agent_type: str
    status: str
    model_used: str | None
    error: str | None
    parent_job_id: int | None
    pipeline_index: int


class ArtifactOut(BaseModel):
    id: int
    job_id: int
    agent_type: str
    title: str
    content: str


@router.get("/projects/{project_id}/jobs/summary")
def jobs_summary(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        from app.services.isolation import get_project_for_tenant

        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    jobs = (
        db.query(Job)
        .filter(Job.tenant_id == auth.tenant_id, Job.project_id == project_id)
        .all()
    )
    by_model: dict[str, dict] = {}
    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1
        key = j.model_used or "(none)"
        slot = by_model.setdefault(key, {"model": key, "total": 0, "done": 0, "failed": 0})
        slot["total"] += 1
        if j.status == "done":
            slot["done"] += 1
        elif j.status == "failed":
            slot["failed"] += 1
    return {
        "project_id": project_id,
        "total": len(jobs),
        "by_status": by_status,
        "by_model": list(by_model.values()),
    }


@router.get("/projects/{project_id}/analytics")
def analytics(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Phase G: simple tables — success by model/backend, jobs per day."""
    from app.db.models import AgentMetric
    from app.services.chat_access import is_workspace_owner
    from app.services.isolation import get_project_for_tenant

    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not is_workspace_owner(db, auth):
        raise HTTPException(status_code=403, detail="owner only")

    metrics = (
        db.query(AgentMetric)
        .filter(AgentMetric.tenant_id == auth.tenant_id, AgentMetric.project_id == project_id)
        .all()
    )
    by_backend: dict[str, dict] = {}
    for m in metrics:
        key = f"{m.backend}|{m.model or '-'}"
        slot = by_backend.setdefault(
            key, {"backend": m.backend, "model": m.model, "total": 0, "success": 0, "fail": 0}
        )
        slot["total"] += 1
        if m.success:
            slot["success"] += 1
        else:
            slot["fail"] += 1

    jobs = (
        db.query(Job)
        .filter(Job.tenant_id == auth.tenant_id, Job.project_id == project_id)
        .all()
    )
    return {
        "project_id": project_id,
        "metrics_by_backend": list(by_backend.values()),
        "jobs_total": len(jobs),
        "jobs_done": sum(1 for j in jobs if j.status == "done"),
        "jobs_failed": sum(1 for j in jobs if j.status == "failed"),
    }


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    return jobs_for_project(db, auth.tenant_id, project_id)


@router.get("/projects/{project_id}/jobs/{job_id}", response_model=JobOut)
def show_job(
    project_id: int,
    job_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        return get_job(db, auth.tenant_id, project_id, job_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    return artifacts_for_project(db, auth.tenant_id, project_id)


@router.get("/projects/{project_id}/artifacts/{artifact_id}", response_model=ArtifactOut)
def show_artifact(
    project_id: int,
    artifact_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        return get_artifact(db, auth.tenant_id, project_id, artifact_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/tasks")
def list_tasks(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    from app.services.isolation import tasks_for_project

    items = tasks_for_project(db, auth.tenant_id, project_id)
    return [
        {"id": t.id, "title": t.title, "done": t.done, "job_id": t.job_id}
        for t in items
    ]
