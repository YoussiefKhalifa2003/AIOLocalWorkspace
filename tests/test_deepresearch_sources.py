"""Phase 3: DeepResearch may only cite pages it actually retrieved."""

from __future__ import annotations

import json

import pytest

from app.services.research import (
    FetchedDoc,
    SearchHit,
    build_evidence_block,
    fetch_documents,
    html_to_text,
    scrub_unverified_urls,
    sources_markdown,
    url_is_fetchable,
)


def _boot(tmp_path, monkeypatch, **env):
    db_path = tmp_path / "research.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("TAVILY_API_KEY", env.pop("TAVILY_API_KEY", ""))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    return sess, info


def _run_deepresearch(sess, info, text: str) -> str:
    from app.agents.runner import run_deepresearch
    from app.db.models import Artifact, Job, WorkRequest
    from app.services.llm import LLMClient

    db = sess.SessionLocal()
    req = WorkRequest(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        user_id=info["user_a"],
        text=text,
        status="routed",
        pipeline_json=json.dumps(["deepresearch"]),
    )
    db.add(req)
    db.flush()
    job = Job(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        request_id=req.id,
        agent_type="deepresearch",
        status="running",
        payload_json=json.dumps({"text": text}),
        pipeline_index=0,
    )
    db.add(job)
    db.flush()
    run_deepresearch(db, job, LLMClient())
    db.commit()
    art = (
        db.query(Artifact)
        .filter(Artifact.job_id == job.id)
        .order_by(Artifact.id.desc())
        .first()
    )
    content = art.content
    db.close()
    return content


class _FakeProvider:
    name = "fake"

    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    def search(self, query, max_results):
        self.queries.append(query)
        return self.hits


def test_artifact_sources_match_retrieved_docs(tmp_path, monkeypatch):
    sess, info = _boot(tmp_path, monkeypatch)
    hits = [
        SearchHit(url="https://example.org/a", title="Alpha"),
        SearchHit(url="https://example.net/b", title="Beta"),
    ]
    monkeypatch.setattr(
        "app.services.research.get_search_provider", lambda: _FakeProvider(hits)
    )
    monkeypatch.setattr(
        "app.services.research.fetch_documents",
        lambda hs, **kw: [
            FetchedDoc(url=h.url, title=h.title, text=f"body of {h.title}", chars=10, ok=True)
            for h in hs
        ],
    )

    content = _run_deepresearch(sess, info, "compare alpha and beta")
    assert "## Sources" in content
    assert "https://example.org/a" in content
    assert "https://example.net/b" in content
    assert "NO LIVE SOURCES" not in content


def test_no_key_means_no_links_and_a_banner(tmp_path, monkeypatch):
    import re

    sess, info = _boot(tmp_path, monkeypatch)
    content = _run_deepresearch(sess, info, "state of local AI workspaces")
    assert "NO LIVE SOURCES" in content
    assert re.search(r"https?://", content) is None


def test_unverified_urls_are_scrubbed(tmp_path, monkeypatch):
    sess, info = _boot(tmp_path, monkeypatch)
    hits = [SearchHit(url="https://example.org/real", title="Real")]
    monkeypatch.setattr(
        "app.services.research.get_search_provider", lambda: _FakeProvider(hits)
    )
    monkeypatch.setattr(
        "app.services.research.fetch_documents",
        lambda hs, **kw: [
            FetchedDoc(url=h.url, title=h.title, text="real evidence", chars=13, ok=True)
            for h in hs
        ],
    )
    monkeypatch.setattr(
        "app.agents.runner._agent_chat",
        lambda *a, **kw: (
            "See https://fake.example.com/paper and https://example.org/real for detail."
        ),
    )

    content = _run_deepresearch(sess, info, "anything")
    assert "fake.example.com" not in content
    assert "[unverified link removed]" in content
    assert "https://example.org/real" in content


def test_scrub_keeps_only_retrieved_domains():
    docs = [FetchedDoc(url="https://good.org/x", text="t", ok=True)]
    out = scrub_unverified_urls(
        "a https://good.org/y b https://sub.good.org/z c https://bad.com/q", docs
    )
    assert "https://good.org/y" in out
    assert "https://sub.good.org/z" in out
    assert "bad.com" not in out


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.org/x",
    ],
)
def test_ssrf_guard_blocks_private_and_odd_schemes(url):
    ok, why = url_is_fetchable(url)
    assert ok is False
    assert why


def test_fetch_documents_marks_blocked_urls_not_ok():
    docs = fetch_documents([SearchHit(url="http://127.0.0.1:8000/")])
    assert len(docs) == 1
    assert docs[0].ok is False
    assert docs[0].text == ""


def test_html_to_text_strips_scripts_and_tags():
    title, text = html_to_text(
        "<html><head><title>T &amp; T</title></head>"
        "<body><script>var x=1;</script><p>Hello <b>world</b></p></body></html>"
    )
    assert title == "T & T"
    assert "Hello world" in text
    assert "var x" not in text


def test_evidence_block_and_sources_are_numbered():
    docs = [
        FetchedDoc(url="https://a.org/1", title="A", text="alpha", ok=True),
        FetchedDoc(url="https://b.org/2", title="B", text="beta", ok=True),
        FetchedDoc(url="https://c.org/3", title="C", ok=False, error="http 500"),
    ]
    block = build_evidence_block(docs)
    assert block.startswith("EVIDENCE")
    assert "[1] A - https://a.org/1" in block
    assert "[2] B - https://b.org/2" in block
    assert "c.org" not in block

    md = sources_markdown(docs)
    assert "1. [A](https://a.org/1)" in md
    assert "c.org" not in md
