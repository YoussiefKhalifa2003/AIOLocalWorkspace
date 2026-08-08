"""Edit/delete own chat messages - ChatGPT-style truncate on edit."""

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
    data_del = deleted.json()
    assert data_del["message"]["deleted_at"]
    assert data_del["message"]["body"] == ""
    assert data_del["removed_ids"] == []

    # Soft-deleted messages are hidden from normal list
    msgs2 = client.get(f"/chats/{g}/messages?after_id=0", headers=ho).json()
    assert not any(m["id"] == mid for m in msgs2)

    # Sync path: after_id high + since should still return mutation
    sync = client.get(
        f"/chats/{g}/messages?after_id={mid}&since=2020-01-01T00:00:00Z",
        headers=ho,
    ).json()
    assert any(m["id"] == mid and m["deleted_at"] for m in sync)


def test_delete_removes_following_agent_replies(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    g = info["chat_general"]
    tenant_id = info["tenant_a"]
    user_a = info["user_a"]

    ask = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "/ask research about xyz", "speak": False},
    ).json()["user_message_id"]

    # Simulate LLM reply + a later human message that must survive
    import app.db.session as sess
    from app.db.models import ChatMessage

    db = sess.SessionLocal()
    try:
        reply = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=None,
            agent_slug="ask",
            body="here is an answer about xyz",
            visibility="public",
        )
        later = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=user_a,
            agent_slug=None,
            body="unrelated follow-up",
            visibility="public",
        )
        db.add(reply)
        db.add(later)
        db.commit()
        db.refresh(reply)
        db.refresh(later)
        reply_id, later_id = reply.id, later.id
    finally:
        db.close()

    deleted = client.delete(f"/chats/{g}/messages/{ask}", headers=ha)
    assert deleted.status_code == 200
    data = deleted.json()
    assert data["message"]["deleted_at"]
    assert reply_id in data["removed_ids"]
    assert later_id not in data["removed_ids"]

    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ha).json()
    ids = {m["id"] for m in msgs}
    assert ask not in ids
    assert reply_id not in ids
    assert later_id in ids


def test_plain_edit_keeps_later_user_messages(tmp_path, monkeypatch):
    """Typo-style edits must not wipe later user lines in the same streak."""
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
        json={"body": "first (fixed typo)"},
    )
    assert edited.status_code == 200
    data = edited.json()
    assert data["message"]["body"] == "first (fixed typo)"
    assert data["message"]["edited_at"]
    assert data["removed_ids"] == []

    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ha).json()
    ids = [m["id"] for m in msgs]
    assert m1 in ids and m2 in ids and m3 in ids
    assert next(m for m in msgs if m["id"] == m1)["edited_at"]


def test_skill_edit_keeps_later_user_messages(tmp_path, monkeypatch):
    """Even /skill edits must not delete later user lines — only following agent replies."""
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    g = info["chat_general"]
    tenant_id = info["tenant_a"]
    user_a = info["user_a"]

    m1 = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "/ask first question", "speak": False},
    ).json()["user_message_id"]

    import app.db.session as sess
    from app.db.models import ChatMessage

    db = sess.SessionLocal()
    try:
        reply = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=None,
            agent_slug="ask",
            body="agent answer",
            visibility="public",
        )
        later = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=user_a,
            agent_slug=None,
            body="third",
            visibility="public",
        )
        db.add(reply)
        db.add(later)
        db.commit()
        db.refresh(reply)
        db.refresh(later)
        reply_id, later_id = reply.id, later.id
    finally:
        db.close()

    edited = client.patch(
        f"/chats/{g}/messages/{m1}",
        headers=ha,
        json={"body": "/ask first question (rewound)"},
    )
    assert edited.status_code == 200
    data = edited.json()
    assert data["message"]["edited_at"]
    assert reply_id in data["removed_ids"]
    assert later_id not in data["removed_ids"]

    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ha).json()
    ids = [m["id"] for m in msgs]
    assert m1 in ids
    assert reply_id not in ids
    assert later_id in ids


def test_plain_edit_still_drops_following_agent_reply(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    g = info["chat_general"]
    tenant_id = info["tenant_a"]
    user_a = info["user_a"]

    ask = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "question for the bot", "speak": False},
    ).json()["user_message_id"]

    import app.db.session as sess
    from app.db.models import ChatMessage

    db = sess.SessionLocal()
    try:
        reply = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=None,
            agent_slug="ask",
            body="agent answer",
            visibility="public",
        )
        later = ChatMessage(
            tenant_id=tenant_id,
            chat_id=g,
            sender_user_id=user_a,
            agent_slug=None,
            body="user follow-up",
            visibility="public",
        )
        db.add(reply)
        db.add(later)
        db.commit()
        db.refresh(reply)
        db.refresh(later)
        reply_id, later_id = reply.id, later.id
    finally:
        db.close()

    edited = client.patch(
        f"/chats/{g}/messages/{ask}",
        headers=ha,
        json={"body": "question for the bot (clarified)"},
    )
    assert edited.status_code == 200
    data = edited.json()
    assert reply_id in data["removed_ids"]
    assert later_id not in data["removed_ids"]

    msgs = client.get(f"/chats/{g}/messages?after_id=0", headers=ha).json()
    ids = {m["id"] for m in msgs}
    assert ask in ids
    assert reply_id not in ids
    assert later_id in ids
