from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.isolation import IsolationError, room_messages, rooms_for_project
from app.services.rooms import ensure_project_rooms

router = APIRouter(tags=["rooms"])


@router.get("/projects/{project_id}/rooms")
def list_rooms(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    ensure_project_rooms(db, auth.tenant_id, project_id)
    db.commit()
    rooms = rooms_for_project(db, auth.tenant_id, project_id)
    return [{"id": r.id, "slug": r.slug, "name": r.name} for r in rooms]


@router.get("/projects/{project_id}/rooms/{slug}")
def read_room(
    project_id: int,
    slug: str,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    try:
        msgs = room_messages(db, auth.tenant_id, project_id, slug, limit=limit)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        {
            "id": m.id,
            "agent_type": m.agent_type,
            "job_id": m.job_id,
            "body": m.body,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in msgs
    ]
