"""OpenRouter free-model catalog for the Models tab."""

from __future__ import annotations

import httpx

from app.config import get_settings

# Curated free models (updated periodically; live /models merge fills gaps)
CURATED_FREE: list[dict] = [
    {
        "id": "openrouter/free",
        "label": "OpenRouter Free (auto)",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "cohere/north-mini-code:free",
        "label": "North Mini Code",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "inclusionai/ling-3.0-flash:free",
        "label": "Ling 3.0 Flash",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "google/gemma-4-31b-it:free",
        "label": "Gemma 4 31B",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "google/gemma-4-26b-a4b-it:free",
        "label": "Gemma 4 26B",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "openai/gpt-oss-20b:free",
        "label": "GPT-OSS 20B",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "nvidia/nemotron-nano-9b-v2:free",
        "label": "Nemotron Nano 9B",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "nvidia/nemotron-3-nano-30b-a3b:free",
        "label": "Nemotron 3 Nano 30B",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "poolside/laguna-s-2.1:free",
        "label": "Laguna S 2.1",
        "provider": "openrouter",
        "free": True,
    },
    {
        "id": "poolside/laguna-xs-2.1:free",
        "label": "Laguna XS 2.1",
        "provider": "openrouter",
        "free": True,
    },
]

DEFAULT_OPENROUTER_PREFS: dict[str, str] = {
    "coding": "cohere/north-mini-code:free",
    "code_review": "openai/gpt-oss-20b:free",
    "research": "inclusionai/ling-3.0-flash:free",
    "writing": "poolside/laguna-s-2.1:free",
    "checklist": "nvidia/nemotron-nano-9b-v2:free",
}


def is_openrouter_model(model: str | None) -> bool:
    if not model:
        return False
    m = model.strip()
    if m in ("gemini-env",):
        return False
    return m.endswith(":free") or m.startswith("openrouter/") or (
        "/" in m and not m.startswith("gemini")
    )


def list_openrouter_free_models(*, limit: int = 40) -> list[dict]:
    settings = get_settings()
    key = settings.openrouter_api_key.strip()
    base = settings.openrouter_base_url.rstrip("/")
    if not key or "openrouter.ai" not in base:
        return list(CURATED_FREE)

    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                f"{base}/models",
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "AIO Agent Workspace",
                },
            )
        if r.status_code >= 400:
            return list(CURATED_FREE)
        data = r.json()
        live_ids = set()
        rows = []
        for item in data.get("data") or []:
            mid = item.get("id") or ""
            if not mid.endswith(":free"):
                continue
            live_ids.add(mid)
            name = (item.get("name") or mid).replace(":free", "").strip()
            rows.append(
                {
                    "id": mid,
                    "label": name,
                    "provider": "openrouter",
                    "free": True,
                }
            )
        # Always lead with auto router + curated that still exist live
        curated_ids = {m["id"] for m in CURATED_FREE}
        head = []
        for m in CURATED_FREE:
            if m["id"] == "openrouter/free" or m["id"] in live_ids or not live_ids:
                head.append(m)
        rest = [r for r in rows if r["id"] not in curated_ids]
        rest.sort(key=lambda x: x["label"].lower())
        merged = head + rest
        seen: set[str] = set()
        out: list[dict] = []
        for m in merged:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            out.append(m)
            if len(out) >= limit:
                break
        return out or list(CURATED_FREE)
    except Exception:  # noqa: BLE001
        return list(CURATED_FREE)
