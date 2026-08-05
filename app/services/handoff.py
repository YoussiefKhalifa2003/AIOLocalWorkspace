from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Artifact, Job
from app.services.audit import write_audit


def parse_json(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default if default is not None else {}


def enqueue_next_pipeline_job(db: Session, finished_job: Job) -> Job | None:
    """If the request has a pipeline, enqueue the next agent after success."""
    from app.db.models import WorkRequest

    if finished_job.status != "done":
        return None

    req = db.query(WorkRequest).filter(WorkRequest.id == finished_job.request_id).one()
    pipeline = parse_json(req.pipeline_json, [])
    if not isinstance(pipeline, list) or not pipeline:
        return None

    next_index = finished_job.pipeline_index + 1
    if next_index >= len(pipeline):
        req.status = "completed"
        return None

    next_agent = pipeline[next_index]
    artifacts = db.query(Artifact).filter_by(job_id=finished_job.id).all()
    artifact_ids = [a.id for a in artifacts]
    handoff = {
        "project_id": finished_job.project_id,
        "request_id": finished_job.request_id,
        "from_agent": finished_job.agent_type,
        "artifact_ids": artifact_ids,
        "notes": f"handoff from {finished_job.agent_type}",
    }
    payload = parse_json(finished_job.payload_json, {})
    payload["handoff"] = handoff
    payload["source_text"] = payload.get("source_text") or payload.get("text")
    from app.services.model_tiers import infer_tier

    payload["model_tier"] = infer_tier(next_agent, str(payload.get("text") or ""))

    job = Job(
        tenant_id=finished_job.tenant_id,
        project_id=finished_job.project_id,
        request_id=finished_job.request_id,
        agent_type=next_agent,
        status="queued",
        payload_json=json.dumps(payload),
        handoff_json=json.dumps(handoff),
        parent_job_id=finished_job.id,
        pipeline_index=next_index,
    )
    db.add(job)
    db.flush()
    write_audit(
        db,
        tenant_id=finished_job.tenant_id,
        project_id=finished_job.project_id,
        request_id=finished_job.request_id,
        job_id=job.id,
        event_type="handoff_enqueued",
        message=f"{finished_job.agent_type} -> {next_agent} (job {job.id})",
    )
    return job
