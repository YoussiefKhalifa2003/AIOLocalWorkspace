from __future__ import annotations

from app.config import get_settings


def resolve_model(tier: str | None, *, agent_type: str = "") -> str:
    settings = get_settings()
    strong = (settings.agent_model_strong or settings.agent_model).strip() or settings.agent_model
    fast = (settings.agent_model_fast or settings.agent_model).strip() or settings.agent_model
    t = (tier or "").lower()
    if t == "fast":
        return fast
    if t == "strong":
        return strong
    # defaults by agent
    if agent_type in ("code_review", "research", "writing"):
        return strong
    if agent_type == "coding":
        return fast
    if agent_type == "checklist":
        return settings.checklist_model or strong
    return settings.agent_model


def infer_tier(agent_type: str, text: str) -> str:
    t = (text or "").lower()
    if agent_type == "code_review":
        return "strong"
    if agent_type in ("research", "writing"):
        return "strong"
    if agent_type == "coding":
        if len(text or "") > 800 or "architecture" in t or "refactor" in t:
            return "strong"
        return "fast"
    return "strong"
