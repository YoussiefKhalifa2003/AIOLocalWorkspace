"""Workspace status evidence for LLM /status skill."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Chat,
    ChatAttachment,
    ChatMessage,
    FileClaim,
    Job,
    Objective,
    TaskItem,
    User,
    WorkRequest,
)
from app.services.board import checklist_stats_for_objective
from app.services.status import (
    format_team_report,
    format_user_status,
    issues_for_user,
)


def objectives_for_member(
    db: Session, *, tenant_id: int, project_id: int, user_id: int
) -> list[Objective]:
    return (
        db.query(Objective)
        .filter(
            Objective.tenant_id == tenant_id,
            Objective.project_id == project_id,
            (Objective.user_id == user_id) | (Objective.assignee_user_id == user_id),
        )
        .order_by(Objective.sort_order, Objective.id)
        .all()
    )


def _clip(text: str, n: int = 280) -> str:
    body = (text or "").strip().replace("\n", " ")
    if len(body) <= n:
        return body
    return body[: n - 1] + "…"


def _extract_user_ask(work_text: str) -> str:
    """Prefer the User ask section from private-room skill prompts."""
    text = (work_text or "").strip()
    m = re.search(r"User ask:\s*(.*)\Z", text, flags=re.I | re.S)
    if m:
        return m.group(1).strip()
    if re.match(r"Private room context\b", text, flags=re.I):
        parts = re.split(r"\n\s*\n", text)
        return (parts[-1] if parts else text).strip()
    # Drop EVIDENCE PACK prompts from /status itself
    if re.search(r"EVIDENCE\s+PACK", text, flags=re.I):
        return ""
    return text


def _channel_messages_by_user(
    db: Session, *, tenant_id: int, user_id: int, limit: int = 16
) -> list[str]:
    rows = (
        db.query(ChatMessage, Chat)
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .filter(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.sender_user_id == user_id,
            Chat.kind == "channel",
        )
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    lines = []
    for msg, chat in reversed(rows):
        body = _clip(msg.body or "", 240)
        if not body:
            continue
        lines.append(f"#{chat.name}: {body}")
    return lines


def _private_room_activity(
    db: Session, *, tenant_id: int, project_id: int, user_id: int, limit: int = 28
) -> list[str]:
    """Recent private-room transcript (user + agent) for owner/self status."""
    priv = (
        db.query(Chat)
        .filter(
            Chat.tenant_id == tenant_id,
            Chat.project_id == project_id,
            Chat.kind == "private",
            Chat.owner_user_id == user_id,
        )
        .order_by(Chat.id.asc())
        .first()
    )
    if priv is None:
        return []
    rows = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == priv.id,
            ChatMessage.tenant_id == tenant_id,
        )
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    lines: list[str] = []
    for msg in reversed(rows):
        body = (msg.body or "").strip()
        if not body:
            continue
        if msg.sender_user_id == user_id:
            who = "member"
            clip_n = 320
        elif msg.agent_slug:
            who = f"agent:{msg.agent_slug}"
            clip_n = 420
        else:
            who = "system"
            clip_n = 240
        lines.append(f"[{who}] {_clip(body, clip_n)}")
    return lines


def _recent_attachments_for_user(
    db: Session, *, tenant_id: int, user_id: int, limit: int = 12
) -> list[str]:
    rows = (
        db.query(ChatAttachment, Chat)
        .join(Chat, Chat.id == ChatAttachment.chat_id)
        .filter(
            ChatAttachment.tenant_id == tenant_id,
            ChatAttachment.uploader_user_id == user_id,
            ChatAttachment.message_id.isnot(None),
        )
        .order_by(ChatAttachment.id.desc())
        .limit(limit)
        .all()
    )
    lines = []
    for att, chat in reversed(rows):
        where = (
            "private room"
            if (chat.kind or "") == "private"
            else f"#{chat.name}"
        )
        msg = f"msg #{att.message_id}" if att.message_id else "unlinked"
        lines.append(
            f"{att.filename} ({att.content_type}, {att.size_bytes}b) in {where} [{msg}]"
        )
    return lines


def _recent_jobs_for_user(
    db: Session, *, tenant_id: int, project_id: int, user_id: int, limit: int = 12
) -> list[str]:
    reqs = (
        db.query(WorkRequest)
        .filter(
            WorkRequest.tenant_id == tenant_id,
            WorkRequest.project_id == project_id,
            WorkRequest.user_id == user_id,
        )
        .order_by(WorkRequest.id.desc())
        .limit(10)
        .all()
    )
    if not reqs:
        return []
    req_by_id = {r.id: r for r in reqs}
    jobs = (
        db.query(Job)
        .filter(Job.request_id.in_(list(req_by_id)))
        .order_by(Job.id.desc())
        .limit(limit)
        .all()
    )
    lines = []
    for j in jobs:
        req = req_by_id.get(j.request_id)
        ask = _extract_user_ask(req.text if req else "") if req else ""
        art = (
            db.query(Artifact)
            .filter(Artifact.job_id == j.id)
            .order_by(Artifact.id.desc())
            .first()
        )
        snippet = _clip(art.content, 200) if art and art.content else ""
        bit = f"job #{j.id} {j.agent_type} [{j.status}]"
        if ask:
            bit += f" — ask: {_clip(ask, 200)}"
        if snippet:
            bit += f" — out: {snippet}"
        lines.append(bit)
    return lines


def build_user_evidence(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user: User,
) -> str:
    """Full activity pack for /status (board + channels + private room + agent jobs).

    Intended for self-view or workspace owner catch-up — includes private-room
    skill traffic so managers do not need to ping the member.
    """
    base = format_user_status(db, tenant_id=tenant_id, project_id=project_id, user=user)
    objs = objectives_for_member(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    lines = [base, "", "EVIDENCE DETAIL"]
    for obj in objs:
        closed, total = checklist_stats_for_objective(db, objective_id=obj.id)
        st = obj.status or ("done" if obj.done else "todo")
        who = "assignee" if obj.assignee_user_id == user.id else "creator"
        lines.append(f"Objective #{obj.id} [{st}] ({who}): {obj.title}")
        if obj.description:
            lines.append(f"  description: {obj.description[:400]}")
        lines.append(f"  subtasks: {closed}/{total} done")
        for t in (
            db.query(TaskItem)
            .filter(TaskItem.objective_id == obj.id)
            .order_by(TaskItem.id.asc())
            .all()
        ):
            mark = "x" if t.done else " "
            lines.append(f"    [{mark}] {t.title}")
        if obj.github_branch:
            lines.append(f"  branch: {obj.github_branch}")
        if obj.github_pr_url:
            lines.append(f"  pr: {obj.github_pr_url}")
        claims = (
            db.query(FileClaim)
            .filter(FileClaim.objective_id == obj.id, FileClaim.active.is_(True))
            .all()
        )
        for c in claims:
            lines.append(f"  claim: {c.path_pattern}")

    resolved = issues_for_user(
        db, tenant_id=tenant_id, project_id=project_id, user_id=user.id, open_only=False
    )
    resolved_done = [i for i in resolved if i.status == "resolved"][:6]
    if resolved_done:
        lines.append("Recently resolved issues:")
        for i in resolved_done:
            lines.append(f"  #{i.id} {i.title}")

    jobs = _recent_jobs_for_user(db, tenant_id=tenant_id, project_id=project_id, user_id=user.id)
    if jobs:
        lines.append("Recent agent jobs (private-room /skills + routed work):")
        lines.extend(f"  {j}" for j in jobs)

    private = _private_room_activity(
        db, tenant_id=tenant_id, project_id=project_id, user_id=user.id
    )
    if private:
        lines.append(
            "Private-room activity (member notes + /skills + agent replies — "
            "visible to owner status only):"
        )
        lines.extend(f"  {p}" for p in private)
    else:
        lines.append("Private-room activity: (none yet)")

    channel = _channel_messages_by_user(db, tenant_id=tenant_id, user_id=user.id)
    if channel:
        lines.append("Recent team-channel messages:")
        lines.extend(f"  {c}" for c in channel)
    else:
        lines.append("Recent team-channel messages: (none — quiet in channels)")

    uploads = _recent_attachments_for_user(db, tenant_id=tenant_id, user_id=user.id)
    if uploads:
        lines.append("Recent attachments (chat uploads):")
        lines.extend(f"  {u}" for u in uploads)
    else:
        lines.append("Recent attachments: (none)")

    return "\n".join(lines)


def build_team_evidence(db: Session, *, tenant_id: int, project_id: int) -> str:
    from app.db.models import WorkspaceMember

    report = format_team_report(db, tenant_id=tenant_id, project_id=project_id)
    members = (
        db.query(User)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .filter(WorkspaceMember.tenant_id == tenant_id)
        .order_by(User.id.asc())
        .all()
    )
    extra: list[str] = [report, "", "PER-MEMBER PRIVATE HIGHLIGHTS"]
    for u in members:
        priv = _private_room_activity(
            db, tenant_id=tenant_id, project_id=project_id, user_id=u.id, limit=8
        )
        jobs = _recent_jobs_for_user(
            db, tenant_id=tenant_id, project_id=project_id, user_id=u.id, limit=4
        )
        if not priv and not jobs:
            continue
        extra.append(f"— {u.name or u.email}:")
        for j in jobs[:3]:
            extra.append(f"  {j}")
        for p in priv[-4:]:
            extra.append(f"  {p}")
    extra.append("")
    extra.append(
        "Write a short owner briefing: who is moving, who looks stuck or quiet, "
        "and what to check next. Use private-room skill asks and board evidence; "
        "do not invent work."
    )
    return "\n".join(extra)


def status_system_prompt() -> str:
    return (
        "You are a workplace status analyst for AIO. "
        "Using ONLY the evidence pack, write a thorough catch-up for a manager or the member. "
        "Include EVERYTHING essential from the evidence: board objectives, blockers/issues, "
        "team-channel chat, private-room notes and /skill work (user asks + agent outcomes), "
        "and chat attachments the member uploaded. "
        "Call out when someone is stuck or asking for help in their private room even if "
        "channels are quiet. "
        "Cover: what they've done, what is in progress, where they asked for help, blockers, "
        "and chat vs board activity. "
        "Format for chat: short section titles on their own line, then plain bullet lines starting with '- '. "
        "Do not use Markdown tables, code fences, or decorative symbols. "
        "Bold sparingly with ** only for names or ids if needed. "
        "Do not invent facts not in the evidence. "
        "If evidence is thin, say so clearly."
    )
