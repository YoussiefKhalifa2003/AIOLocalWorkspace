from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Job
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.isolation import IsolationError, get_request
from app.services.work_requests import create_work_request

router = APIRouter(tags=["requests"])


class AskIn(BaseModel):
    text: str = Field(min_length=1)


class AskOut(BaseModel):
    request_id: int
    agents: list[str]
    job_ids: list[int]
    reason: str
    used_llm: bool


class RequestOut(BaseModel):
    id: int
    project_id: int
    text: str
    status: str
    pipeline: list[str]
    jobs: list[dict]


@router.post("/projects/{project_id}/requests", response_model=AskOut)
def create_request(
    project_id: int,
    body: AskIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        from app.services.isolation import get_project_for_tenant

        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    req, job_ids, plan = create_work_request(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        user_id=auth.user_id,
        text=body.text,
    )
    db.commit()
    return AskOut(
        request_id=req.id,
        agents=plan.agents,
        job_ids=job_ids,
        reason=plan.reason,
        used_llm=plan.used_llm,
    )


@router.get("/projects/{project_id}/requests/{request_id}", response_model=RequestOut)
def show_request(
    project_id: int,
    request_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        req = get_request(db, auth.tenant_id, project_id, request_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    jobs = (
        db.query(Job)
        .filter(
            Job.request_id == req.id,
            Job.tenant_id == auth.tenant_id,
            Job.project_id == project_id,
        )
        .order_by(Job.pipeline_index.asc(), Job.id.asc())
        .all()
    )
    pipeline = json.loads(req.pipeline_json or "[]")
    return RequestOut(
        id=req.id,
        project_id=req.project_id,
        text=req.text,
        status=req.status,
        pipeline=pipeline,
        jobs=[
            {
                "id": j.id,
                "agent_type": j.agent_type,
                "status": j.status,
                "pipeline_index": j.pipeline_index,
                "parent_job_id": j.parent_job_id,
                "model_used": j.model_used,
                "error": j.error,
            }
            for j in jobs
        ],
    )
