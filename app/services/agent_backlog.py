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
from app.services.work_requests import create_work_request
from app.worker import drain_queue

logger = logging.getLogger(__name__)

_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def enqueue_agent_backlog(
    db: Session,
    auth: AuthContext,
    obj: Objective,
    *,
    coding_runner: str = "",
) -> list[int] | None:
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

    extra: dict[str, object] = {"objective_id": obj.id}
    if coding_runner:
        extra["coding_runner"] = coding_runner
    req, job_ids, _ = create_work_request(
        db,
        tenant_id=obj.tenant_id,
        project_id=obj.project_id,
        user_id=owner_id(obj),
        text=agent_task_prompt(obj),
        extra_payload=extra,
    )
    obj.request_id = req.id
    db.flush()
    ids = list(job_ids or [])
    schedule_agent_backlog_followup(obj.id, ids)
    return ids


def agent_task_prompt(obj: Objective) -> str:
    """Task brief for the coding agent, whether it edits files or emits a blob."""
    lines = [
        f"Implement objective #{obj.id}: {obj.title}",
    ]
    if (obj.description or "").strip():
        lines += ["", "Details:", obj.description.strip()]
    lines += [
        "",
        "You are working inside a checkout of the project repository.",
        "If you can edit files directly, make the smallest correct change in the "
        "existing code, following the conventions already in the repo, and do not "
        "commit or push - AIO handles git.",
        "If you can only emit text, reply with the code in fenced blocks.",
    ]
    return "\n".join(lines)


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

    Safe to call from board polls - deduped by in-flight set.
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
            f"({obj.title}). Card moved to doing - check server logs / try again."
        ),
        agent_slug="coding",
    )


def finish_agent_backlog(
    db: Session, *, objective_id: int, job_ids: list[int]
) -> None:
    """Prepare a workspace, run the coding agent in it, push, and open a PR."""
    import time

    from app.config import get_settings
    from app.services.agent_workspace import (
        changed_files,
        commit_all,
        prepare_workspace,
        push_branch,
        write_artifact_files,
    )
    from app.services.github_pr import (
        artifact_files_for_objective,
        branch_slug,
        create_pr_from_artifact,
        open_pr_for_branch,
        resolve_github_token,
    )

    obj = db.query(Objective).filter(Objective.id == objective_id).one_or_none()
    if obj is None:
        return
    if (obj.status or "") != "agent_backlog":
        return

    project = db.query(Project).filter(Project.id == obj.project_id).one()
    branch = f"aio/obj-{obj.id}-{branch_slug(obj.title)}-{int(time.time()) % 100000}"

    # The checkout has to exist before the agent runs, so a workspace-capable
    # runner (codex / claude_code) can edit real files inside it.
    ws = prepare_workspace(project, obj.id, branch)
    ws_path = ws.get("path") or "" if ws.get("ok") else ""
    if not ws.get("ok"):
        logger.info(
            "workspace prepare failed for obj=%s: %s; agent will emit text only",
            obj.id,
            ws.get("error"),
        )

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
    file_list: list[str] = []
    agent_edited = False
    pr: dict = {}

    def _api_fallback() -> dict:
        return create_pr_from_artifact(
            project=project,
            objective_id=obj.id,
            title=obj.title,
            body=(
                f"AIO generated for objective #{obj.id}\n\n#obj-{obj.id}\n\n"
                f"```\n{content[:50000]}\n```"
            ),
            content=content,
            branch_name=branch,
        )

    if ws_path:
        agent_files = changed_files(ws_path)
        if agent_files:
            # The coding agent edited the checkout itself; keep its work as-is.
            agent_edited = True
            file_list = agent_files
            written = {"ok": True}
        else:
            files = artifact_files_for_objective(obj.id, obj.title, content)
            file_list = list(files.keys())
            written = write_artifact_files(ws_path, files)

        if written.get("ok"):
            commit_all(
                ws_path,
                message=f"[AIO #{obj.id}] {obj.title}"[:200],
                branch=branch,
            )
            token = resolve_github_token(project)
            repo = (project.github_repo or get_settings().github_repo or "").strip()
            pushed = push_branch(ws_path, branch, token=token, repo=repo)
            if pushed.get("ok"):
                pr = open_pr_for_branch(
                    project=project,
                    objective_id=obj.id,
                    title=obj.title,
                    branch=branch,
                    files=file_list,
                    workspace_note=ws_path,
                )
            else:
                logger.warning(
                    "workspace push failed for obj=%s: %s; falling back to API",
                    obj.id,
                    pushed.get("error"),
                )
                pr = _api_fallback()
        else:
            logger.warning(
                "workspace write failed for obj=%s: %s; falling back to API",
                obj.id,
                written.get("error"),
            )
            pr = _api_fallback()
    else:
        pr = _api_fallback()

    obj = db.query(Objective).filter(Objective.id == objective_id).one()
    if (obj.status or "") != "agent_backlog":
        return
    if pr.get("ok"):
        obj.github_pr_url = pr.get("pr_url")
        obj.github_pr_number = pr.get("pr_number")
        obj.github_branch = pr.get("branch") or branch
        set_objective_status(obj, "in_review")
        files_out = pr.get("files") or file_list
        file_note = f"\nFiles: {', '.join(files_out[:20])}" if files_out else ""
        ws_note = f"\nWorkspace: {ws_path}" if ws_path else ""
        how = "agent edited the checkout" if agent_edited else "generated files"
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=(
                f"Agent PR for objective #{obj.id} ({how}): "
                f"{obj.github_pr_url}{file_note}{ws_note}"
            ),
            agent_slug="coding",
        )
    else:
        set_objective_status(obj, "doing")
        ws_note = f"\n\nLocal workspace: `{ws_path}`" if ws_path else ""
        post_general(
            db,
            tenant_id=obj.tenant_id,
            project_id=obj.project_id,
            body=(
                f"Agent finished objective #{obj.id} (manual PR):\n\n"
                f"{pr.get('message', content)[:4000]}{ws_note}"
            ),
            agent_slug="coding",
        )
    auto_claim_from_objective(db, obj)
