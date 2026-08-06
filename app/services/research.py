"""Live retrieval for DeepResearch.

Everything DeepResearch cites has to come through here, so the agent can only
reference pages that were actually fetched.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT = ("text/html", "text/plain", "application/xhtml", "application/pdf")
USER_AGENT = "Mozilla/5.0 (compatible; AIO-DeepResearch/1.0)"


class ResearchUnavailable(RuntimeError):
    """Raised when no search provider is configured."""


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    published: str | None = None


@dataclass
class FetchedDoc:
    url: str
    title: str = ""
    text: str = ""
    chars: int = 0
    ok: bool = True
    error: str = ""


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchHit]: ...


@dataclass
class TavilySearch:
    api_key: str
    timeout: float = 20.0
    name: str = field(default="tavily", init=False)

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        if not (self.api_key or "").strip():
            raise ResearchUnavailable("TAVILY_API_KEY is not configured")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "query": query,
                        "max_results": max(1, min(int(max_results), 20)),
                        "search_depth": "advanced",
                        "include_answer": False,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("tavily search failed: %s", exc)
            return []
        if r.status_code >= 400:
            logger.warning("tavily search error %s: %s", r.status_code, r.text[:200])
            return []
        hits: list[SearchHit] = []
        for row in (r.json() or {}).get("results") or []:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=(row.get("title") or "").strip(),
                    snippet=(row.get("content") or "").strip()[:1200],
                    published=row.get("published_date"),
                )
            )
        return hits


def get_search_provider() -> SearchProvider | None:
    settings = get_settings()
    key = (settings.tavily_api_key or "").strip()
    if not key:
        return None
    return TavilySearch(api_key=key, timeout=float(settings.research_fetch_timeout_seconds))


def _host_is_private(host: str) -> bool:
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def url_is_fetchable(url: str) -> tuple[bool, str]:
    parsed = urlparse(url or "")
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme {parsed.scheme or 'none'} not allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "no host"
    if host in {"localhost", "metadata.google.internal"}:
        return False, "host not allowed"
    if _host_is_private(host):
        return False, "private or unresolvable host"
    return True, ""


_SCRIPT_RE = re.compile(r"<(script|style|noscript|template)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")
_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
}


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, plain text). Deliberately simple - no new dependency."""
    raw = html or ""
    title_m = _TITLE_RE.search(raw)
    title = _TAG_RE.sub("", title_m.group(1)).strip() if title_m else ""
    body = _SCRIPT_RE.sub(" ", raw)
    body = re.sub(r"</(p|div|li|h[1-6]|tr|section|article)>", "\n", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = _TAG_RE.sub(" ", body)
    for ent, ch in _ENTITIES.items():
        body = body.replace(ent, ch)
        title = title.replace(ent, ch)
    body = _WS_RE.sub(" ", body)
    body = "\n".join(line.strip() for line in body.splitlines())
    body = _BLANK_RE.sub("\n\n", body).strip()
    return title, body


def fetch_documents(
    hits: list[SearchHit],
    *,
    timeout: float | None = None,
    max_chars: int | None = None,
) -> list[FetchedDoc]:
    settings = get_settings()
    timeout = float(timeout if timeout is not None else settings.research_fetch_timeout_seconds)
    max_chars = int(max_chars if max_chars is not None else settings.research_max_page_chars)
    docs: list[FetchedDoc] = []
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"}
    seen: set[str] = set()

    for hit in hits:
        url = (hit.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        allowed, why = url_is_fetchable(url)
        if not allowed:
            docs.append(FetchedDoc(url=url, title=hit.title, ok=False, error=why))
            continue
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                r = client.get(url)
        except httpx.HTTPError as exc:
            docs.append(
                FetchedDoc(url=url, title=hit.title, ok=False, error=f"fetch failed: {exc}")
            )
            continue
        if r.status_code >= 400:
            docs.append(
                FetchedDoc(url=url, title=hit.title, ok=False, error=f"http {r.status_code}")
            )
            continue
        # A redirect can land somewhere private even when the first host was fine.
        final_ok, final_why = url_is_fetchable(str(r.url))
        if not final_ok:
            docs.append(
                FetchedDoc(url=url, title=hit.title, ok=False, error=f"redirect blocked: {final_why}")
            )
            continue
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and not any(ctype.startswith(c) for c in ALLOWED_CONTENT):
            docs.append(
                FetchedDoc(url=url, title=hit.title, ok=False, error=f"content-type {ctype}")
            )
            continue
        if ctype == "application/pdf":
            text = _pdf_to_text(r.content)
            title = hit.title
        else:
            title, text = html_to_text(r.text)
        text = (text or hit.snippet or "").strip()[:max_chars]
        if not text:
            docs.append(FetchedDoc(url=url, title=hit.title, ok=False, error="empty page"))
            continue
        docs.append(
            FetchedDoc(
                url=str(r.url),
                title=(title or hit.title or url)[:200],
                text=text,
                chars=len(text),
                ok=True,
            )
        )
    return docs


def _pdf_to_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:25])
    except Exception as exc:  # noqa: BLE001 - never fail research on a bad PDF
        logger.info("pdf extract failed: %s", exc)
        return ""


def build_evidence_block(docs: list[FetchedDoc], *, budget: int = 48000) -> str:
    """Numbered evidence the model is allowed to cite, capped to a char budget."""
    good = [d for d in docs if d.ok and d.text]
    if not good:
        return ""
    per_doc = max(1200, budget // max(1, len(good)))
    parts: list[str] = []
    for i, doc in enumerate(good, start=1):
        excerpt = doc.text[:per_doc].strip()
        parts.append(f"[{i}] {doc.title} - {doc.url}\n{excerpt}")
    return "EVIDENCE\n\n" + "\n\n---\n\n".join(parts)


def sources_markdown(docs: list[FetchedDoc]) -> str:
    good = [d for d in docs if d.ok and d.text]
    if not good:
        return "## Sources\n\nNone retrieved.\n"
    lines = ["## Sources", ""]
    for i, doc in enumerate(good, start=1):
        lines.append(f"{i}. [{doc.title or doc.url}]({doc.url})")
    return "\n".join(lines) + "\n"


_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")


def allowed_domains(docs: list[FetchedDoc]) -> set[str]:
    out: set[str] = set()
    for doc in docs:
        if not (doc.ok and doc.text):
            continue
        host = (urlparse(doc.url).hostname or "").lower()
        if host:
            out.add(host.removeprefix("www."))
    return out


def scrub_unverified_urls(text: str, docs: list[FetchedDoc]) -> str:
    """Replace any URL whose host was not actually retrieved."""
    domains = allowed_domains(docs)

    def repl(m: re.Match[str]) -> str:
        host = (urlparse(m.group(0)).hostname or "").lower().removeprefix("www.")
        if host and any(host == d or host.endswith(f".{d}") for d in domains):
            return m.group(0)
        return "[unverified link removed]"

    return _URL_RE.sub(repl, text or "")


def strip_all_urls(text: str) -> str:
    return _URL_RE.sub("[no source available]", text or "")
