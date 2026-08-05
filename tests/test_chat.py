from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "privacy.db"
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


def test_private_room_privacy_gate_a(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers_a = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}

    # Login via join key for omar to get session key is already api_key_omar from seed
    chats_a = client.get("/chats", headers=headers_a).json()
    kinds_a = {(c["name"], c["kind"]) for c in chats_a}
    assert ("general", "channel") in kinds_a
    assert any(c["kind"] == "private" and c["owner_user_id"] == info["user_a"] for c in chats_a)
    assert not any(c["owner_user_id"] == info["user_omar"] and c["kind"] == "private" for c in chats_a)

    chats_omar = client.get("/chats", headers=headers_omar).json()
    assert any(c["owner_user_id"] == info["user_omar"] for c in chats_omar)
    assert not any(c["owner_user_id"] == info["user_a"] and c["kind"] == "private" for c in chats_omar)

    priv_a = info["chat_private_a"]
    priv_omar = info["chat_private_omar"]
    general = info["chat_general"]

    # Omar cannot read/post A's private
    assert client.get(f"/chats/{priv_a}/messages", headers=headers_omar).status_code == 403
    assert (
        client.post(
            f"/chats/{priv_a}/messages",
            headers=headers_omar,
            json={"body": "peek", "speak": False},
        ).status_code
        == 403
    )

    # Owner A cannot read Omar's private
    assert client.get(f"/chats/{priv_omar}/messages", headers=headers_a).status_code == 403

    # Both can use general
    assert client.get(f"/chats/{general}/messages", headers=headers_a).status_code == 200
    assert client.get(f"/chats/{general}/messages", headers=headers_omar).status_code == 200
    r = client.post(
        f"/chats/{general}/messages",
        headers=headers_omar,
        json={"body": "just saying hi to the team", "speak": False},
    )
    assert r.status_code == 200
    assert r.json()["replies"] == []

    # A can post in own private
    r2 = client.post(
        f"/chats/{priv_a}/messages",
        headers=headers_a,
        json={"body": "secret work", "speak": False},
    )
    assert r2.status_code == 200


def test_chat_help_and_invite(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]

    r = client.post(
        f"/chats/{general}/messages",
        headers=headers,
        json={"body": "/help", "speak": False},
    )
    assert r.status_code == 200
    assert "add objective" in r.json()["replies"][0]["body"]

    inv = client.post(
        "/workspace/invite",
        headers=headers,
        json={"email": "colleague@local.test"},
    )
    assert inv.status_code == 200
    assert inv.json()["api_key_issued"] == "demo-key-a"
    assert inv.json().get("private_chat_id")

    # Colleague logs in with join key
    login = client.post(
        "/auth/login",
        json={"email": "colleague@local.test", "api_key": "demo-key-a"},
    )
    assert login.status_code == 200
    col_key = login.json()["api_key"]
    col_headers = {"X-API-Key": col_key, "X-User-Email": "colleague@local.test"}
    col_chats = client.get("/chats", headers=col_headers).json()
    assert any(c["kind"] == "channel" and c["name"] == "general" for c in col_chats)
    assert any(c["kind"] == "private" for c in col_chats)
    # cannot see owner's private
    assert client.get(f"/chats/{info['chat_private_a']}/messages", headers=col_headers).status_code == 403

    created = client.post(
        f"/chats/{general}/messages",
        headers=headers,
        json={"body": "/create chat design-room", "speak": False},
    )
    assert created.status_code == 200
    assert created.json()["created_chat_id"]

    direct = client.post("/chats", headers=headers, json={"name": "via-api", "kind": "channel"})
    assert direct.status_code == 200
    new_id = direct.json()["id"]
    deleted = client.delete(f"/chats/{new_id}", headers=headers)
    assert deleted.status_code == 200
