"""Background chat LLM jobs so one user's /deepresearch doesn't block others' sends."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Artifact, Chat, ChatMessage, Job, Objective, utcnow
from app.worker import drain_queue

logger = logging.getLogger(__name__)

_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def schedule_chat_agent_followup(
    *,
    request_id: int,
    job_ids: list[int],
    chat_id: int,
    tenant_id: int,
    user_id: int,
    agents: list[str],
    plan_reason: str,
    request_text: str,
    speak: bool = False,
    whisper: bool = False,
    agent_slug: str = "lead",
) -> None:
    """Run drain_queue + post the chat reply on a daemon thread."""
    with _inflight_lock:
        if request_id in _inflight:
            return
        _inflight.add(request_id)

    meta = {
        "request_id": int(request_id),
        "job_ids": [int(j) for j in job_ids],
        "chat_id": int(chat_id),
        "tenant_id": int(tenant_id),
        "user_id": int(user_id),
        "agents": list(agents),
        "plan_reason": plan_reason or "",
        "request_text": request_text or "",
        "speak": bool(speak),
        "whisper": bool(whisper),
        "agent_slug": agent_slug or "lead",
    }

    def _run() -> None:
        try:
            _chat_agent_followup_worker(meta)
        finally:
            with _inflight_lock:
                _inflight.discard(int(meta["request_id"]))

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"aio-chat-agent-{request_id}",
    ).start()


def _wait_for_committed_jobs(job_ids: list[int], timeout: float = 8.0) -> bool:
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


def _chat_agent_followup_worker(meta: dict[str, Any]) -> None:
    job_ids: list[int] = list(meta.get("job_ids") or [])
    request_id = int(meta["request_id"])
    if not _wait_for_committed_jobs(job_ids):
        logger.warning(
            "chat agent request=%s jobs %s not visible after wait; continuing",
            request_id,
            job_ids,
        )

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        drain_queue(max_jobs=30)
        _finish_chat_agent(db, meta)
        db.commit()
    except Exception:
        logger.exception("chat agent followup failed for request %s", request_id)
        db.rollback()
        try:
            _post_chat_agent_failure(db, meta)
            db.commit()
        except Exception:
            logger.exception(
                "chat agent failure post also failed for request %s", request_id
            )
            db.rollback()
    finally:
        db.close()


def _finish_chat_agent(db: Session, meta: dict[str, Any]) -> None:
    from app.services.orchestrator import _CONFIRM_AGENTS, _with_confirm_footer

    request_id = int(meta["request_id"])
    agents: list[str] = list(meta.get("agents") or [])
    plan_reason = str(meta.get("plan_reason") or "")
    request_text = str(meta.get("request_text") or "")
    chat_id = int(meta["chat_id"])
    tenant_id = int(meta["tenant_id"])
    user_id = int(meta["user_id"])

    jobs = db.query(Job).filter(Job.request_id == request_id).order_by(Job.id).all()
    arts = (
        db.query(Artifact)
        .filter(Artifact.job_id.in_([j.id for j in jobs] or [-1]))
        .order_by(Artifact.id)
        .all()
    )
    if not arts:
        err = next((j.error for j in jobs if j.error), "no output")
        body = f"Lead→{agents}: failed ({err})"
        agent_slug = agents[0] if agents else "lead"
    else:
        chunks = [f"[Lead routed → {', '.join(agents)} | {plan_reason}]"]
        for a in arts:
            chunks.append(f"--- {a.agent_type} ---\n{a.content}")
        body = "\n\n".join(chunks)
        confirm_agents = [a for a in agents if a in _CONFIRM_AGENTS]
        cands: list[Objective] = []
        if confirm_agents:
            from app.services.orchestrator import _candidate_objectives
            from app.services.board import set_objective_status

            project_id = jobs[0].project_id if jobs else 1
            cands = _candidate_objectives(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                request_text=request_text,
                request_id=request_id,
            )
            if cands:
                for obj in cands:
                    if obj.request_id is None:
                        obj.request_id = request_id
                    if (obj.status or "") == "todo":
                        set_objective_status(obj, "doing")
                db.flush()
        body, _confirm_ids = _with_confirm_footer(body, cands)
        agent_slug = agents[-1] if agents else str(meta.get("agent_slug") or "lead")

    _post_agent_chat_message(
        db,
        tenant_id=tenant_id,
        chat_id=chat_id,
        body=body,
        agent_slug=agent_slug,
        speak=bool(meta.get("speak")),
        whisper=bool(meta.get("whisper")),
        whisper_user_id=user_id,
    )


def _post_chat_agent_failure(db: Session, meta: dict[str, Any]) -> None:
    _post_agent_chat_message(
        db,
        tenant_id=int(meta["tenant_id"]),
        chat_id=int(meta["chat_id"]),
        body="Agent run failed - check server logs and try again.",
        agent_slug=str(meta.get("agent_slug") or "lead"),
        speak=False,
        whisper=bool(meta.get("whisper")),
        whisper_user_id=int(meta["user_id"]),
    )


def _post_agent_chat_message(
    db: Session,
    *,
    tenant_id: int,
    chat_id: int,
    body: str,
    agent_slug: str,
    speak: bool,
    whisper: bool,
    whisper_user_id: int,
) -> ChatMessage:
    from app.db.models import ChatAttachment
    from app.services.chart_render import pop_charts_marker
    from app.services.chat_visibility import mark_whisper
    from app.services.tts import TTSError, synthesize_speech

    chat = db.query(Chat).filter(Chat.id == chat_id).one_or_none()
    if chat is None:
        raise RuntimeError(f"chat {chat_id} missing for agent reply")

    audio_url = None
    text, chart_ids = pop_charts_marker(body or "")
    if speak and text:
        try:
            path = synthesize_speech(text[:800])
            audio_url = f"/media/tts/{path.name}"
        except TTSError:
            audio_url = None

    reply = ChatMessage(
        tenant_id=tenant_id,
        chat_id=chat_id,
        sender_user_id=None,
        agent_slug=agent_slug or "lead",
        body=text,
        audio_url=audio_url,
        visibility="public",
        created_at=utcnow(),
    )
    if whisper:
        mark_whisper(reply, whisper_user_id)
    db.add(reply)
    db.flush()

    if chart_ids:
        rows = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.id.in_(chart_ids),
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.chat_id == chat_id,
                ChatAttachment.message_id.is_(None),
            )
            .all()
        )
        for row in rows:
            row.message_id = reply.id
        db.flush()
    return reply
