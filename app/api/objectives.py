from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Job, Objective, TaskItem, WorkRequest, utcnow
from app.db.session import get_db
from app.services.audit import write_audit
from app.services.auth import AuthContext, get_auth
from app.services.board import (
    build_board,
    can_edit_objective,
    owner_id,
    set_objective_status,
)
from app.services.file_claims import auto_claim_from_objective, release_claims_for_objective
from app.services.isolation import IsolationError, get_project_for_tenant
from app.services.work_requests import create_work_request

router = APIRouter(tags=["objectives"])


class ObjectiveIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ObjectiveOut(BaseModel):
    id: int
    title: str
    description: str | None = None
    done: bool
    status: str = "todo"
    assignee_user_id: int | None = None
    request_id: int | None
    sort_order: int
    github_pr_url: str | None = None
    github_branch: str | None = None
    github_pr_number: int | None = None
    github_merged_at: str | None = None


class ObjectivePatch(BaseModel):
    status: str | None = None
    assignee_user_id: int | None = None
    title: str | None = None
    description: str | None = None
    coding_runner: str | None = None


class ObjectiveSetupIn(BaseModel):
    description: str | None = None
    subtasks: list[str] = Field(default_factory=list)
    dismiss: bool = False


def _obj_out(obj: Objective) -> ObjectiveOut:
    return ObjectiveOut(
        id=obj.id,
        title=obj.title,
        description=obj.description,
        done=obj.done,
        status=obj.status or ("done" if obj.done else "todo"),
        assignee_user_id=owner_id(obj),
        request_id=obj.request_id,
        sort_order=obj.sort_order,
        github_pr_url=obj.github_pr_url,
        github_branch=obj.github_branch,
        github_pr_number=obj.github_pr_number,
        github_merged_at=(
            obj.github_merged_at.isoformat() if obj.github_merged_at else None
        ),
    )


class ProgressOut(BaseModel):
    total: int
    done: int
    percent: int
    bar: str
    objectives: list[ObjectiveOut]


class RunOut(BaseModel):
    objective_id: int
    request_id: int
    agents: list[str]
    job_ids: list[int]
    reason: str
    used_llm: bool
    marked_done: bool


class ChecklistItemOut(BaseModel):
    id: int
    title: str
    done: bool
    job_id: int | None


def _bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "] 0%"
    pct = int(round(100 * done / total))
    filled = int(round(width * done / total))
    filled = min(width, max(0, filled))
    return f"[{'#' * filled}{'-' * (width - filled)}] {pct}%"


def _get_objective(db: Session, tenant_id: int, project_id: int, objective_id: int) -> Objective:
    obj = (
        db.query(Objective)
        .filter(
            Objective.id == objective_id,
            Objective.tenant_id == tenant_id,
            Objective.project_id == project_id,
        )
        .one_or_none()
    )
    if obj is None:
        raise IsolationError(f"objective {objective_id} not found")
    return obj


@router.get("/projects/{project_id}/objectives", response_model=ProgressOut)
def list_objectives(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    all_users: bool = False,
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from app.services.chat_access import is_workspace_owner

    q = db.query(Objective).filter(
        Objective.tenant_id == auth.tenant_id, Objective.project_id == project_id
    )
    if not (all_users and is_workspace_owner(db, auth)):
        q = q.filter(Objective.user_id == auth.user_id)
    rows = q.order_by(Objective.sort_order.asc(), Objective.id.asc()).all()
    total = len(rows)
    done = sum(1 for r in rows if r.done)
    return ProgressOut(
        total=total,
        done=done,
        percent=int(round(100 * done / total)) if total else 0,
        bar=_bar(done, total),
        objectives=[_obj_out(r) for r in rows],
    )


@router.post("/projects/{project_id}/objectives", response_model=ObjectiveOut)
def add_objective(
    project_id: int,
    body: ObjectiveIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    max_order = (
        db.query(Objective)
        .filter(Objective.tenant_id == auth.tenant_id, Objective.project_id == project_id)
        .count()
    )
    obj = Objective(
        tenant_id=auth.tenant_id,
        project_id=project_id,
        user_id=auth.user_id,
        assignee_user_id=auth.user_id,
        title=body.title.strip(),
        done=False,
        status="todo",
        sort_order=max_order + 1,
    )
    db.add(obj)
    db.flush()
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        event_type="objective_added",
        message=f"objective {obj.id}: {obj.title}",
    )
    db.commit()
    return _obj_out(obj)


@router.get("/projects/{project_id}/board")
def get_board(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Recover cards stuck in agent_backlog after a restart mid-run
    from app.services.agent_backlog import kick_stale_agent_backlog

    kick_stale_agent_backlog(db, project_id=project_id)
    return build_board(db, tenant_id=auth.tenant_id, project_id=project_id)


@router.patch("/projects/{project_id}/objectives/{objective_id}", response_model=ObjectiveOut)
def patch_objective(
    project_id: int,
    objective_id: int,
    body: ObjectivePatch,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not can_edit_objective(db, auth, obj):
        raise HTTPException(status_code=403, detail="can only edit your own objectives")

    if body.title is not None:
        obj.title = body.title.strip()[:255]

    if body.description is not None:
        obj.description = body.description.strip() or None

    from app.services.chat_access import is_workspace_owner

    if body.assignee_user_id is not None:
        if not is_workspace_owner(db, auth):
            raise HTTPException(status_code=403, detail="only owner can reassign")
        obj.assignee_user_id = body.assignee_user_id

    prev_status = obj.status or ("done" if obj.done else "todo")
    if body.status is not None:
        try:
            set_objective_status(obj, body.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if obj.status == "doing" and prev_status != "doing":
            auto_claim_from_objective(db, obj)
        if obj.status in ("done", "todo") and prev_status not in ("done", "todo"):
            release_claims_for_objective(db, obj.id)
        if obj.status == "done":
            release_claims_for_objective(db, obj.id)

        if obj.status == "agent_backlog" and prev_status != "agent_backlog":
            from app.services.agent_backlog import enqueue_agent_backlog
            from app.services.coding_backend import CODING_RUNNERS

            runner = (body.coding_runner or "").strip().lower()
            if runner and runner not in CODING_RUNNERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown coding runner {runner!r}; use one of {', '.join(CODING_RUNNERS)}",
                )
            enqueue_agent_backlog(db, auth, obj, coding_runner=runner)

    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        event_type="objective_patched",
        message=f"objective {obj.id} status={obj.status} assignee={obj.assignee_user_id}",
    )
    db.commit()
    return _obj_out(obj)


class MergeIn(BaseModel):
    confirm: bool = False
    merge_method: str | None = None
    delete_branch: bool = True


@router.post("/projects/{project_id}/objectives/{objective_id}/merge")
def merge_objective(
    project_id: int,
    objective_id: int,
    body: MergeIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Owner-only: merge the objective's PR, then move the card to done."""
    from app.services.chat_access import is_workspace_owner
    from app.services.github_notify import post_general
    from app.services.github_pr import delete_remote_branch, merge_pull_request

    try:
        project = get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not is_workspace_owner(db, auth):
        raise HTTPException(status_code=403, detail="only the workspace owner can merge")

    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail="confirmation required: merging into the default branch cannot be undone",
        )

    status = obj.status or ("done" if obj.done else "todo")
    if status != "in_review":
        raise HTTPException(
            status_code=400,
            detail=f"objective must be in_review to merge (currently {status})",
        )
    if not obj.github_pr_number:
        raise HTTPException(status_code=400, detail="objective has no linked pull request")

    result = merge_pull_request(
        project=project,
        pr_number=int(obj.github_pr_number),
        commit_title=f"[AIO #{obj.id}] {obj.title}"[:250],
        commit_message=f"Merged from AIO objective #{obj.id}.",
        merge_method=body.merge_method or "",
    )

    if not result.get("ok"):
        write_audit(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            event_type="objective_merge_failed",
            message=f"objective {obj.id} pr={obj.github_pr_number} reason={result.get('reason_code')}",
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=result.get("message") or "GitHub refused the merge",
        )

    base = result.get("base") or "main"
    obj.github_merged_at = utcnow()
    set_objective_status(obj, "done")
    release_claims_for_objective(db, obj.id)

    branch_note = ""
    if body.delete_branch and obj.github_branch:
        deleted = delete_remote_branch(project=project, branch=obj.github_branch)
        branch_note = (
            f" Branch `{obj.github_branch}` deleted."
            if deleted.get("ok")
            else f" Branch `{obj.github_branch}` left in place."
        )

    post_general(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        body=(
            f"Merged PR #{obj.github_pr_number} for objective #{obj.id} into `{base}`. "
            f"Card moved to done.{branch_note}\n{obj.github_pr_url or ''}"
        ),
        agent_slug="lead",
    )
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        event_type="objective_merged",
        message=(
            f"objective {obj.id} pr={obj.github_pr_number} base={base} "
            f"method={result.get('merge_method')} sha={result.get('sha')}"
        ),
    )
    db.commit()
    return {
        "ok": True,
        "objective": _obj_out(obj).model_dump(),
        "merged": True,
        "sha": result.get("sha"),
        "base": base,
        "merge_method": result.get("merge_method"),
        "message": result.get("message"),
    }


def _clear_setup_markers(db: Session, *, tenant_id: int, objective_id: int) -> int:
    """Remove [[setup:N]] from lead messages so the card does not remount after refresh."""
    import re

    from app.db.models import ChatMessage

    marker = f"[[setup:{objective_id}]]"
    rows = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.body.contains(marker),
        )
        .all()
    )
    n = 0
    for msg in rows:
        cleaned = re.sub(rf"\n?\[\[setup:{objective_id}\]\]\s*", "", msg.body or "").rstrip()
        if cleaned != msg.body:
            msg.body = cleaned
            n += 1
    if n:
        db.flush()
    return n


@router.put("/projects/{project_id}/objectives/{objective_id}/setup", response_model=ObjectiveOut)
def setup_objective(
    project_id: int,
    objective_id: int,
    body: ObjectiveSetupIn,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Optional post-!add setup: description + objective-scoped subtasks (or dismiss)."""
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not can_edit_objective(db, auth, obj):
        raise HTTPException(status_code=403, detail="can only edit your own objectives")

    if body.dismiss:
        _clear_setup_markers(db, tenant_id=auth.tenant_id, objective_id=obj.id)
        write_audit(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            event_type="objective_setup_dismissed",
            message=f"objective {obj.id} setup dismissed",
        )
        db.commit()
        db.refresh(obj)
        return _obj_out(obj)

    if body.description is not None:
        desc = body.description.strip()
        obj.description = desc or None

    db.query(TaskItem).filter(
        TaskItem.objective_id == obj.id,
        TaskItem.tenant_id == auth.tenant_id,
        TaskItem.project_id == project_id,
    ).delete(synchronize_session=False)

    owner = owner_id(obj)
    for raw in body.subtasks or []:
        title = (raw or "").strip()[:255]
        if not title:
            continue
        db.add(
            TaskItem(
                tenant_id=auth.tenant_id,
                project_id=project_id,
                objective_id=obj.id,
                owner_user_id=owner,
                title=title,
                done=False,
            )
        )

    _clear_setup_markers(db, tenant_id=auth.tenant_id, objective_id=obj.id)
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        event_type="objective_setup",
        message=f"objective {obj.id} setup subtasks={len(body.subtasks or [])}",
    )
    db.commit()
    db.refresh(obj)
    return _obj_out(obj)


def _enqueue_agent_backlog(db: Session, auth: AuthContext, obj: Objective) -> None:
    from app.services.agent_backlog import enqueue_agent_backlog

    enqueue_agent_backlog(db, auth, obj)


@router.post("/projects/{project_id}/objectives/{objective_id}/run", response_model=RunOut)
def run_objective(
    project_id: int,
    objective_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    mark_done: bool = True,
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if obj.done:
        raise HTTPException(status_code=400, detail="objective already done")

    req, job_ids, route = create_work_request(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        user_id=auth.user_id,
        text=obj.title,
    )
    obj.request_id = req.id
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        request_id=req.id,
        event_type="objective_run",
        message=f"objective {obj.id} -> request {req.id}",
    )
    db.commit()
    return RunOut(
        objective_id=obj.id,
        request_id=req.id,
        agents=route.agents,
        job_ids=job_ids,
        reason=route.reason,
        used_llm=route.used_llm,
        marked_done=False,
    )


@router.post("/projects/{project_id}/objectives/{objective_id}/complete", response_model=ObjectiveOut)
def complete_objective(
    project_id: int,
    objective_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Only complete if linked request finished successfully (or no failed jobs)
    if obj.request_id:
        jobs = (
            db.query(Job)
            .filter(
                Job.request_id == obj.request_id,
                Job.tenant_id == auth.tenant_id,
                Job.project_id == project_id,
            )
            .all()
        )
        if any(j.status == "failed" for j in jobs):
            raise HTTPException(status_code=400, detail="linked jobs failed; not marking done")
        if any(j.status in ("queued", "running") for j in jobs):
            raise HTTPException(status_code=400, detail="linked jobs still running")
        req = db.query(WorkRequest).filter(WorkRequest.id == obj.request_id).one_or_none()
        if req and req.status not in ("completed", "routed") and jobs:
            # allow completed; if all jobs done mark request completed
            if all(j.status == "done" for j in jobs):
                req.status = "completed"

    obj.done = True
    obj.completed_at = utcnow()
    obj.status = "done"
    release_claims_for_objective(db, obj.id)
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        request_id=obj.request_id,
        event_type="objective_done",
        message=f"objective {obj.id} completed",
    )
    db.commit()
    return _obj_out(obj)


@router.delete("/projects/{project_id}/objectives/{objective_id}")
def delete_objective(
    project_id: int,
    objective_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
        obj = _get_objective(db, auth.tenant_id, project_id, objective_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.delete(obj)
    db.commit()
    return {"status": "deleted", "id": objective_id}


@router.get("/projects/{project_id}/checklist", response_model=list[ChecklistItemOut])
def checklist(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    all_items: bool = False,
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    q = db.query(TaskItem).filter(
        TaskItem.tenant_id == auth.tenant_id,
        TaskItem.project_id == project_id,
    )
    if not all_items:
        # Only the newest batch (latest request_id that has tasks)
        latest_req = (
            db.query(TaskItem.request_id)
            .filter(
                TaskItem.tenant_id == auth.tenant_id,
                TaskItem.project_id == project_id,
                TaskItem.request_id.isnot(None),
            )
            .order_by(TaskItem.id.desc())
            .limit(1)
            .scalar()
        )
        if latest_req is not None:
            q = q.filter(TaskItem.request_id == latest_req)
        else:
            # fallback: newest 10
            items = q.order_by(TaskItem.id.desc()).limit(10).all()
            items = list(reversed(items))
            return [
                ChecklistItemOut(id=i.id, title=i.title, done=i.done, job_id=i.job_id)
                for i in items
            ]

    items = q.order_by(TaskItem.done.asc(), TaskItem.id.asc()).all()
    return [
        ChecklistItemOut(id=i.id, title=i.title, done=i.done, job_id=i.job_id) for i in items
    ]


@router.delete("/projects/{project_id}/checklist")
def checklist_clear(
    project_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    deleted = (
        db.query(TaskItem)
        .filter(TaskItem.tenant_id == auth.tenant_id, TaskItem.project_id == project_id)
        .delete()
    )
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        event_type="checklist_cleared",
        message=f"deleted {deleted} items",
    )
    db.commit()
    return {"status": "cleared", "deleted": deleted}


@router.get("/projects/{project_id}/requests/{request_id}/result")
def request_result(
    project_id: int,
    request_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Proof of work: artifacts produced for a request."""
    try:
        get_project_for_tenant(db, auth.tenant_id, project_id)
    except IsolationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from app.db.models import Artifact, Job

    jobs = (
        db.query(Job)
        .filter(
            Job.request_id == request_id,
            Job.tenant_id == auth.tenant_id,
            Job.project_id == project_id,
        )
        .order_by(Job.pipeline_index.asc(), Job.id.asc())
        .all()
    )
    if not jobs:
        raise HTTPException(status_code=404, detail="no jobs for request")
    arts = (
        db.query(Artifact)
        .filter(
            Artifact.tenant_id == auth.tenant_id,
            Artifact.project_id == project_id,
            Artifact.job_id.in_([j.id for j in jobs]),
        )
        .order_by(Artifact.id.asc())
        .all()
    )
    return {
        "request_id": request_id,
        "jobs": [
            {"id": j.id, "agent_type": j.agent_type, "status": j.status, "error": j.error}
            for j in jobs
        ],
        "artifacts": [
            {
                "id": a.id,
                "agent_type": a.agent_type,
                "title": a.title,
                "content": a.content,
            }
            for a in arts
        ],
    }


@router.post("/projects/{project_id}/checklist/{item_id}/done", response_model=ChecklistItemOut)
def checklist_done(
    project_id: int,
    item_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
    done: bool = True,
):
    item = (
        db.query(TaskItem)
        .filter(
            TaskItem.id == item_id,
            TaskItem.tenant_id == auth.tenant_id,
            TaskItem.project_id == project_id,
        )
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="checklist item not found")
    item.done = done
    write_audit(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        job_id=item.job_id,
        event_type="checklist_toggled",
        message=f"item {item.id} done={done}",
    )
    db.commit()
    return ChecklistItemOut(id=item.id, title=item.title, done=item.done, job_id=item.job_id)
