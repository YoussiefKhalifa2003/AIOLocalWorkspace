"""Delete chat rules + AI mode skills."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "delete_chat.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info


def test_cannot_delete_general_or_default_private(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    assert (
        client.delete(f"/chats/{info['chat_general']}", headers=headers).status_code == 400
    )
    assert (
        client.delete(f"/chats/{info['chat_private_a']}", headers=headers).status_code == 400
    )


def test_creator_can_delete_own_chat(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    created = client.post(
        "/chats",
        headers=headers,
        json={"name": "temp-ops", "kind": "private", "mode": "ops"},
    ).json()
    cid = created["id"]
    assert client.delete(f"/chats/{cid}", headers=headers).status_code == 200
    ids = {c["id"] for c in client.get("/chats", headers=headers).json()}
    assert cid not in ids


def test_delete_chat_with_messages_mentions_and_presence(tmp_path, monkeypatch):
    """Regression: FK rows used to make DELETE /chats/{id} fail with IntegrityError."""
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    created = client.post(
        "/chats",
        headers=headers,
        json={"name": "busy-room", "kind": "private", "mode": "ops"},
    ).json()
    cid = created["id"]
    # Message + bang reply
    assert (
        client.post(
            f"/chats/{cid}/messages",
            headers=headers,
            json={"body": "!help", "speak": False},
        ).status_code
        == 200
    )
    # Point presence at this chat (same FK that blocked deletes in production)
    assert (
        client.post(
            "/workspace/presence",
            headers=headers,
            json={"active_chat_id": cid, "typing": False},
        ).status_code
        == 200
    )
    r = client.delete(f"/chats/{cid}", headers=headers)
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in client.get("/chats", headers=headers).json()}
    assert cid not in ids


def test_cannot_delete_others_chat(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    headers_a = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    created = client.post(
        "/chats",
        headers=headers_omar,
        json={"name": "omar-secret", "kind": "private", "mode": "llm"},
    ).json()
    # Owner A cannot see/delete Omar's private chat
    assert client.delete(f"/chats/{created['id']}", headers=headers_a).status_code in (403, 404)


def test_llm_mode_accepts_ask_skill(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    created = client.post(
        "/chats",
        headers=headers,
        json={"name": "ai-lab", "kind": "private", "mode": "llm"},
    ).json()
    assert created["mode"] == "llm"
    r = client.post(
        f"/chats/{created['id']}/messages",
        headers=headers,
        json={"body": "/ask reply with the word ping only", "speak": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("pending") is True, "AI mode /ask should queue in the background"
    assert data.get("user_message_id"), "user message should still be saved"


def test_ops_mode_rejects_ask_skill(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    created = client.post(
        "/chats",
        headers=headers,
        json={"name": "ops-lab", "kind": "private", "mode": "ops"},
    ).json()
    assert created["mode"] == "ops"
    r = client.post(
        f"/chats/{created['id']}/messages",
        headers=headers,
        json={"body": "/ask hello", "speak": False},
    )
    assert r.status_code == 200
    body = (r.json().get("replies") or [{}])[0].get("body") or ""
    assert "commands-only" in body.lower() or "skills" in body.lower()
