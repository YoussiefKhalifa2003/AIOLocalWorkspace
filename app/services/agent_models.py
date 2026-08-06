from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentModelPref, utcnow
from app.services.model_tiers import resolve_model
from app.services.opencode_provider import AGENT_TYPES, is_opencode_model, normalize_model_id
from app.services.openrouter_provider import DEFAULT_OPENROUTER_PREFS, is_openrouter_model

DEFAULT_PREFS: dict[str, str] = dict(DEFAULT_OPENROUTER_PREFS)


def get_prefs_map(db: Session, tenant_id: int) -> dict[str, str]:
    settings = get_settings()
    if settings.openrouter_api_key and "openrouter.ai" in settings.openrouter_base_url:
        out = dict(DEFAULT_OPENROUTER_PREFS)
    elif settings.opencode_api_key:
        from app.services.opencode_provider import OPENCODE_FREE_MODELS

        # legacy opencode defaults
        out = {
            "coding": "deepseek-v4-flash-free",
            "code_review": "big-pickle",
            "research": "ling-3.0-flash-free",
            "writing": "mimo-v2.5-free",
            "checklist": "north-mini-code-free",
            "status": "ling-3.0-flash-free",
        }
        _ = OPENCODE_FREE_MODELS
    else:
        out = {a: "gemini-env" for a in DEFAULT_OPENROUTER_PREFS}
    rows = db.query(AgentModelPref).filter(AgentModelPref.tenant_id == tenant_id).all()
    for r in rows:
        out[r.agent_type] = r.model_id
    return out


def set_pref(db: Session, *, tenant_id: int, agent_type: str, model_id: str) -> AgentModelPref:
    if agent_type not in AGENT_TYPES:
        raise ValueError(f"unknown agent_type {agent_type}")
    mid = model_id.strip()
    if mid.startswith("opencode/"):
        mid = normalize_model_id(mid) or mid
    row = (
        db.query(AgentModelPref)
        .filter(AgentModelPref.tenant_id == tenant_id, AgentModelPref.agent_type == agent_type)
        .one_or_none()
    )
    if row is None:
        row = AgentModelPref(tenant_id=tenant_id, agent_type=agent_type, model_id=mid)
        db.add(row)
    else:
        row.model_id = mid
        row.updated_at = utcnow()
    db.flush()
    return row


def _backend_for(mid: str) -> str:
    if mid == "gemini-env":
        return "gemini"
    if is_openrouter_model(mid):
        return "openrouter"
    if is_opencode_model(mid):
        return "opencode"
    # plain gemini model ids
    if mid.startswith("gemini"):
        return "gemini"
    return "openrouter" if "/" in mid else "gemini"


def resolve_agent_model(
    db: Session | None,
    *,
    tenant_id: int | None,
    agent_type: str,
    model_tier: str | None = None,
    payload_model: str | None = None,
) -> tuple[str, str]:
    """Return (model_id, backend) - backend is openrouter|gemini|opencode."""
    settings = get_settings()

    def from_id(mid: str) -> tuple[str, str]:
        if mid == "gemini-env":
            return resolve_model(model_tier, agent_type=agent_type), "gemini"
        return mid, _backend_for(mid)

    if payload_model:
        return from_id(payload_model.strip())

    if db is not None and tenant_id is not None:
        pref = (
            db.query(AgentModelPref)
            .filter(AgentModelPref.tenant_id == tenant_id, AgentModelPref.agent_type == agent_type)
            .one_or_none()
        )
        if pref and pref.model_id:
            return from_id(pref.model_id)

    force = (settings.agent_llm_backend or "auto").lower()
    if force == "gemini":
        return resolve_model(model_tier, agent_type=agent_type), "gemini"
    if force == "openrouter" or (
        force == "auto"
        and settings.openrouter_api_key
        and "openrouter.ai" in settings.openrouter_base_url
    ):
        return DEFAULT_OPENROUTER_PREFS.get(agent_type, "openrouter/free"), "openrouter"
    if force == "opencode" and settings.opencode_api_key:
        return "big-pickle", "opencode"

    return resolve_model(model_tier, agent_type=agent_type), "gemini"
