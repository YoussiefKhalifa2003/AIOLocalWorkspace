from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Artifact, Job, TaskItem, utcnow
from app.services.audit import write_audit
from app.services.coding_backend import record_metric
from app.services.handoff import enqueue_next_pipeline_job, parse_json
from app.services.llm import LLMClient, LLMError
from app.services.rooms import post_agent_output

logger = logging.getLogger(__name__)

CHAT_MARKDOWN = (
    "Format for chat readability: short sections, ## headings sparingly, "
    "bullet lists, **bold** for key points, and fenced ```language code``` blocks. "
    "No HTML. Avoid walls of unformatted text."
)


def _sys(text: str) -> str:
    return f"{text.rstrip()} {CHAT_MARKDOWN}"


def _release_db(db: Session) -> None:
    """Commit so SQLite does not hold a write lock across slow network/LLM I/O."""
    db.commit()


def _save_artifact(db: Session, job: Job, title: str, content: str) -> Artifact:
    art = Artifact(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        agent_type=job.agent_type,
        title=title,
        content=content,
    )
    db.add(art)
    db.flush()
    return art


def _model_for(db: Session, job: Job, agent_type: str) -> tuple[str, str]:
    """Return (model_id, backend) for this job/agent."""
    from app.services.agent_models import resolve_agent_model

    payload = parse_json(job.payload_json, {})
    return resolve_agent_model(
        db,
        tenant_id=job.tenant_id,
        agent_type=agent_type,
        model_tier=payload.get("model_tier"),
        payload_model=payload.get("model") or payload.get("agent_model"),
    )


def _agent_chat(
    db: Session,
    job: Job,
    llm: LLMClient,
    *,
    agent_type: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    from app.config import get_settings
    from app.services.opencode_provider import chat_with_model

    model, backend = _model_for(db, job, agent_type)
    job.model_used = model
    settings = get_settings()
    force = (settings.agent_llm_backend or "auto").lower()
    tokens: int | None = None
    metric_backend = backend

    if force == "gemini":
        backend = "gemini"
        model = settings.agent_model
        job.model_used = model
    elif force == "openrouter":
        backend = "openrouter"
    elif force == "opencode":
        backend = "opencode"

    # Flush model_used etc., then drop the write lock before HTTP to the LLM.
    _release_db(db)

    try:
        if backend == "opencode":
            try:
                result = chat_with_model(
                    model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
                )
                job.model_used = f"{result.backend}:{result.model}"
                tokens = result.tokens
                metric_backend = result.backend
                content = result.content
            except LLMError:
                if force == "opencode":
                    raise
                from app.services.model_tiers import resolve_model

                gem = resolve_model(parse_json(job.payload_json, {}).get("model_tier"), agent_type=agent_type)
                job.model_used = f"gemini-fallback:{gem}"
                lr = llm.chat_result(
                    model=gem,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="gemini",
                )
                tokens = lr.tokens
                metric_backend = lr.backend or "gemini"
                content = lr.content
        elif backend == "openrouter":
            try:
                lr = llm.chat_result(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="openrouter",
                )
                job.model_used = f"openrouter:{model}"
                tokens = lr.tokens
                metric_backend = "openrouter"
                content = lr.content
            except LLMError:
                if force == "openrouter":
                    raise
                from app.services.model_tiers import resolve_model

                gem = resolve_model(parse_json(job.payload_json, {}).get("model_tier"), agent_type=agent_type)
                job.model_used = f"gemini-fallback:{gem}"
                lr = llm.chat_result(
                    model=gem,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_backend="gemini",
                )
                tokens = lr.tokens
                metric_backend = lr.backend or "gemini"
                content = lr.content
        else:
            use_model = model if model != "gemini-env" else settings.agent_model
            job.model_used = use_model
            lr = llm.chat_result(
                model=use_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                force_backend="gemini",
            )
            tokens = lr.tokens
            metric_backend = lr.backend or "gemini"
            content = lr.content

        record_metric(
            db,
            job=job,
            backend=metric_backend,
            model=job.model_used,
            success=True,
            tokens=tokens,
        )
        return content
    except Exception:
        record_metric(
            db,
            job=job,
            backend=metric_backend or backend,
            model=job.model_used,
            success=False,
        )
        raise


def _maybe_post_review_to_general(db: Session, job: Job, content: str) -> None:
    payload = parse_json(job.payload_json, {})
    if not payload.get("post_to_general") and not payload.get("github"):
        return
    from app.services.github_notify import post_general

    repo = payload.get("repo") or ""
    title = payload.get("pr_title") or "PR review"
    pr_url = payload.get("pr_url") or ""
    body = (
        f"**Code review** - {repo}\n"
        f"{title}\n"
        f"{pr_url}\n\n"
        f"{content}"
    )
    post_general(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        body=body,
        agent_slug="code_review",
    )


def run_ask(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="ask",
        messages=[
            {
                "role": "system",
                "content": _sys(
                    "You are a helpful workplace assistant. Answer clearly and directly. "
                    "Use short paragraphs or bullets when useful. "
                    "If ATTACHED FILES are present, treat them as the primary source when the user "
                    "refers to \"this\" / the file / the document. Do not invent unrelated sources."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Ask reply", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="ask",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"ask artifact {art.id}",
    )


_DEEPRESEARCH_SHAPE = (
    "Required shape (markdown):\n"
    "1) Title + 3-6 sentence executive summary\n"
    "2) Key findings (bullets with concrete claims)\n"
    "3) At least one markdown table comparing options, metrics, risks, or timelines\n"
    "4) Deeper analysis: drivers, tradeoffs, edge cases\n"
    "5) Optional chart ONLY when numbers clearly benefit from a visual - never ASCII art. "
    "If (and only if) a chart helps, append one or more fenced blocks exactly like:\n"
    "```aio-chart\n"
    '{"title":"…","type":"bar|line|pie","labels":["A","B"],'
    '"series":[{"name":"…","values":[1,2]}]}\n'
    "```\n"
    "Skip charts for qualitative answers. Chart numbers must come from the evidence "
    "(or be marked estimate). Max 3 charts.\n"
    "6) Recommendations / next steps\n"
    "7) Open questions / what would change the answer\n"
)

_DEEPRESEARCH_SYSTEM = (
    "You are DeepResearch, a rigorous workplace research analyst.\n\n"
    + _DEEPRESEARCH_SHAPE
    + "\nEvidence rules (non-negotiable):\n"
    "- Use ONLY the numbered EVIDENCE block below as external fact material.\n"
    "- Cite every non-obvious claim inline with its evidence number, like [2].\n"
    "- Never output a URL that does not appear in the EVIDENCE block.\n"
    "- Never invent papers, organisations, quotes, dates, or statistics. "
    "If a number is not in the evidence, either omit it or clearly mark it as "
    "'estimate (not from sources)'.\n"
    "- If the evidence does not support part of the question, say so plainly and "
    "list what is missing under 'Open questions'.\n"
    "- Do not write the Sources section yourself; it is appended automatically.\n"
    "If ATTACHED FILES are present, treat them as primary evidence and cite them as [file]."
)

_DEEPRESEARCH_NO_SOURCES_SYSTEM = (
    "You are DeepResearch, a rigorous workplace research analyst working OFFLINE "
    "with no web access and no retrieved sources.\n\n"
    + _DEEPRESEARCH_SHAPE
    + "\nEvidence rules (non-negotiable):\n"
    "- You have NO sources. Do not output any URL, citation, paper title, or "
    "specific statistic presented as fact.\n"
    "- Write from general reasoning only, and label it as unverified.\n"
    "- Be explicit about what would need to be checked against real sources.\n"
    "If ATTACHED FILES are present, treat them as the only evidence."
)

_NO_SOURCES_BANNER = (
    "> **NO LIVE SOURCES:** `TAVILY_API_KEY` is not configured, so this briefing is "
    "unverified, contains no citations, and must not be treated as researched fact.\n"
)


def _drop_model_sources_section(text: str) -> str:
    """The Sources section is generated from real docs, so drop the model's version."""
    import re as _re

    return _re.split(r"\n#{1,3}\s*sources\b.*", text or "", maxsplit=1, flags=_re.I)[0].rstrip()


def _research_queries(db: Session, job: Job, llm: LLMClient, text: str) -> list[str]:
    """1-3 search queries for the request, falling back to the raw text."""
    fallback = [(text or "").strip()[:300]] if (text or "").strip() else []
    try:
        raw = _agent_chat(
            db,
            job,
            llm,
            agent_type="deepresearch",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Turn the user's research request into 1-3 web search queries. "
                        'Reply with JSON only: {"queries": ["...", "..."]}. '
                        "No prose, no markdown fences."
                    ),
                },
                {"role": "user", "content": text[:4000]},
            ],
            max_tokens=200,
            temperature=0.0,
        )
    except LLMError:
        return fallback
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[-1] if "\n" in body else body
    start, end = body.find("{"), body.rfind("}")
    if start >= 0 and end > start:
        body = body[start : end + 1]
    try:
        queries = json.loads(body).get("queries") or []
    except (ValueError, AttributeError):
        return fallback
    cleaned = [str(q).strip() for q in queries if str(q).strip()][:3]
    return cleaned or fallback


def _finalize_deepresearch_content(db: Session, job: Job, content: str) -> str:
    """Render optional aio-chart blocks to PNG attachments; append [[charts:…]] marker."""
    from app.db.models import ChatAttachment, WorkRequest
    from app.services.attachments import save_bytes
    from app.services.chart_render import charts_marker, process_aio_charts

    cleaned, charts = process_aio_charts(content)
    if not charts:
        return cleaned

    payload = parse_json(job.payload_json, {})
    chat_id = payload.get("chat_id")
    try:
        chat_id = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        chat_id = None
    if not chat_id:
        # No chat context (e.g. CLI pipeline without chat) - keep captions, drop images
        return cleaned

    req = db.query(WorkRequest).filter(WorkRequest.id == job.request_id).one_or_none()
    user_id = int(req.user_id) if req else 0
    if not user_id:
        return cleaned

    att_ids: list[int] = []
    for chart in charts:
        try:
            safe, ctype, rel, size = save_bytes(
                chart.png_bytes,
                tenant_id=job.tenant_id,
                chat_id=chat_id,
                filename=chart.filename,
                content_type="image/png",
            )
        except Exception:
            logger.exception("failed to store chart png")
            continue
        row = ChatAttachment(
            tenant_id=job.tenant_id,
            chat_id=chat_id,
            message_id=None,
            uploader_user_id=user_id,
            filename=safe,
            content_type=ctype,
            size_bytes=size,
            storage_path=rel,
        )
        db.add(row)
        db.flush()
        att_ids.append(int(row.id))

    marker = charts_marker(att_ids)
    if marker:
        cleaned = f"{cleaned.rstrip()}\n\n{marker}"
    return cleaned


def run_deepresearch(db: Session, job: Job, llm: LLMClient) -> None:
    from app.services.research import (
        build_evidence_block,
        fetch_documents,
        get_search_provider,
        scrub_unverified_urls,
        sources_markdown,
        strip_all_urls,
    )

    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    settings = get_settings()
    provider = get_search_provider()

    if provider is None:
        body = _agent_chat(
            db,
            job,
            llm,
            agent_type="deepresearch",
            messages=[
                {"role": "system", "content": _sys(_DEEPRESEARCH_NO_SOURCES_SYSTEM)},
                {"role": "user", "content": text},
            ],
            max_tokens=4096,
            temperature=0.35,
        )
        content = f"{_NO_SOURCES_BANNER}\n{strip_all_urls(body)}\n\n## Sources\n\nNone - no live search provider configured.\n"
        write_audit(
            db,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            event_type="deepresearch_sources",
            message="0 sources (no search provider configured)",
        )
    else:
        queries = _research_queries(db, job, llm, text)
        _release_db(db)
        hits: list = []
        seen: set[str] = set()
        for q in queries:
            for hit in provider.search(q, settings.research_max_results):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
        docs = fetch_documents(hits[: settings.research_max_results * 2])
        good = [d for d in docs if d.ok and d.text][: settings.research_max_results]
        evidence = build_evidence_block(good)
        _release_db(db)

        if not good:
            body = _agent_chat(
                db,
                job,
                llm,
                agent_type="deepresearch",
                messages=[
                    {"role": "system", "content": _sys(_DEEPRESEARCH_NO_SOURCES_SYSTEM)},
                    {"role": "user", "content": text},
                ],
                max_tokens=4096,
                temperature=0.35,
            )
            content = (
                "> **NO LIVE SOURCES:** search returned nothing usable for this request, "
                "so the briefing below is unverified and uncited.\n\n"
                f"{strip_all_urls(body)}\n\n## Sources\n\nNone retrieved.\n"
            )
        else:
            body = _agent_chat(
                db,
                job,
                llm,
                agent_type="deepresearch",
                messages=[
                    {"role": "system", "content": _sys(_DEEPRESEARCH_SYSTEM)},
                    {
                        "role": "user",
                        "content": f"RESEARCH REQUEST\n{text}\n\n{evidence}",
                    },
                ],
                max_tokens=4096,
                temperature=0.35,
            )
            cleaned = scrub_unverified_urls(body, good)
            cleaned = _drop_model_sources_section(cleaned)
            content = f"{cleaned.rstrip()}\n\n{sources_markdown(good)}"

        from app.services.research import allowed_domains

        write_audit(
            db,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            event_type="deepresearch_sources",
            message=(
                f"{len(good)} sources from {len(queries)} queries: "
                f"{', '.join(sorted(allowed_domains(good))) or 'none'}"
            ),
        )

    content = _finalize_deepresearch_content(db, job, content)
    art = _save_artifact(db, job, "Deep research", content)
    from app.services.chart_render import pop_charts_marker

    room_body, _ = pop_charts_marker(content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="deepresearch",
        body=room_body,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"deepresearch artifact {art.id}",
    )


def run_writing(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    handoff = parse_json(job.handoff_json, {}) or payload.get("handoff") or {}
    source_bits = [payload.get("text") or ""]
    for aid in handoff.get("artifact_ids") or []:
        from app.db.models import Artifact as Art

        art = (
            db.query(Art)
            .filter(
                Art.id == aid,
                Art.tenant_id == job.tenant_id,
                Art.project_id == job.project_id,
            )
            .one_or_none()
        )
        if art:
            source_bits.append(f"--- prior artifact {art.id} ---\n{art.content}")
    prompt = "\n\n".join(b for b in source_bits if b)
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="writing",
        messages=[
            {
                "role": "system",
                "content": _sys("You are a writing agent. Draft a clear short report or brief."),
            },
            {"role": "user", "content": prompt},
        ],
    )
    art = _save_artifact(db, job, "Draft", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="writing",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"writing artifact {art.id}",
    )


def _workspace_for_job(payload: dict) -> str:
    """The objective's checkout, when it already exists on disk."""
    from app.services.agent_workspace import is_workspace_ready, workspace_path

    objective_id = payload.get("objective_id")
    if not objective_id:
        return ""
    path = workspace_path(int(objective_id))
    return str(path) if is_workspace_ready(path) else ""


def run_coding(db: Session, job: Job, llm: LLMClient) -> None:
    from app.services.coding_backend import WORKSPACE_BACKENDS, get_coding_backend_for

    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    model, backend = _model_for(db, job, "coding")
    settings = get_settings()
    explicit = (payload.get("coding_runner") or "").strip().lower()
    runner = (explicit or settings.coding_backend or "llm").strip().lower()
    # Board/API can force a runner; chat /code usually has no coding_runner.
    forced_cli = explicit in WORKSPACE_BACKENDS

    if runner in WORKSPACE_BACKENDS:
        workspace = _workspace_for_job(payload)
        # Chat /code (or mis-set CODING_BACKEND) without a board workspace:
        # never spawn Codex/Claude with cwd=None - use the LLM coding path.
        if not workspace and not forced_cli:
            logger.info(
                "coding runner %s skipped (no workspace); using LLM for job %s",
                runner,
                job.id,
            )
            runner = "llm"
        elif not workspace and forced_cli:
            msg = (
                f"**{explicit} failed:** no objective workspace ready for this job. "
                f"Send the card to agent backlog from the Board (key `a`) so a checkout exists."
            )
            record_metric(db, job=job, backend=explicit, model=model, success=False)
            job.model_used = f"{explicit}:no-workspace"
            _finish_coding_job(db, job, msg)
            return
        else:
            coding = get_coding_backend_for(runner)
            job.model_used = f"{runner}:{model}"
            try:
                result = coding.run(
                    prompt=text, model=model, llm=llm, workspace=workspace or None
                )
            except LLMError as exc:
                logger.warning("coding runner %s failed (%s)", runner, exc)
                record_metric(db, job=job, backend=runner, model=model, success=False)
                if forced_cli:
                    # Explicit board choice must not silently look like LLM success.
                    err = (
                        f"**{runner} failed:** {exc}\n\n"
                        f"Install the CLI (`aio doctor`), set the API key in `.env`, "
                        f"restart uvicorn, then retry."
                    )
                    job.model_used = f"{runner}:error"
                    _finish_coding_job(db, job, err)
                    return
                # Global CODING_BACKEND=codex with workspace but CLI broken → LLM fallback
                result = None
            if result is not None:
                record_metric(
                    db,
                    job=job,
                    backend=result.backend,
                    model=result.model,
                    success=result.success,
                    duration_ms=result.duration_ms,
                )
                if forced_cli and not result.success:
                    body = (
                        f"**{runner} exited with an error.**\n\n"
                        f"{result.content or '(no output)'}"
                    )
                    _finish_coding_job(db, job, body)
                    return
                _finish_coding_job(db, job, result.content)
                return
            job.model_used = model

    # Prefer OpenCode/LLM chat path for selected models; legacy shell only if coding_backend=opencode AND gemini
    if runner == "opencode" and backend == "gemini":
        job.model_used = model
        coding = get_coding_backend_for("opencode")
        try:
            result = coding.run(prompt=text, model=model, llm=llm)
            record_metric(
                db,
                job=job,
                backend=result.backend,
                model=result.model,
                success=result.success,
                duration_ms=result.duration_ms,
            )
            content = result.content
        except LLMError:
            record_metric(db, job=job, backend=runner, model=model, success=False)
            raise
    else:
        content = _agent_chat(
            db,
            job,
            llm,
            agent_type="coding",
            messages=[
                {
                    "role": "system",
                    "content": _sys(
                        "You are a coding agent. Write correct, minimal source code for the request. "
                        "Prefer a single fenced code block. Brief notes only if needed. "
                        "Do not write marketing prose or essays."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
    _finish_coding_job(db, job, content)


def _finish_coding_job(db: Session, job: Job, content: str) -> None:
    art = _save_artifact(db, job, "Code", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="coding",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"coding artifact {art.id}",
    )


def run_code_review(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("diff") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="code_review",
        messages=[
            {
                "role": "system",
                "content": _sys(
                    "You are a code review agent. List bugs, risks, and suggestions. "
                    "Be specific. If no diff is present, say what is missing."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Code review", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="code_review",
        body=content,
        job_id=job.id,
    )
    _maybe_post_review_to_general(db, job, content)
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"code_review artifact {art.id}",
    )


def run_checklist(db: Session, job: Job, llm: LLMClient) -> None:
    payload = parse_json(job.payload_json, {})
    handoff = parse_json(job.handoff_json, {}) or payload.get("handoff") or {}
    text = payload.get("text") or ""
    prior = []
    for aid in handoff.get("artifact_ids") or []:
        from app.db.models import Artifact as Art

        art = (
            db.query(Art)
            .filter(
                Art.id == aid,
                Art.tenant_id == job.tenant_id,
                Art.project_id == job.project_id,
            )
            .one_or_none()
        )
        if art:
            prior.append(art.content)
    try:
        raw = _agent_chat(
            db,
            job,
            llm,
            agent_type="checklist",
            messages=[
                {
                    "role": "system",
                    "content": (
                        'Return ONLY JSON {"tasks":["..."]} - short actionable follow-ups.'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Request:\n{text}\n\nContext:\n" + "\n".join(prior[-2:]),
                },
            ],
            temperature=0.1,
            max_tokens=500,
        )
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("no tasks")
    except (LLMError, ValueError, json.JSONDecodeError):
        tasks = [
            "Review agent output",
            "Confirm next steps with owner",
            "Update project status",
        ]

    created = []
    from app.db.models import WorkRequest

    req = db.query(WorkRequest).filter(WorkRequest.id == job.request_id).one_or_none()
    owner_id = req.user_id if req else None
    for title in tasks[:12]:
        item = TaskItem(
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            owner_user_id=owner_id,
            title=str(title)[:255],
            done=False,
        )
        db.add(item)
        created.append(str(title))
    db.flush()
    body = "Checklist updated:\n" + "\n".join(f"- [ ] {t}" for t in created)
    art = _save_artifact(db, job, "Checklist", body)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="checklist",
        body=body,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"checklist artifact {art.id}; {len(created)} tasks",
    )


def run_status(db: Session, job: Job, llm: LLMClient) -> None:
    from app.services.status_evidence import status_system_prompt

    payload = parse_json(job.payload_json, {})
    text = payload.get("text") or payload.get("source_text") or ""
    content = _agent_chat(
        db,
        job,
        llm,
        agent_type="status",
        messages=[
            {
                "role": "system",
                "content": _sys(status_system_prompt()),
            },
            {"role": "user", "content": text},
        ],
    )
    art = _save_artifact(db, job, "Status briefing", content)
    post_agent_output(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        agent_type="status",
        body=content,
        job_id=job.id,
    )
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="agent_done",
        message=f"status artifact {art.id}",
    )


AGENTS = {
    "ask": run_ask,
    "deepresearch": run_deepresearch,
    "research": run_ask,  # legacy jobs / prefs
    "writing": run_writing,
    "coding": run_coding,
    "code_review": run_code_review,
    "checklist": run_checklist,
    "status": run_status,
}


def execute_job(db: Session, job: Job, llm: LLMClient | None = None) -> None:
    client = llm or LLMClient()
    job.status = "running"
    job.started_at = utcnow()
    db.flush()
    write_audit(
        db,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        request_id=job.request_id,
        job_id=job.id,
        event_type="job_started",
        message=f"started {job.agent_type}",
    )
    # Commit before long LLM / web fetches so SQLite does not hold a write lock
    # for minutes — otherwise other clients' presence/message polls ReadTimeout.
    db.commit()
    try:
        handler = AGENTS.get(job.agent_type)
        if handler is None:
            raise LLMError(f"unknown agent_type {job.agent_type}")
        handler(db, job, client)
        job.status = "done"
        job.finished_at = utcnow()
        job.error = None
        enqueue_next_pipeline_job(db, job)
    except Exception as exc:  # noqa: BLE001 - record any agent failure
        job.status = "failed"
        job.finished_at = utcnow()
        job.error = str(exc)
        write_audit(
            db,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            request_id=job.request_id,
            job_id=job.id,
            event_type="job_failed",
            message=str(exc),
        )
    db.flush()
