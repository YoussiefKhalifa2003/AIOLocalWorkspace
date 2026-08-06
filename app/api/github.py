from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Job, Objective, Project, User, WebhookEvent, WorkRequest
from app.db.session import get_db
from app.services.audit import write_audit
from app.services.github_notify import post_general
from app.services.model_tiers import infer_tier
from app.services.rooms import ensure_project_rooms

router = APIRouter(tags=["github"])

OBJ_RE = re.compile(r"(?:#obj-|objective\s+)(\d+)", re.I)


def _verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    import hashlib
    import hmac

    if not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, signature)


def _find_objective(db: Session, project: Project, text: str) -> Objective | None:
    m = OBJ_RE.search(text or "")
    if not m:
        return None
    oid = int(m.group(1))
    return (
        db.query(Objective)
        .filter(
            Objective.id == oid,
            Objective.tenant_id == project.tenant_id,
            Objective.project_id == project.id,
        )
        .one_or_none()
    )


def _queue_code_review(
    db: Session,
    *,
    project: Project,
    user: User,
    title: str,
    diff_text: str,
    pr_url: str | None,
    repo: str,
) -> tuple[WorkRequest, Job]:
    text = f"Review GitHub change for {repo}: {title}\nPR: {pr_url or '(sim)'}\n\n{diff_text}"
    ensure_project_rooms(db, project.tenant_id, project.id)
    tier = infer_tier("code_review", text)
    req = WorkRequest(
        tenant_id=project.tenant_id,
        project_id=project.id,
        user_id=user.id,
        text=text,
        status="routed",
        pipeline_json=json.dumps(["code_review", "checklist"]),
    )
    db.add(req)
    db.flush()
    job = Job(
        tenant_id=project.tenant_id,
        project_id=project.id,
        request_id=req.id,
        agent_type="code_review",
        status="queued",
        payload_json=json.dumps(
            {
                "text": text,
                "diff": diff_text,
                "github": True,
                "post_to_general": True,
                "pr_url": pr_url,
                "pr_title": title,
                "repo": repo,
                "model_tier": tier,
            }
        ),
        pipeline_index=0,
    )
    db.add(job)
    db.flush()
    return req, job


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    settings = get_settings()
    body = await request.body()
    if not _verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    delivery_id = x_github_delivery or __import__("hashlib").sha256(body).hexdigest()
    existing = db.query(WebhookEvent).filter_by(delivery_id=delivery_id).one_or_none()
    if existing:
        return {"status": "duplicate", "delivery_id": delivery_id}

    payload = json.loads(body.decode() or "{}")
    event = WebhookEvent(
        delivery_id=delivery_id,
        event_type=x_github_event or "unknown",
        payload_json=json.dumps(payload),
        processed=False,
    )
    db.add(event)
    db.flush()

    repo = (payload.get("repository") or {}).get("full_name")
    if not repo:
        event.processed = True
        db.commit()
        return {"status": "ignored", "reason": "no repository"}

    project = db.query(Project).filter(Project.github_repo == repo).one_or_none()
    if project is None:
        event.processed = True
        db.commit()
        return {"status": "ignored", "reason": f"no project mapped to {repo}"}

    user = db.query(User).filter(User.tenant_id == project.tenant_id).order_by(User.id).first()
    if user is None:
        raise HTTPException(status_code=500, detail="no user for tenant")

    action = payload.get("action") or ""
    event_name = x_github_event or "unknown"
    result: dict = {"status": "ok", "project_id": project.id}

    if event_name == "pull_request" and action in ("opened", "synchronize", "reopened", ""):
        # empty action: webhook-sim legacy payloads
        pr = payload.get("pull_request") or {}
        diff_text = pr.get("diff_text") or payload.get("diff_text") or ""
        title = pr.get("title") or "GitHub PR"
        pr_url = pr.get("html_url") or pr.get("url")
        pr_number = pr.get("number")
        head = (pr.get("head") or {}).get("ref") or pr.get("head_ref")
        blob = f"{title}\n{pr.get('body') or ''}"
        obj = _find_objective(db, project, blob)
        if obj is not None:
            if pr_url:
                obj.github_pr_url = pr_url
            if pr_number is not None:
                obj.github_pr_number = int(pr_number)
            if head:
                obj.github_branch = head
            notice = (
                f"PR opened: **{title}**\n"
                f"{pr_url or '(no url)'}\n"
                f"Linked objective #{obj.id}: {obj.title}"
            )
        else:
            notice = f"PR opened: **{title}**\n{pr_url or '(no url)'}"
        post_general(
            db,
            tenant_id=project.tenant_id,
            project_id=project.id,
            body=notice,
            agent_slug="lead",
        )
        if action in ("opened", "reopened", ""):
            req, job = _queue_code_review(
                db,
                project=project,
                user=user,
                title=title,
                diff_text=diff_text,
                pr_url=pr_url,
                repo=repo,
            )
            result.update(
                {"status": "queued", "request_id": req.id, "job_id": job.id, "objective_id": obj.id if obj else None}
            )
        else:
            result["status"] = "notified"

    elif event_name == "pull_request" and action == "closed":
        pr = payload.get("pull_request") or {}
        merged = bool(pr.get("merged"))
        title = pr.get("title") or "PR"
        pr_url = pr.get("html_url") or ""
        blob = f"{title}\n{pr.get('body') or ''}"
        obj = _find_objective(db, project, blob)
        if not obj and pr.get("number"):
            obj = (
                db.query(Objective)
                .filter(
                    Objective.project_id == project.id,
                    Objective.github_pr_number == int(pr["number"]),
                )
                .one_or_none()
            )
        if merged:
            # AIO-initiated merges already announced themselves; don't double-post.
            already_ours = obj is not None and obj.github_merged_at is not None
            if not already_ours:
                body = f"PR merged on GitHub: **{title}**\n{pr_url}"
                if obj is not None:
                    body += (
                        f"\nObjective #{obj.id} ({obj.title}) is still "
                        f"{obj.status or 'open'} - run Merge and done in AIO, "
                        "or mark it done manually."
                    )
                post_general(
                    db,
                    tenant_id=project.tenant_id,
                    project_id=project.id,
                    body=body,
                    agent_slug="lead",
                )
            result["status"] = "merge_notified"
            result["objective_id"] = obj.id if obj else None
            result["deduped"] = bool(already_ours)
        else:
            post_general(
                db,
                tenant_id=project.tenant_id,
                project_id=project.id,
                body=f"PR closed (not merged): **{title}**\n{pr_url}",
                agent_slug="lead",
            )
            result["status"] = "closed_notified"

    elif event_name == "push":
        ref = payload.get("ref") or ""
        branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ""
        obj = None
        if branch:
            obj = (
                db.query(Objective)
                .filter(
                    Objective.project_id == project.id,
                    Objective.github_branch == branch,
                    Objective.done.is_(False),
                )
                .order_by(Objective.id.desc())
                .first()
            )
        if obj is not None:
            post_general(
                db,
                tenant_id=project.tenant_id,
                project_id=project.id,
                body=f"Push on `{branch}` (objective #{obj.id}: {obj.title})",
                agent_slug="lead",
            )
            result["status"] = "push_notified"
        else:
            result["status"] = "ignored"
            result["reason"] = "no matching objective branch"
    else:
        write_audit(
            db,
            tenant_id=project.tenant_id,
            project_id=project.id,
            event_type="github_webhook_audit",
            message=f"delivery={delivery_id} event={event_name} action={action}",
        )
        result["status"] = "audited"

    write_audit(
        db,
        tenant_id=project.tenant_id,
        project_id=project.id,
        request_id=result.get("request_id"),
        job_id=result.get("job_id"),
        event_type="github_webhook",
        message=f"delivery={delivery_id} event={event_name} action={action} status={result.get('status')}",
    )
    event.processed = True
    db.commit()
    return result
