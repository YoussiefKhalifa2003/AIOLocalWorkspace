from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.config import Settings, get_settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Chat client that can target Gemini or OpenRouter based on model id."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _offline_stub(self, messages: list[dict[str, str]]) -> str:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "JSON" in system and "tasks" in system:
            return json.dumps(
                {
                    "tasks": [
                        "Review produced output",
                        "Share with stakeholders",
                        "Schedule follow-up",
                    ]
                }
            )
        if "JSON" in system and "agents" in system.lower():
            return json.dumps({"agents": ["research"], "reason": "offline_stub"})
        if "code review" in system.lower():
            return (
                "OFFLINE CODE REVIEW\n"
                "- Check for missing tests\n"
                "- Watch for secrets in diff\n"
                f"Context excerpt: {user[:400]}"
            )
        if "writing" in system.lower():
            return f"OFFLINE DRAFT\n\nSummary of request:\n{user[:800]}\n\nRecommendation: proceed with a thin vertical slice."
        if "research" in system.lower():
            return (
                "OFFLINE RESEARCH NOTES\n"
                "- Market: local AI workspaces are crowded\n"
                "- Differentiator: auto-routing + quiet rooms + tenant walls\n"
                f"- Query: {user[:400]}"
            )
        return f"OFFLINE RESPONSE\n{user[:1000]}"

    def _endpoint_for(self, model: str) -> tuple[str, str, str]:
        """Return (base_url, api_key, backend_label)."""
        mid = (model or "").strip()
        # OpenRouter free / paid model ids look like provider/name or end with :free
        if mid.endswith(":free") or "/" in mid and not mid.startswith("gemini"):
            key = self.settings.openrouter_api_key.strip()
            base = self.settings.openrouter_base_url.rstrip("/")
            if key and "openrouter.ai" in base:
                return base, key, "openrouter"
        # Gemini path
        gkey = self.settings.resolve_gemini_key()
        gbase = self.settings.resolve_gemini_base()
        if gkey:
            return gbase, gkey, "gemini"
        # Fallback: whatever OpenRouter key/url is (compat)
        return (
            self.settings.openrouter_base_url.rstrip("/"),
            self.settings.openrouter_api_key.strip(),
            "openrouter",
        )

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        force_backend: str | None = None,
    ) -> str:
        if force_backend == "openrouter":
            key = self.settings.openrouter_api_key.strip()
            base = self.settings.openrouter_base_url.rstrip("/")
            backend = "openrouter"
        elif force_backend == "gemini":
            key = self.settings.resolve_gemini_key()
            base = self.settings.resolve_gemini_base()
            backend = "gemini"
        else:
            base, key, backend = self._endpoint_for(model)

        if not key:
            return self._offline_stub(messages)

        # Strip gemini-env sentinel
        use_model = model
        if use_model in ("gemini-env", ""):
            use_model = self.settings.agent_model

        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "AIO Agent Workspace",
        }
        body: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries):
            try:
                with httpx.Client(timeout=90.0) as client:
                    resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 429:
                    time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
                    last_error = LLMError(f"rate limited ({backend}): {resp.text}")
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"LLM HTTP {resp.status_code} ({backend}): {resp.text}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
        raise LLMError(f"LLM failed after retries ({backend}): {last_error}")
