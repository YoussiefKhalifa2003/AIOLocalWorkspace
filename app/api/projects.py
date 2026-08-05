from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.services.auth import AuthContext, get_auth
from app.services.chat_access import is_workspace_owner
from app.services.isolation import IsolationError, get_project_for_tenant
from app.services.rooms import ensure_project_rooms

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    name: str
    github_repo: str | None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    github_repo: str | None = None


@router.get("", response_model=list[ProjectOut])
def list_projects(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    rows = db.query(Project).filter(Project.tenant_id == auth.tenant_id).order_by(Project.id).all()
    return rows


@router.post("", response_model=ProjectOut)
def create_project(
    body: ProjectIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    if not is_workspace_owner(db, auth):
        raise HTTPException(status_code=403, detail="owner only")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    existing = (
        db.query(Project)
        .filter(Project.tenant_id == auth.tenant_id, Project.name == name)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"project '{name}' already exists")
    project = Project(
        tenant_id=auth.tenant_id,
        name=name,
        github_repo=(body.github_repo or "").strip() or None,
    )
    db.add(project)
    db.flush()
    ensure_project_rooms(db, auth.tenant_id, project.id)
    db.commit()
    db.refresh(project)
    return project


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
