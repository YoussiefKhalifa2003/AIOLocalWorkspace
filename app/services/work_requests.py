from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import Job, WorkRequest
from app.router.classify import RoutePlan, classify_request
from app.services.audit import write_audit
from app.services.model_tiers import infer_tier
from app.services.rooms import ensure_project_rooms


def create_work_request(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user_id: int,
    text: str,
    plan: RoutePlan | None = None,
    extra_payload: dict | None = None,
) -> tuple[WorkRequest, list[int], RoutePlan]:
    ensure_project_rooms(db, tenant_id, project_id)
    route = plan or classify_request(text)
    req = WorkRequest(
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        text=text,
        status="routed",
        pipeline_json=json.dumps(route.agents),
    )
    db.add(req)
    db.flush()

    job_ids: list[int] = []
    if route.agents:
        first = route.agents[0]
        tier = infer_tier(first, text)
        payload = {"text": text, "model_tier": tier}
        if extra_payload:
            payload.update(extra_payload)
        job = Job(
            tenant_id=tenant_id,
            project_id=project_id,
            request_id=req.id,
            agent_type=first,
            status="queued",
            payload_json=json.dumps(payload),
            pipeline_index=0,
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)

    write_audit(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=req.id,
        job_id=job_ids[0] if job_ids else None,
        event_type="request_routed",
        message=f"agents={route.agents} reason={route.reason}",
    )
    return req, job_ids, route
