"""Phase 1: whisper visibility filters."""

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "whisper.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
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
    from app.main import app

    return TestClient(app), info, sess.SessionLocal


def test_whisper_hidden_from_other_user(tmp_path, monkeypatch):
    client, info, SessionLocal = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]

    from app.db.models import ChatMessage
    from app.services.chat_visibility import mark_whisper

    db = SessionLocal()
    public = ChatMessage(
        tenant_id=info["tenant_a"],
        chat_id=general,
        sender_user_id=info["user_a"],
        body="hello team",
        visibility="public",
    )
    whisper = ChatMessage(
        tenant_id=info["tenant_a"],
        chat_id=general,
        sender_user_id=info["user_a"],
        body="!secret command",
        visibility="public",
    )
    db.add(public)
    db.add(whisper)
    db.flush()
    mark_whisper(whisper, info["user_a"])
    bot = ChatMessage(
        tenant_id=info["tenant_a"],
        chat_id=general,
        sender_user_id=None,
        agent_slug="lead",
        body="Added objective #1",
    )
    db.add(bot)
    db.flush()
    mark_whisper(bot, info["user_a"])
    db.commit()
    db.close()

    rows_a = client.get(f"/chats/{general}/messages?after_id=0", headers=ha).json()
    bodies_a = [m["body"] for m in rows_a]
    assert "hello team" in bodies_a
    assert "!secret command" in bodies_a
    assert "Added objective #1" in bodies_a
    assert any(m.get("visibility") == "whisper" for m in rows_a)

    rows_o = client.get(f"/chats/{general}/messages?after_id=0", headers=ho).json()
    bodies_o = [m["body"] for m in rows_o]
    assert "hello team" in bodies_o
    assert "!secret command" not in bodies_o
    assert "Added objective #1" not in bodies_o
