from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    content: str
    tokens: int | None = None
    backend: str = ""
    model: str = ""


def _tokens_from_usage(data: dict[str, Any]) -> int | None:
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and total > 0:
        return total
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    try:
        n = int(prompt) + int(completion)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def estimate_tokens(messages: list[dict[str, str]], content: str) -> int:
    n = sum(len(m.get("content") or "") for m in messages) + len(content or "")
    return max(1, n // 4)


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
            return json.dumps({"agents": ["ask"], "reason": "offline_stub"})
        if "code review" in system.lower():
            return (
                "OFFLINE CODE REVIEW\n"
                "- Check for missing tests\n"
                "- Watch for secrets in diff\n"
                f"Context excerpt: {user[:400]}"
            )
        if "writing" in system.lower():
            return f"OFFLINE DRAFT\n\nSummary of request:\n{user[:800]}\n\nRecommendation: proceed with a thin vertical slice."
        if "helpful workplace assistant" in system.lower() or "research agent" in system.lower():
            # Prefer attached file content when present (tests + offline demos)
            if "ATTACHED FILES" in user:
                return (
                    "OFFLINE ASK REPLY\n"
                    "Based on the attached file(s) in the user message:\n"
                    f"{user[user.index('ATTACHED FILES'):user.index('ATTACHED FILES') + 1200]}\n"
                )
            return (
                "OFFLINE ASK REPLY\n"
                "- Market: local AI workspaces are crowded\n"
                "- Differentiator: auto-routing + quiet rooms + tenant walls\n"
                f"- Query: {user[:400]}"
            )
        if "deepresearch" in system.lower() or "rigorous workplace research" in system.lower():
            topic = user[:120].replace("\n", " ")
            return (
                f"# Deep research (offline)\n\n"
                "**No live model and no retrieved sources.** Nothing below is verified, "
                "and no citations or links are available.\n\n"
                f"**Executive summary.** Stub briefing for: {topic}\n\n"
                "## Key findings\n"
                "- Finding A: illustrative only (no live model)\n"
                "- Finding B: attach real keys for full depth\n\n"
                "## Comparison\n\n"
                "| Option | Pros | Cons | Fit |\n"
                "| --- | --- | --- | --- |\n"
                "| Fast path | Cheap, quick | Shallow | Demos |\n"
                "| Deep path | Insightful tables | Slower | Real decisions |\n\n"
                "## ASCII sketch\n"
                "```\n"
                "impact  |####      | low\n"
                "        |########  | med\n"
                "        |##########| high\n"
                "```\n\n"
                "## Recommendations\n"
                "1. Re-run with a live LLM key and TAVILY_API_KEY for cited analysis.\n"
            )
        if "status analyst" in system.lower() or "catch-up" in system.lower():
            # Echo key evidence so tests can assert board facts survived the LLM path
            return (
                "## Status briefing (offline)\n\n"
                "Based on workspace evidence only:\n\n"
                f"{user[:1800]}\n\n"
                "_Quiet in chat does not mean idle if board cards/issues are active._"
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
        return self.chat_result(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            force_backend=force_backend,
        ).content

    def chat_result(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        force_backend: str | None = None,
    ) -> LLMResult:
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
            content = self._offline_stub(messages)
            return LLMResult(
                content=content,
                tokens=estimate_tokens(messages, content),
                backend="offline",
                model=model or "offline",
            )

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
                msg = data["choices"][0]["message"]
                content = msg.get("content")
                if not content:
                    reasoning = msg.get("reasoning") or ""
                    if isinstance(reasoning, str) and reasoning.strip():
                        content = reasoning
                if not content:
                    raise LLMError(f"LLM empty content ({backend}): {resp.text[:400]}")
                tokens = _tokens_from_usage(data) or estimate_tokens(messages, content)
                return LLMResult(
                    content=content,
                    tokens=tokens,
                    backend=backend,
                    model=use_model,
                )
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
        raise LLMError(f"LLM failed after retries ({backend}): {last_error}")
