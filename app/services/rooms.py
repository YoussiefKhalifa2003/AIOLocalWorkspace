from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Room, RoomMessage
from app.services.audit import write_audit

AGENT_ROOM = {
    "ask": "ask",
    "research": "ask",  # legacy
    "writing": "writing",
    "coding": "coding",
    "code_review": "review",
    "checklist": "tasks",
    "status": "general",
}

DEFAULT_ROOMS = [
    ("general", "General"),
    ("ask", "Ask"),
    ("research", "Ask"),  # legacy slug still creatable / readable
    ("writing", "Writing"),
    ("coding", "Coding"),
    ("review", "Code Review"),
    ("tasks", "Tasks"),
]


def ensure_project_rooms(db: Session, tenant_id: int, project_id: int) -> None:
    existing = {
        r.slug
        for r in db.query(Room).filter(Room.tenant_id == tenant_id, Room.project_id == project_id)
    }
    for slug, name in DEFAULT_ROOMS:
        if slug not in existing:
            db.add(Room(tenant_id=tenant_id, project_id=project_id, slug=slug, name=name))
    db.flush()


def post_to_room(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    room_slug: str,
    body: str,
    job_id: int | None = None,
    agent_type: str | None = None,
) -> RoomMessage | None:
    ensure_project_rooms(db, tenant_id, project_id)
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
        return None
    msg = RoomMessage(
        tenant_id=tenant_id,
        project_id=project_id,
        room_id=room.id,
        job_id=job_id,
        agent_type=agent_type,
        body=body,
    )
    db.add(msg)
    db.flush()
    return msg


def post_agent_output(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    agent_type: str,
    body: str,
    job_id: int | None = None,
) -> None:
    room_slug = AGENT_ROOM.get(agent_type, "general")
    post_to_room(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        room_slug=room_slug,
        body=body,
        job_id=job_id,
        agent_type=agent_type,
    )
    settings = get_settings()
    if settings.general_status_posts:
        short = body.strip().splitlines()[0][:160] if body.strip() else f"{agent_type} finished"
        post_to_room(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            room_slug="general",
            body=f"[{agent_type}] {short}",
            job_id=job_id,
            agent_type=agent_type,
        )
    write_audit(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        job_id=job_id,
        event_type="room_posted",
        message=f"posted to #{room_slug}",
    )
