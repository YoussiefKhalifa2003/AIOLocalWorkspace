from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.isolation import IsolationError, get_project_for_tenant
from fastapi import HTTPException

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    name: str
    github_repo: str | None


@router.get("", response_model=list[ProjectOut])
def list_projects(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    rows = db.query(Project).filter(Project.tenant_id == auth.tenant_id).order_by(Project.id).all()
    return rows


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        return get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
