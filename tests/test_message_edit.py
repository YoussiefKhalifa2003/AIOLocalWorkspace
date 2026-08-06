"""Edit/delete own chat messages — ChatGPT-style truncate on edit."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "editmsg.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
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

    return TestClient(app), info


def test_edit_and_delete_own_message(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    g = info["chat_general"]

    sent = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "hello team", "speak": False},
    )
    assert sent.status_code == 200
    mid = sent.json()["user_message_id"]

    # Omar cannot edit A's message
    assert (
        client.patch(
            f"/chats/{g}/messages/{mid}",
            headers=ho,
            json={"body": "hijack"},
        ).status_code
        == 403
    )

    edited = client.patch(
        f"/chats/{g}/messages/{mid}",
        headers=ha,
        json={"body": "hello team (fixed)"},
    )
    assert edited.status_code == 200
    data = edited.json()
    assert data["message"]["body"] == "hello team (fixed)"
    assert data["message"]["edited_at"]
    assert data["removed_ids"] == []

    # Everyone sees the edit
    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ho).json()
    mine = next(m for m in msgs if m["id"] == mid)
    assert mine["body"] == "hello team (fixed)"
    assert mine["edited_at"]

    deleted = client.delete(f"/chats/{g}/messages/{mid}", headers=ha)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"]
    assert deleted.json()["body"] == ""

    # Soft-deleted messages are hidden from normal list
    msgs2 = client.get(f"/chats/{g}/messages?after_id=0", headers=ho).json()
    assert not any(m["id"] == mid for m in msgs2)

    # Sync path: after_id high + since should still return mutation
    sync = client.get(
        f"/chats/{g}/messages?after_id={mid}&since=2020-01-01T00:00:00Z",
        headers=ho,
    ).json()
    assert any(m["id"] == mid and m["deleted_at"] for m in sync)


def test_edit_truncates_later_messages(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    g = info["chat_general"]

    m1 = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "first", "speak": False},
    ).json()["user_message_id"]
    m2 = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "second", "speak": False},
    ).json()["user_message_id"]
    m3 = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "third", "speak": False},
    ).json()["user_message_id"]

    edited = client.patch(
        f"/chats/{g}/messages/{m1}",
        headers=ha,
        json={"body": "first (rewound)"},
    )
    assert edited.status_code == 200
    data = edited.json()
    assert data["message"]["body"] == "first (rewound)"
    assert data["message"]["edited_at"]
    assert set(data["removed_ids"]) == {m2, m3}

    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ha).json()
    ids = [m["id"] for m in msgs]
    assert m1 in ids
    assert m2 not in ids
    assert m3 not in ids
    assert next(m for m in msgs if m["id"] == m1)["body"] == "first (rewound)"

    # Other clients learn about truncations via since=
    sync = client.get(
        f"/chats/{g}/messages?after_id={m3}&since=2020-01-01T00:00:00Z",
        headers=ha,
    ).json()
    deleted_ids = {m["id"] for m in sync if m.get("deleted_at")}
    assert m2 in deleted_ids and m3 in deleted_ids
