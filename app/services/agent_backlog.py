from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.orm import Session

from app.db.models import Artifact, Job, Objective, Project
from app.services.auth import AuthContext
from app.services.board import owner_id, set_objective_status
from app.services.file_claims import auto_claim_from_objective, extract_paths, find_collisions
from app.services.github_notify import post_general
from app.services.github_pr import create_pr_from_artifact
from app.services.work_requests import create_work_request
from app.worker import drain_queue

logger = logging.getLogger(__name__)

_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def enqueue_agent_backlog(db: Session, auth: AuthContext, obj: Objective) -> list[int] | None:
    """Queue coding work for an agent_backlog card and return quickly.

    Heavy LLM + GitHub PR run in a background thread so the Board UI can stay
    responsive and poll for status changes (agent_backlog → in_review / doing).
    """
    paths = extract_paths(obj.title)
    collisions = find_collisions(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        user_id=owner_id(obj),
        paths=paths,
    )
    if collisions:
        from app.db.models import WorkIssue

        issue = WorkIssue(
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            owner_user_id=owner_id(obj),
            title=f"File claim conflict for objective #{obj.id}",
            detail=", ".join(c.path_pattern for c in collisions),
            status="open",
        )
        db.add(issue)
        set_objective_status(obj, "blocked")
        return None

    req, job_ids, _ = create_work_request(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        user_id=owner_id(obj),
        text=f"Implement objective #{obj.id}: {obj.title}",
    )
    obj.request_id = req.id
    db.flush()
    ids = list(job_ids or [])
    schedule_agent_backlog_followup(obj.id, ids)
    return ids


def schedule_agent_backlog_followup(objective_id: int, job_ids: list[int]) -> None:
    with _inflight_lock:
        if objective_id in _inflight:
            return
        _inflight.add(objective_id)

    def _run() -> None:
        try:
            _agent_backlog_followup_worker(objective_id, job_ids)
        finally:
            with _inflight_lock:
                _inflight.discard(objective_id)

    thread = threading.Thread(
        target=_run,
        daemon=True,
        name=f"aio-backlog-{objective_id}",
    )
    thread.start()


def kick_stale_agent_backlog(db: Session, *, project_id: int) -> int:
    """Re-schedule finish for agent_backlog cards whose follow-up died mid-flight.

    Safe to call from board polls — deduped by in-flight set.
    """
    objs = (
        db.query(Objective)
        .filter(
            Objective.project_id == project_id,
            Objective.status == "agent_backlog",
        )
        .all()
    )
    kicked = 0
    for obj in objs:
        job_ids: list[int] = []
        if obj.request_id:
            job_ids = [
                j.id
                for j in db.query(Job)
                .filter(Job.request_id == obj.request_id)
                .order_by(Job.id.asc())
                .all()
            ]
        if not job_ids:
            continue
        with _inflight_lock:
            if obj.id in _inflight:
                continue
        schedule_agent_backlog_followup(obj.id, job_ids)
        kicked += 1
    return kicked


def _wait_for_committed_jobs(job_ids: list[int], timeout: float = 8.0) -> bool:
    """HTTP handler commits after enqueue; wait until jobs are visible to other sessions."""
    if not job_ids:
        return True
    from app.db.session import SessionLocal

    deadline = time.time() + timeout
    while time.time() < deadline:
        db = SessionLocal()
        try:
            n = db.query(Job).filter(Job.id.in_(job_ids)).count()
            if n >= len(job_ids):
                return True
        finally:
            db.close()
        time.sleep(0.15)
    return False


def _agent_backlog_followup_worker(objective_id: int, job_ids: list[int]) -> None:
    if not _wait_for_committed_jobs(job_ids):
        logger.warning(
            "agent_backlog obj=%s jobs %s not visible after wait; continuing anyway",
            objective_id,
            job_ids,
        )
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        finish_agent_backlog(db, objective_id=objective_id, job_ids=job_ids)
        db.commit()
    except Exception:
        logger.exception("agent_backlog followup failed for objective %s", objective_id)
        db.rollback()
        try:
            _post_backlog_failure(db, objective_id=objective_id)
            db.commit()
        except Exception:
            logger.exception(
                "agent_backlog failure post also failed for objective %s", objective_id
            )
            db.rollback()
    finally:
        db.close()


def _post_backlog_failure(db: Session, *, objective_id: int) -> None:
    obj = db.query(Objective).filter(Objective.id == objective_id).one_or_none()
    if obj is None:
        return
    if (obj.status or "") == "agent_backlog":
        set_objective_status(obj, "doing")
    post_general(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        body=(
            f"Agent run failed for objective #{obj.id} "
            f"({obj.title}). Card moved to doing — check server logs / try again."
        ),
        agent_slug="coding",
    )


def finish_agent_backlog(
    db: Session, *, objective_id: int, job_ids: list[int]
) -> None:
    """Drain coding jobs and open (or fall back) a GitHub PR."""
    obj = db.query(Objective).filter(Objective.id == objective_id).one_or_none()
    if obj is None:
        return
    if (obj.status or "") != "agent_backlog":
        return

    drain_queue(max_jobs=30)

    # Fresh read after drain commits in other sessions
    db.expire_all()
    obj = db.query(Objective).filter(Objective.id == objective_id).one_or_none()
    if obj is None or (obj.status or "") != "agent_backlog":
        return

    ids = list(job_ids or [])
    if not ids and obj.request_id:
        ids = [
            j.id
            for j in db.query(Job).filter(Job.request_id == obj.request_id).all()
        ]

    arts = (
        db.query(Artifact)
        .filter(Artifact.job_id.in_(ids or [-1]))
        .order_by(Artifact.id)
        .all()
    )
    content = arts[0].content if arts else "(no coding output)"
    project = db.query(Project).filter(Project.id == obj.project_id).one()
    pr = create_pr_from_artifact(
        project=project,
        objective_id=obj.id,
        title=obj.title,
        body=(
            f"AIO generated for objective #{obj.id}\n\n#obj-{obj.id}\n\n"
            f"```\n{content[:50000]}\n```"
        ),
        content=content,
    )
    obj = db.query(Objective).filter(Objective.id == objective_id).one()
    if (obj.status or "") != "agent_backlog":
        return
    if pr.get("ok"):
        obj.github_pr_url = pr.get("pr_url")
        obj.github_pr_number = pr.get("pr_number")
        obj.github_branch = pr.get("branch")
        set_objective_status(obj, "in_review")
        files = pr.get("files") or []
        file_note = f"\nFiles: {', '.join(files)}" if files else ""
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=f"Agent PR for objective #{obj.id}: {obj.github_pr_url}{file_note}",
            agent_slug="coding",
        )
    else:
        set_objective_status(obj, "doing")
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=(
                f"Agent finished objective #{obj.id} (manual PR):\n\n"
                f"{pr.get('message', content)[:4000]}"
            ),
            agent_slug="coding",
        )
    auto_claim_from_objective(db, obj)
