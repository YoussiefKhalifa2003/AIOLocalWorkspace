"""Presence + typing API tests."""

from datetime import timedelta

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "presence.db"
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


def _by_id(users: list[dict], user_id: int) -> dict:
    return next(u for u in users if int(u["user_id"]) == int(user_id))


def test_heartbeat_online_and_offline(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]

    r = client.post("/workspace/presence", headers=headers, json={"chat_id": general})
    assert r.status_code == 200

    users = client.get("/workspace/presence", headers=headers).json()["users"]
    me = _by_id(users, info["user_a"])
    assert me["online"] is True
    assert "room" not in me
    assert "chat_id" not in me

    # Expire heartbeat
    from app.db import session as sess
    from app.db.models import UserPresence, utcnow

    db = sess.SessionLocal()
    row = db.query(UserPresence).filter(UserPresence.user_id == info["user_a"]).one()
    row.last_seen = utcnow() - timedelta(seconds=60)
    db.commit()
    db.close()

    users = client.get("/workspace/presence", headers=headers).json()["users"]
    me = _by_id(users, info["user_a"])
    assert me["online"] is False


def test_private_heartbeat_still_online(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers_a = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv_a = info["chat_private_a"]

    assert client.post(
        "/workspace/presence", headers=headers_a, json={"chat_id": priv_a}
    ).status_code == 200

    users = client.get("/workspace/presence", headers=headers_omar).json()["users"]
    a = _by_id(users, info["user_a"])
    assert a["online"] is True
    assert a.get("typing_chat_id") is None


def test_cannot_heartbeat_others_private(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv_a = info["chat_private_a"]

    r = client.post(
        "/workspace/presence", headers=headers_omar, json={"chat_id": priv_a}
    )
    assert r.status_code == 403


def test_typing_channel_ok_private_rejected(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]
    priv = info["chat_private_a"]

    r = client.post(
        "/workspace/presence",
        headers=headers,
        json={"chat_id": general, "typing": True},
    )
    assert r.status_code == 200
    users = client.get("/workspace/presence", headers=headers).json()["users"]
    me = _by_id(users, info["user_a"])
    assert me["typing_chat_id"] == general

    bad = client.post(
        "/workspace/presence",
        headers=headers,
        json={"chat_id": priv, "typing": True},
    )
    assert bad.status_code == 400


def test_typing_expires(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]

    assert client.post(
        "/workspace/presence",
        headers=headers,
        json={"chat_id": general, "typing": True},
    ).status_code == 200

    from app.db import session as sess
    from app.db.models import UserPresence, utcnow

    db = sess.SessionLocal()
    row = db.query(UserPresence).filter(UserPresence.user_id == info["user_a"]).one()
    row.typing_until = utcnow() - timedelta(seconds=1)
    db.commit()
    db.close()

    users = client.get("/workspace/presence", headers=headers).json()["users"]
    me = _by_id(users, info["user_a"])
    assert me["typing_chat_id"] is None


def test_two_user_presence_smoke(tmp_path, monkeypatch):
    """Two sessions: online + typing; clear typing; offline TTL."""
    client, info = _boot(tmp_path, monkeypatch)
    headers_a = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]
    priv_a = info["chat_private_a"]

    assert client.post(
        "/workspace/presence", headers=headers_a, json={"chat_id": general, "typing": True}
    ).status_code == 200
    assert client.post(
        "/workspace/presence", headers=headers_omar, json={"chat_id": general}
    ).status_code == 200

    users = client.get("/workspace/presence", headers=headers_omar).json()["users"]
    a = _by_id(users, info["user_a"])
    omar = _by_id(users, info["user_omar"])
    assert a["online"] and a["typing_chat_id"] == general
    assert omar["online"] is True

    assert client.post(
        "/workspace/presence", headers=headers_a, json={"chat_id": priv_a, "typing": False}
    ).status_code == 200
    users = client.get("/workspace/presence", headers=headers_omar).json()["users"]
    a = _by_id(users, info["user_a"])
    assert a["online"] is True
    assert a["typing_chat_id"] is None

    from app.db import session as sess
    from app.db.models import UserPresence, utcnow

    db = sess.SessionLocal()
    row = db.query(UserPresence).filter(UserPresence.user_id == info["user_omar"]).one()
    row.last_seen = utcnow() - timedelta(seconds=60)
    db.commit()
    db.close()
    users = client.get("/workspace/presence", headers=headers_a).json()["users"]
    omar = _by_id(users, info["user_omar"])
    assert omar["online"] is False


def test_explicit_offline_is_immediate(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    headers = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    headers_omar = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]

    assert client.post(
        "/workspace/presence", headers=headers, json={"chat_id": general}
    ).status_code == 200
    assert _by_id(
        client.get("/workspace/presence", headers=headers_omar).json()["users"],
        info["user_a"],
    )["online"] is True

    assert client.post(
        "/workspace/presence", headers=headers, json={"offline": True}
    ).status_code == 200
    assert _by_id(
        client.get("/workspace/presence", headers=headers_omar).json()["users"],
        info["user_a"],
    )["online"] is False


def test_format_typing_names():
    from app.cli_pkg.tui.views.chat import _format_typing_names

    assert _format_typing_names([]) == ""
    assert _format_typing_names(["Ali"]) == "Ali is typing…"
    assert _format_typing_names(["Ali", "Sam"]) == "Ali and Sam are typing…"
    assert _format_typing_names(["A", "B", "C"]) == "A, B, and C are typing…"
    assert _format_typing_names(["A", "B", "C", "D"]) == "A, B, and 2 others…"
