"""Private-room /skills - humans never @ agents."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Chat, ChatMessage
from app.services.chat_visibility import visible_messages_filter

# Public skills (shown in `/` picker). Keep this list short.
SKILLS: dict[str, dict] = {
    "ask": {
        "agent": "ask",
        "blurb": "just ask anything",
        "label": "/ask",
    },
    "deepresearch": {
        "agent": "deepresearch",
        "blurb": "deep dive with tables & structure",
        "label": "/deepresearch",
    },
    "code": {
        "agent": "coding",
        "blurb": "build or patch",
        "label": "/code",
    },
    "write": {
        "agent": "writing",
        "blurb": "draft clear prose",
        "label": "/write",
    },
    "review": {
        "agent": "code_review",
        "blurb": "check the diff",
        "label": "/review",
    },
    "checklist": {
        "agent": "checklist",
        "blurb": "break into ticks",
        "label": "/checklist",
    },
    "status": {
        "agent": "status",
        "blurb": "AI catch-up on a member",
        "label": "/status",
    },
}

# Old names still work; they route to /ask
_SKILL_ALIASES: dict[str, str] = {
    "web": "ask",
    "research": "ask",
    "deep-research": "deepresearch",
    "deep_research": "deepresearch",
}


@dataclass
class ParsedSkill:
    skill: str | None
    agent: str | None
    rest: str
    hint: str | None = None


def parse_skill(text: str) -> ParsedSkill:
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return ParsedSkill(skill=None, agent=None, rest=raw)
    body = raw[1:].lstrip()
    if not body:
        names = ", ".join(f"/{k}" for k in SKILLS)
        return ParsedSkill(
            skill=None,
            agent=None,
            rest="",
            hint=f"Skills: {names}. Example: /ask what is AIO?",
        )
    parts = body.split(None, 1)
    key = parts[0].lower().strip("/")
    rest = parts[1] if len(parts) > 1 else ""
    key = _SKILL_ALIASES.get(key, key)
    meta = SKILLS.get(key)
    if meta is None:
        return ParsedSkill(
            skill=None,
            agent=None,
            rest=raw,
            hint=f"Unknown skill `/{key}`. Try /ask, /deepresearch, /code, /write, /review, /checklist, /status.",
        )
    return ParsedSkill(skill=key, agent=meta["agent"], rest=rest)


def recent_private_context(
    db: Session, *, chat: Chat, user_id: int, limit: int = 15
) -> str:
    rows = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_id == chat.id,
            ChatMessage.tenant_id == chat.tenant_id,
            visible_messages_filter(user_id),
        )
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))
    lines = []
    for m in rows:
        who = m.agent_slug or ("you" if m.sender_user_id == user_id else "user")
        lines.append(f"{who}: {(m.body or '')[:400]}")
    return "\n".join(lines)
