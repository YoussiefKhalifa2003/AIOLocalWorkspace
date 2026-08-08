"""Create chat: visibility + mode rules."""

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "create_chat.db"
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

    return TestClient(app), info


def test_member_cannot_create_public_channel(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    r = client.post(
        "/chats",
        headers=headers,
        json={"name": "team-wide", "kind": "channel", "mode": "ops"},
    )
    assert r.status_code == 403


def test_member_can_create_private_ops_or_llm(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    r = client.post(
        "/chats",
        headers=headers,
        json={"name": "omar-ops", "kind": "private", "mode": "ops"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "private"
    assert body["mode"] == "ops"
    assert body["owner_user_id"] == info["user_omar"]

    # Only Omar sees it
    headers_a = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ids_a = {c["id"] for c in client.get("/chats", headers=headers_a).json()}
    assert body["id"] not in ids_a


def test_owner_can_create_public_llm_channel(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    r = client.post(
        "/chats",
        headers=headers,
        json={"name": "ai-lounge", "kind": "channel", "mode": "llm"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "channel"
    assert body["mode"] == "llm"

    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    ids = {c["id"] for c in client.get("/chats", headers=headers_omar).json()}
    assert body["id"] in ids
