"""Chat LLM runs in background so other users can still post."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "async_chat.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info


def test_ask_returns_pending_without_blocking_other_user(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv = info["chat_private_a"]
    general = info["chat_general"]

    captured: list[dict] = []

    def capture_schedule(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "app.services.chat_agent_jobs.schedule_chat_agent_followup",
        capture_schedule,
    )

    t0 = time.time()
    ask = client.post(
        f"/chats/{priv}/messages",
        headers=ha,
        json={"body": "/ask what is concurrency", "speak": False},
    )
    ask_elapsed = time.time() - t0
    assert ask.status_code == 200, ask.text
    data = ask.json()
    assert data.get("pending") is True
    assert data.get("replies") == []
    assert data.get("user_message_id")
    assert ask_elapsed < 2.0, f"/ask blocked for {ask_elapsed:.2f}s"
    assert len(captured) == 1
    assert captured[0]["chat_id"] == priv

    t1 = time.time()
    plain = client.post(
        f"/chats/{general}/messages",
        headers=ho,
        json={"body": "hello from omar while ask is queued", "speak": False},
    )
    plain_elapsed = time.time() - t1
    assert plain.status_code == 200, plain.text
    assert plain.json().get("user_message_id")
    assert plain_elapsed < 2.0, f"plain send blocked for {plain_elapsed:.2f}s"

    # Simulate background finish posting a reply
    from app.db.session import SessionLocal
    from app.services.chat_agent_jobs import _post_agent_chat_message

    db = SessionLocal()
    try:
        _post_agent_chat_message(
            db,
            tenant_id=info["tenant_a"],
            chat_id=priv,
            body="async reply body",
            agent_slug="ask",
            speak=False,
            whisper=True,
            whisper_user_id=info["user_a"],
        )
        db.commit()
    finally:
        db.close()

    rows = client.get(f"/chats/{priv}/messages?after_id=0", headers=ha).json()
    agents = [r for r in rows if r.get("agent_slug") or r.get("agent")]
    assert any("async reply body" in (r.get("body") or "") for r in agents)


def test_plain_bang_still_sync(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]

    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!help", "speak": False},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("pending") is False
    assert data.get("replies"), "bang commands still return an immediate lead reply"
