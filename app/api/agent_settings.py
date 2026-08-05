from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.services.agent_models import DEFAULT_PREFS, get_prefs_map, set_pref
from app.services.auth import AuthContext, get_auth
from app.services.opencode_provider import AGENT_TYPES, list_free_models as list_opencode_models
from app.services.openrouter_provider import list_openrouter_free_models

router = APIRouter(tags=["agent-settings"])


class PrefPatch(BaseModel):
    agent_type: str = Field(min_length=1, max_length=40)
    model_id: str = Field(min_length=1, max_length=120)


class PrefsBody(BaseModel):
    prefs: list[PrefPatch]


@router.get("/workspace/agent-models")
def get_agent_models(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    models: list[dict] = [
        {
            "id": "gemini-env",
            "label": f"Gemini (.env: {settings.agent_model})",
            "provider": "gemini",
            "free": True,
        },
    ]
    # Primary free set: OpenRouter
    if settings.openrouter_api_key and "openrouter.ai" in settings.openrouter_base_url:
        models.extend(list_openrouter_free_models())
    # Optional OpenCode Zen free (if key set)
    if settings.opencode_api_key:
        for m in list_opencode_models():
            models.append({**m, "label": f"[OpenCode] {m['label']}"})

    prefs = get_prefs_map(db, auth.tenant_id)
    return {
        "agents": list(AGENT_TYPES),
        "models": models,
        "prefs": prefs,
        "defaults": DEFAULT_PREFS,
        "openrouter_configured": bool(
            settings.openrouter_api_key and "openrouter.ai" in settings.openrouter_base_url
        ),
        "opencode_configured": bool(settings.opencode_api_key),
        "gemini_configured": bool(settings.resolve_gemini_key()),
        "github_configured": bool(settings.github_token),
        "backend": settings.agent_llm_backend,
    }


@router.patch("/workspace/agent-models")
def patch_agent_models(
    body: PrefsBody,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    updated = {}
    for item in body.prefs:
        try:
            row = set_pref(
                db, tenant_id=auth.tenant_id, agent_type=item.agent_type, model_id=item.model_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updated[row.agent_type] = row.model_id
    db.commit()
    return {"status": "ok", "prefs": get_prefs_map(db, auth.tenant_id), "updated": updated}
