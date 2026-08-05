from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Job
from app.db.session import get_db
from app.services.audit import write_audit
from app.services.auth import AuthContext, get_auth
from app.services.handoff import parse_json
from app.services.isolation import IsolationError, get_job

router = APIRouter(tags=["reviews"])


@router.post("/projects/{project_id}/reviews/{job_id}/approve")
def approve_review(
    project_id: int,
    job_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        job = get_job(db, auth.tenant_id, project_id, job_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if job.agent_type != "code_review":
        raise HTTPException(status_code=400, detail="job is not a code_review")
    if job.status != "done":
        raise HTTPException(status_code=400, detail=f"job status is {job.status}, need done")

    # If checklist not already queued via pipeline, enqueue it
    existing = (
        db.query(Job)
        .filter(
            Job.request_id == job.request_id,
            Job.agent_type == "checklist",
            Job.tenant_id == auth.tenant_id,
        )
        .one_or_none()
    )
    if existing is None:
        from app.db.models import Artifact

        arts = db.query(Artifact).filter_by(job_id=job.id).all()
        handoff = {
            "project_id": project_id,
            "request_id": job.request_id,
            "from_agent": "code_review",
            "artifact_ids": [a.id for a in arts],
            "notes": "human approved review",
        }
        payload = parse_json(job.payload_json, {})
        payload["handoff"] = handoff
        checklist = Job(
            tenant_id=auth.tenant_id,
            project_id=project_id,
            request_id=job.request_id,
            agent_type="checklist",
            status="queued",
            payload_json=json.dumps(payload),
            handoff_json=json.dumps(handoff),
            parent_job_id=job.id,
            pipeline_index=job.pipeline_index + 1,
        )
        db.add(checklist)
        db.flush()
        write_audit(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            request_id=job.request_id,
            job_id=checklist.id,
            event_type="review_approved",
            message=f"approved job {job.id}; queued checklist {checklist.id}",
        )
        db.commit()
        return {"status": "approved", "checklist_job_id": checklist.id}

    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="review_approved",
        message=f"approved job {job.id}; checklist already exists {existing.id}",
    )
    db.commit()
    return {"status": "approved", "checklist_job_id": existing.id}
