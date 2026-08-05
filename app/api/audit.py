from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth

router = APIRouter(tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    job_id: int | None
    request_id: int | None
    event_type: str
    message: str


@router.get("/projects/{project_id}/audit", response_model=list[AuditOut])
def list_audit(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.tenant_id == auth.tenant_id, AuditEvent.project_id == project_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))
