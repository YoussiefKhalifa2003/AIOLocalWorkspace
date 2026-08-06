"""OpenCode Zen model catalog + chat completions for AIO agents.

Free models (from OpenCode Zen) are selectable per agent in the UI.
Calls go to https://opencode.ai/zen/v1/chat/completions when OPENCODE_API_KEY is set.
Falls back to `opencode run --model ...` when the CLI is installed and authenticated.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.services.llm import LLMError

# Display name → Zen model id (chat/completions compatible)
OPENCODE_FREE_MODELS: list[dict[str, str]] = [
    {"id": "north-mini-code-free", "label": "North Mini Code", "provider": "opencode", "free": True},
    {"id": "ling-3.0-flash-free", "label": "Ling-3.0-flash", "provider": "opencode", "free": True},
    {"id": "laguna-s-2.1-free", "label": "Laguna S 2.1", "provider": "opencode", "free": True},
    {"id": "deepseek-v4-flash-free", "label": "DeepSeek V4 Flash Free", "provider": "opencode", "free": True},
    {"id": "mimo-v2.5-free", "label": "MiMo V2.5", "provider": "opencode", "free": True},
    {"id": "big-pickle", "label": "Big Pickle", "provider": "opencode", "free": True},
    {"id": "nemotron-3-ultra-free", "label": "Nemotron 3 Ultra", "provider": "opencode", "free": True},
]

FREE_IDS = {m["id"] for m in OPENCODE_FREE_MODELS}
# Also accept opencode/<id> form used by OpenCode CLI config
FREE_IDS |= {f"opencode/{m['id']}" for m in OPENCODE_FREE_MODELS}

AGENT_TYPES = ("research", "writing", "coding", "code_review", "checklist", "status")


@dataclass
class ChatResult:
    content: str
    backend: str
    model: str
    tokens: int | None = None


def normalize_model_id(model: str | None) -> str:
    if not model:
        return ""
    m = model.strip()
    if m.startswith("opencode/"):
        return m.split("/", 1)[1]
    return m


def is_opencode_model(model: str | None) -> bool:
    mid = normalize_model_id(model)
    return mid in {m["id"] for m in OPENCODE_FREE_MODELS} or mid.endswith("-free") or mid == "big-pickle"


def list_free_models() -> list[dict]:
    """Return curated free models; optionally enrich from Zen /models."""
    settings = get_settings()
    base = list(OPENCODE_FREE_MODELS)
    if not settings.opencode_api_key:
        return base
    try:
        url = f"{settings.opencode_base_url.rstrip('/')}/models"
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                url,
                headers={"Authorization": f"Bearer {settings.opencode_api_key}"},
            )
        if r.status_code >= 400:
            return base
        data = r.json()
        remote_ids = {item.get("id") for item in (data.get("data") or []) if item.get("id")}
        # Prefer curated free list order; drop any no longer present if remote returned data
        if remote_ids:
            return [m for m in base if m["id"] in remote_ids] or base
    except Exception:  # noqa: BLE001
        return base
    return base


def chat_opencode_zen(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> ChatResult:
    settings = get_settings()
    mid = normalize_model_id(model)
    if not settings.opencode_api_key:
        raise LLMError(
            "OpenCode model selected but OPENCODE_API_KEY is empty. "
            "Get a key at https://opencode.ai/auth and add it to .env"
        )
    url = f"{settings.opencode_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.opencode_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": mid,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise LLMError(f"OpenCode Zen HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"OpenCode Zen bad response: {data!r}") from exc
    from app.services.llm import _tokens_from_usage, estimate_tokens

    tokens = _tokens_from_usage(data) or estimate_tokens(messages, content)
    return ChatResult(content=content, backend="opencode_zen", model=mid, tokens=tokens)


def chat_opencode_cli(
    *,
    model: str,
    prompt: str,
) -> ChatResult:
    settings = get_settings()
    mid = normalize_model_id(model)
    cli_model = f"opencode/{mid}"
    try:
        proc = subprocess.run(
            [
                settings.opencode_bin,
                "run",
                "--model",
                cli_model,
                "--format",
                "default",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=settings.opencode_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LLMError("opencode CLI not found; set OPENCODE_API_KEY for Zen HTTP instead") from exc
    except subprocess.TimeoutExpired as exc:
        raise LLMError("opencode CLI timed out") from exc
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        raise LLMError(f"opencode exited {proc.returncode}")
    return ChatResult(content=out or "(empty)", backend="opencode_cli", model=mid)


def chat_with_model(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> ChatResult:
    """Route to OpenCode Zen (preferred) or CLI."""
    settings = get_settings()
    if settings.opencode_api_key:
        return chat_opencode_zen(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
    # CLI path: flatten messages into one prompt
    parts = []
    for m in messages:
        parts.append(f"{m.get('role', 'user').upper()}:\n{m.get('content', '')}")
    return chat_opencode_cli(model=model, prompt="\n\n".join(parts))
