"""Owner /status evidence includes private-room skill traffic."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.models import Chat, ChatMessage, User
from app.services.status_evidence import build_user_evidence


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "status_ev.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
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


def test_evidence_includes_private_room_help_ask(tmp_path, monkeypatch):
    _, info, SessionLocal = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        omar = db.query(User).filter(User.id == info["user_omar"]).one()
        priv = (
            db.query(Chat)
            .filter(Chat.kind == "private", Chat.owner_user_id == omar.id)
            .one()
        )
        ask = (
            "/web i seem to be having an issue with fixing app/api/chats.py "
            "so can you give me more information on that"
        )
        db.add(
            ChatMessage(
                tenant_id=info["tenant_a"],
                chat_id=priv.id,
                sender_user_id=omar.id,
                agent_slug=None,
                body=ask,
                visibility="public",
            )
        )
        db.add(
            ChatMessage(
                tenant_id=info["tenant_a"],
                chat_id=priv.id,
                sender_user_id=None,
                agent_slug="research",
                body="## app/api/chats.py — Auth Edge Case Help\nHere is diagnostic guidance…",
                visibility="public",
            )
        )
        db.commit()

        pack = build_user_evidence(
            db,
            tenant_id=info["tenant_a"],
            project_id=info["project_a"],
            user=omar,
        )
        assert "Private-room activity" in pack
        assert "app/api/chats.py" in pack
        assert "having an issue" in pack
        assert "agent:research" in pack
        assert "Auth Edge Case Help" in pack
    finally:
        db.close()
