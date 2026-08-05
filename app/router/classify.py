from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.config import get_settings
from app.services.llm import LLMClient, LLMError

VALID_AGENTS = ("research", "writing", "coding", "code_review", "checklist")


@dataclass
class RoutePlan:
    agents: list[str]
    reason: str
    used_llm: bool


def _keyword_fallback(text: str) -> RoutePlan:
    lower = text.lower()
    agents: list[str] = []

    wants_research = any(
        k in lower for k in ("research", "look up", "find out", "investigate", "competitor")
    )
    wants_coding = any(
        k in lower
        for k in (
            "python",
            "javascript",
            "typescript",
            "java ",
            "code that",
            "write a function",
            "write me a",
            "implement",
            "programming",
            "script",
            "algorithm",
            "leetcode",
            "function that",
            "generate a list",
            "return a list",
            "coding",
            "source code",
            "only give me the code",
        )
    )
    wants_writing = (
        any(k in lower for k in ("draft", "report", "brief", "summarize", "rewrite", "essay", "blog"))
        and not wants_coding
    )
    if (
        any(k in lower for k in ("write", "draft"))
        and not wants_coding
        and not wants_research
    ):
        wants_writing = True

    wants_code_review = (
        any(k in lower for k in ("review", "diff", "pull request", "pr ", "patch", "code review", "bug in"))
        and not wants_coding
    )
    wants_tasks = any(
        k in lower
        for k in (
            "todo",
            "to-do",
            "checklist",
            "task",
            "follow-up",
            "follow up",
            "mark done",
            "check off",
        )
    )

    if wants_coding and not wants_research:
        agents = ["coding"]
        if wants_tasks or "then" in lower:
            agents.append("checklist")
    elif wants_code_review and not wants_research and not wants_writing:
        agents = ["code_review"]
        if wants_tasks or "then" in lower:
            agents.append("checklist")
    elif wants_research and wants_writing:
        agents = ["research", "writing"]
        if wants_tasks:
            agents.append("checklist")
    elif wants_research:
        agents = ["research"]
        if wants_tasks:
            agents.append("checklist")
    elif wants_writing:
        agents = ["writing"]
        if wants_tasks:
            agents.append("checklist")
    elif wants_tasks:
        agents = ["checklist"]
    else:
        agents = ["research"]

    seen: set[str] = set()
    ordered = []
    for a in agents:
        if a in VALID_AGENTS and a not in seen:
            seen.add(a)
            ordered.append(a)
    return RoutePlan(agents=ordered, reason="keyword_fallback", used_llm=False)


def _parse_agents_json(raw: str) -> list[str] | None:
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list):
        return None
    cleaned = [a for a in agents if a in VALID_AGENTS]
    return cleaned or None


def classify_request(text: str, llm: LLMClient | None = None) -> RoutePlan:
    # Prefer keywords for clear coding asks so LLM doesn't pick "writing"
    kw = _keyword_fallback(text)
    if kw.agents and kw.agents[0] == "coding":
        return kw

    settings = get_settings()
    if not settings.resolve_gemini_key() and not settings.openrouter_api_key:
        return kw

    client = llm or LLMClient(settings)
    prompt = (
        "You route work to specialist agents. Return ONLY JSON like "
        '{"agents":["coding"],"reason":"..."}.\n'
        f"Valid agents: {list(VALID_AGENTS)}.\n"
        "coding = write/implement source code. writing = prose/docs/reports. "
        "code_review = review existing diffs/PRs. "
        "If the user asks to write Python/JS/code/functions, use coding NOT writing.\n"
        "Order matters (pipeline). Use the fewest agents needed.\n"
        f"User request:\n{text}"
    )
    try:
        content = client.chat(
            model=settings.router_model,
            messages=[
                {"role": "system", "content": "You are a strict JSON router."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
            force_backend="gemini" if settings.resolve_gemini_key() else "openrouter",
        )
        agents = _parse_agents_json(content)
        if agents:
            return RoutePlan(agents=agents, reason="llm_router", used_llm=True)
    except LLMError:
        pass
    return kw
