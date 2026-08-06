from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Artifact, Job, TaskItem, utcnow
from app.services.audit import write_audit
from app.services.coding_backend import get_coding_backend, record_metric
from app.services.handoff import enqueue_next_pipeline_job, parse_json
from app.services.llm import LLMClient, LLMError
from app.services.rooms import post_agent_output

CHAT_MARKDOWN = (
    "Format for chat readability: short sections, ## headings sparingly, "
    "bullet lists, **bold** for key points, and fenced ```language code``` blocks. "
    "No HTML. Avoid walls of unformatted text."
)


def _sys(text: str) -> str:
    return f"{text.rstrip()} {CHAT_MARKDOWN}"


def _save_artifact(db: Session, job: Job, title: str, content: str) -> Artifact:
    art = Artifact(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        agent_type=job.agent_type,
        title=title,
        content=content,
    )
    db.add(art)
    db.flush()
    return art


def _model_for(db: Session, job: Job, agent_type: str) -> tuple[str, str]:
    """Return (model_id, backend) for this job/agent."""
    from app.services.agent_models import resolve_agent_model

    payload = parse_json(job.payload_json, {})
    return resolve_agent_model(
        db,
        tenant_id=job.tenant_id,
        agent_type=agent_type,
        model_tier=payload.get("model_tier"),
        payload_model=payload.get("model") or payload.get("agent_model"),
    )


def _agent_chat(
    db: Session,
    job: Job,
    llm: LLMClient,
    *,
    agent_type: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    from app.config import get_settings
    from app.services.opencode_provider import chat_with_model

    model, backend = _model_for(db, job, agent_type)
    job.model_used = model
    settings = get_settings()
    force = (settings.agent_llm_backend or "auto").lower()
    tokens: int | None = None
    metric_backend = backend

    if force == "gemini":
        backend = "gemini"
        model = settings.agent_model
        job.model_used = model
    elif force == "openrouter":
        backend = "openrouter"
    elif force == "opencode":
        backend = "opencode"

    try:
        if backend == "opencode":
            try:
                result = chat_with_model(
                    model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
                )
                job.model_used = f"{result.backend}:{result.model}"
                tokens = result.tokens
                metric_backend = result.backend
                content = result.content
            except LLMError:
                if force == "opencode":
                    raise
                from app.services.model_tiers import resolve_model

                gem = resolve_model(parse_json(job.payload_json, {}).get("model_tier"), agent_type=agent_type)
                job.model_used = f"gemini-fallback:{gem}"
                lr = llm.chat_result(
                    model=gem,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="gemini",
                )
                tokens = lr.tokens
                metric_backend = lr.backend or "gemini"
                content = lr.content
        elif backend == "openrouter":
            try:
                lr = llm.chat_result(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="openrouter",
                )
                job.model_used = f"openrouter:{model}"
                tokens = lr.tokens
                metric_backend = "openrouter"
                content = lr.content
            except LLMError:
                if force == "openrouter":
                    raise
                from app.services.model_tiers import resolve_model

                gem = resolve_model(parse_json(job.payload_json, {}).get("model_tier"), agent_type=agent_type)
                job.model_used = f"gemini-fallback:{gem}"
                lr = llm.chat_result(
                    model=gem,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="gemini",
                )
                tokens = lr.tokens
                metric_backend = lr.backend or "gemini"
                content = lr.content
        else:
            use_model = model if model != "gemini-env" else settings.agent_model
            job.model_used = use_model
            lr = llm.chat_result(
                model=use_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                force_backend="gemini",
            )
            tokens = lr.tokens
            metric_backend = lr.backend or "gemini"
            content = lr.content

        record_metric(
            db,
            job=job,
            backend=metric_backend,
            model=job.model_used,
            success=True,
            tokens=tokens,
        )
        return content
    except Exception:
        record_metric(
            db,
            job=job,
            backend=metric_backend or backend,
            model=job.model_used,
            success=False,
        )
        raise


def _maybe_post_review_to_general(db: Session, job: Job, content: str) -> None:
    payload = parse_json(job.payload_json, {})
    if not payload.get("post_to_general") and not payload.get("github"):
        return
    from app.services.github_notify import post_general

    repo = payload.get("repo") or ""
    title = payload.get("pr_title") or "PR review"
    pr_url = payload.get("pr_url") or ""
    body = (
        f"**Code review** - {repo}\n"
        f"{title}\n"
        f"{pr_url}\n\n"
        f"{content}"
    )
    post_general(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        body=body,
        agent_slug="code_review",
    )


def run_ask(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="ask",
        messages=[
            {
                "role": "system",
                "content": _sys(
                    "You are a helpful workplace assistant. Answer clearly and directly. "
                    "Use short paragraphs or bullets when useful. "
                    "If ATTACHED FILES are present, treat them as the primary source when the user "
                    "refers to \"this\" / the file / the document. Do not invent unrelated sources."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Ask reply", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="ask",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"ask artifact {art.id}",
    )


def run_writing(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    handoff = parse_json(job.handoff_json, {}) or payload.get("handoff") or {}
    source_bits = [payload.get("text") or ""]
    for aid in handoff.get("artifact_ids") or []:
        from app.db.models import Artifact as Art

        art = (
            db.query(Art)
            .filter(
                Art.id == aid,
                Art.tenant_id == job.tenant_id,
                Art.project_id == job.project_id,
            )
            .one_or_none()
        )
        if art:
            source_bits.append(f"--- prior artifact {art.id} ---\n{art.content}")
    prompt = "\n\n".join(b for b in source_bits if b)
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="writing",
        messages=[
            {
                "role": "system",
                "content": _sys("You are a writing agent. Draft a clear short report or brief."),
            },
            {"role": "user", "content": prompt},
        ],
    )
    art = _save_artifact(db, job, "Draft", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="writing",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"writing artifact {art.id}",
    )


def run_coding(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    model, backend = _model_for(db, job, "coding")
    # Prefer OpenCode/LLM chat path for selected models; legacy shell only if coding_backend=opencode AND gemini
    settings = get_settings()
    if (settings.coding_backend or "llm").lower() == "opencode" and backend == "gemini":
        job.model_used = model
        coding = get_coding_backend()
        try:
            result = coding.run(prompt=text, model=model, llm=llm)
            record_metric(
                db,
                job=job,
                backend=result.backend,
                model=result.model,
                success=result.success,
                duration_ms=result.duration_ms,
            )
            content = result.content
        except LLMError:
            record_metric(db, job=job, backend=settings.coding_backend, model=model, success=False)
            raise
    else:
        content = _agent_chat(
            db,
            job,
            llm,
            agent_type="coding",
            messages=[
                {
                    "role": "system",
                    "content": _sys(
                        "You are a coding agent. Write correct, minimal source code for the request. "
                        "Prefer a single fenced code block. Brief notes only if needed. "
                        "Do not write marketing prose or essays."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
    art = _save_artifact(db, job, "Code", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="coding",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"coding artifact {art.id}",
    )


def run_code_review(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("diff") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="code_review",
        messages=[
            {
                "role": "system",
                "content": _sys(
                    "You are a code review agent. List bugs, risks, and suggestions. "
                    "Be specific. If no diff is present, say what is missing."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Code review", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="code_review",
        body=content,
        job_id=job.id,
    )
    _maybe_post_review_to_general(db, job, content)
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"code_review artifact {art.id}",
    )


def run_checklist(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    handoff = parse_json(job.handoff_json, {}) or payload.get("handoff") or {}
    text = payload.get("text") or ""
    prior = []
    for aid in handoff.get("artifact_ids") or []:
        from app.db.models import Artifact as Art

        art = (
            db.query(Art)
            .filter(
                Art.id == aid,
                Art.tenant_id == job.tenant_id,
                Art.project_id == job.project_id,
            )
            .one_or_none()
        )
        if art:
            prior.append(art.content)
    try:
        raw = _agent_chat(
            db,
            job,
            llm,
            agent_type="checklist",
            messages=[
                {
                    "role": "system",
                    "content": (
                        'Return ONLY JSON {"tasks":["..."]} - short actionable follow-ups.'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Request:\n{text}\n\nContext:\n" + "\n".join(prior[-2:]),
                },
            ],
            temperature=0.1,
            max_tokens=500,
        )
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("no tasks")
    except (LLMError, ValueError, json.JSONDecodeError):
        tasks = [
            "Review agent output",
            "Confirm next steps with owner",
            "Update project status",
        ]

    created = []
    from app.db.models import WorkRequest

    req = db.query(WorkRequest).filter(WorkRequest.id == job.request_id).one_or_none()
    owner_id = req.user_id if req else None
    for title in tasks[:12]:
        item = TaskItem(
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            owner_user_id=owner_id,
            title=str(title)[:255],
            done=False,
        )
        db.add(item)
        created.append(str(title))
    db.flush()
    body = "Checklist updated:\n" + "\n".join(f"- [ ] {t}" for t in created)
    art = _save_artifact(db, job, "Checklist", body)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="checklist",
        body=body,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"checklist artifact {art.id}; {len(created)} tasks",
    )


def run_status(db: Session, job: Job, llm: LLMClient) -> None:
    from app.services.status_evidence import status_system_prompt

    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="status",
        messages=[
            {
                "role": "system",
                "content": _sys(status_system_prompt()),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Status briefing", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="status",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"status artifact {art.id}",
    )


AGENTS = {
    "ask": run_ask,
    "research": run_ask,  # legacy jobs / prefs
    "writing": run_writing,
    "coding": run_coding,
    "code_review": run_code_review,
    "checklist": run_checklist,
    "status": run_status,
}


def execute_job(db: Session, job: Job, llm: LLMClient | None = None) -> None:
    client = llm or LLMClient()
    job.status = "running"
    job.started_at = utcnow()
    db.flush()
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="job_started",
        message=f"started {job.agent_type}",
    )
    try:
        handler = AGENTS.get(job.agent_type)
        if handler is None:
            raise LLMError(f"unknown agent_type {job.agent_type}")
        handler(db, job, client)
        job.status = "done"
        job.finished_at = utcnow()
        job.error = None
        enqueue_next_pipeline_job(db, job)
    except Exception as exc:  # noqa: BLE001 - record any agent failure
        job.status = "failed"
        job.finished_at = utcnow()
        job.error = str(exc)
        write_audit(
            db,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            event_type="job_failed",
            message=str(exc),
        )
    db.flush()
