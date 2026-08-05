from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def write_audit(
    db: Session,
    *,
    tenant_id: int,
    event_type: str,
    message: str,
    project_id: int | None = None,
    job_id: int | None = None,
    request_id: int | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        project_id=project_id,
        job_id=job_id,
        request_id=request_id,
        event_type=event_type,
        message=message,
    )
    db.add(event)
    db.flush()
    return event
