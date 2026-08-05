from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "skills.db"
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

    return TestClient(app), info


def test_skills_private_only(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv = info["chat_private_omar"]
    general = info["chat_general"]

    # Plain private note — no AI
    quiet = client.post(
        f"/chats/{priv}/messages",
        headers=ho,
        json={"body": "thinking out loud about auth", "speak": False},
    )
    assert quiet.json()["replies"] == []

    # Skill runs (offline stub without keys)
    skill = client.post(
        f"/chats/{priv}/messages",
        headers=ho,
        json={"body": "/write short intro", "speak": False},
    )
    assert skill.status_code == 200
    assert skill.json()["replies"]
    assert "writing" in skill.json()["replies"][0]["body"].lower() or "OFFLINE" in skill.json()["replies"][0]["body"] or "Lead" in skill.json()["replies"][0]["body"]

    # General slash — no AI
    g = client.post(
        f"/chats/{general}/messages",
        headers=ho,
        json={"body": "/code fix login", "speak": False},
    )
    assert g.json()["replies"] == []
