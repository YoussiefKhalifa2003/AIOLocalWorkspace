"""DeepResearch chart attachments wire into chat replies."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "charts.db"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("UPLOADS_DIR", str(uploads))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from app.db.session import init_db
    from app.services.seed import seed_demo_data

    init_db()
    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info, sess.SessionLocal


def test_finalize_deepresearch_creates_attachments(tmp_path, monkeypatch):
    client, info, SessionLocal = _boot(tmp_path, monkeypatch)
    from app.agents.runner import _finalize_deepresearch_content
    from app.db.models import ChatAttachment, Job, WorkRequest
    from app.services.chart_render import pop_charts_marker

    db = SessionLocal()
    req = WorkRequest(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        user_id=info["user_a"],
        text="compare",
        status="routed",
        pipeline_json='["deepresearch"]',
    )
    db.add(req)
    db.flush()
    job = Job(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        request_id=req.id,
        agent_type="deepresearch",
        status="running",
        payload_json=json.dumps(
            {"text": "compare", "chat_id": info["chat_private_a"]}
        ),
    )
    db.add(job)
    db.flush()

    content = (
        "## Compare\n\nNumbers below.\n\n"
        "```aio-chart\n"
        '{"title":"Scores","type":"bar","labels":["A","B"],'
        '"series":[{"name":"pts","values":[3,7]}]}\n'
        "```\n\n## Sources\n\nNone.\n"
    )
    out = _finalize_deepresearch_content(db, job, content)
    db.commit()

    cleaned, ids = pop_charts_marker(out)
    assert "```aio-chart" not in cleaned
    assert "Chart: Scores" in cleaned
    assert len(ids) == 1
    row = db.query(ChatAttachment).filter(ChatAttachment.id == ids[0]).one()
    assert row.content_type == "image/png"
    assert row.message_id is None
    assert row.chat_id == info["chat_private_a"]
    db.close()


def test_post_reply_links_chart_attachments(tmp_path, monkeypatch):
    client, info, SessionLocal = _boot(tmp_path, monkeypatch)
    from app.db.models import Chat, ChatAttachment, User
    from app.services.attachments import save_bytes
    from app.services.auth import AuthContext
    from app.services.chart_render import charts_marker
    from app.services.orchestrator import IntentResult, _post_lead_reply

    db = SessionLocal()
    chat_id = info["chat_private_a"]
    safe, ctype, rel, size = save_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        tenant_id=info["tenant_a"],
        chat_id=chat_id,
        filename="chart.png",
        content_type="image/png",
    )
    att = ChatAttachment(
        tenant_id=info["tenant_a"],
        chat_id=chat_id,
        message_id=None,
        uploader_user_id=info["user_a"],
        filename=safe,
        content_type=ctype,
        size_bytes=size,
        storage_path=rel,
    )
    db.add(att)
    db.flush()

    chat = db.query(Chat).filter(Chat.id == chat_id).one()
    user = db.query(User).filter(User.id == info["user_a"]).one()
    auth = AuthContext(user=user, tenant_id=user.tenant_id, user_id=user.id)
    body = f"Research done\n\n{charts_marker([att.id])}"
    replies, _, _, _ = _post_lead_reply(
        db,
        auth=auth,
        chat=chat,
        result=IntentResult(True, body, "deepresearch"),
        speak=False,
    )
    db.commit()
    assert len(replies) == 1
    msg = replies[0]
    assert "[[charts:" not in (msg.body or "")
    db.refresh(att)
    assert att.message_id == msg.id

    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    rows = client.get(f"/chats/{chat_id}/messages?after_id=0", headers=ha).json()
    mine = next(m for m in rows if m["id"] == msg.id)
    assert len(mine["attachments"]) == 1
    assert mine["attachments"][0]["content_type"] == "image/png"
    db.close()
