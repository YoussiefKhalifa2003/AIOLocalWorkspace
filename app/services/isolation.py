from sqlalchemy.orm import Session

from app.db.models import Artifact, Job, Project, Room, RoomMessage, TaskItem, WorkRequest


class IsolationError(PermissionError):
    pass


def get_project_for_tenant(db: Session, tenant_id: int, project_id: int) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.tenant_id == tenant_id)
        .one_or_none()
    )
    if project is None:
        raise IsolationError(f"project {project_id} not found for tenant {tenant_id}")
    return project


def jobs_for_project(db: Session, tenant_id: int, project_id: int) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.tenant_id == tenant_id, Job.project_id == project_id)
        .order_by(Job.id.asc())
        .all()
    )


def artifacts_for_project(db: Session, tenant_id: int, project_id: int) -> list[Artifact]:
    return (
        db.query(Artifact)
        .filter(Artifact.tenant_id == tenant_id, Artifact.project_id == project_id)
        .order_by(Artifact.id.asc())
        .all()
    )


def get_artifact(db: Session, tenant_id: int, project_id: int, artifact_id: int) -> Artifact:
    artifact = (
        db.query(Artifact)
        .filter(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
            Artifact.project_id == project_id,
        )
        .one_or_none()
    )
    if artifact is None:
        raise IsolationError("artifact not found in tenant/project scope")
    return artifact


def get_job(db: Session, tenant_id: int, project_id: int | None, job_id: int) -> Job:
    q = db.query(Job).filter(Job.id == job_id, Job.tenant_id == tenant_id)
    if project_id is not None:
        q = q.filter(Job.project_id == project_id)
    job = q.one_or_none()
    if job is None:
        raise IsolationError("job not found in tenant/project scope")
    return job


def get_request(db: Session, tenant_id: int, project_id: int, request_id: int) -> WorkRequest:
    req = (
        db.query(WorkRequest)
        .filter(
            WorkRequest.id == request_id,
            WorkRequest.tenant_id == tenant_id,
            WorkRequest.project_id == project_id,
        )
        .one_or_none()
    )
    if req is None:
        raise IsolationError("request not found in tenant/project scope")
    return req


def tasks_for_project(db: Session, tenant_id: int, project_id: int) -> list[TaskItem]:
    return (
        db.query(TaskItem)
        .filter(TaskItem.tenant_id == tenant_id, TaskItem.project_id == project_id)
        .order_by(TaskItem.id.asc())
        .all()
    )


def rooms_for_project(db: Session, tenant_id: int, project_id: int) -> list[Room]:
    return (
        db.query(Room)
        .filter(Room.tenant_id == tenant_id, Room.project_id == project_id)
        .order_by(Room.slug.asc())
        .all()
    )


def room_messages(
    db: Session, tenant_id: int, project_id: int, room_slug: str, limit: int = 50
) -> list[RoomMessage]:
    room = (
        db.query(Room)
        .filter(
            Room.tenant_id == tenant_id,
            Room.project_id == project_id,
            Room.slug == room_slug,
        )
        .one_or_none()
    )
    if room is None:
        raise IsolationError(f"room '{room_slug}' not found for project")
    return (
        db.query(RoomMessage)
        .filter(
            RoomMessage.tenant_id == tenant_id,
            RoomMessage.project_id == project_id,
            RoomMessage.room_id == room.id,
        )
        .order_by(RoomMessage.id.asc())
        .limit(limit)
        .all()
    )
