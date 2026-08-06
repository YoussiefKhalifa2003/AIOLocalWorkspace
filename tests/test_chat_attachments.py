"""Chat attachment API: upload, link, download, auth."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "attach.db"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("UPLOADS_DIR", str(uploads))
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


def test_upload_message_download_and_auth(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    chat = info["chat_private_a"]

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    up = client.post(
        f"/chats/{chat}/attachments",
        headers=ha,
        files={"file": ("bug.png", png, "image/png")},
    )
    assert up.status_code == 200, up.text
    att = up.json()
    assert att["filename"] == "bug.png"
    assert att["url"] == f"/attachments/{att['id']}"

    # attachment-only message
    sent = client.post(
        f"/chats/{chat}/messages",
        headers=ha,
        json={"body": "", "speak": False, "attachment_ids": [att["id"]]},
    )
    assert sent.status_code == 200, sent.text
    mid = sent.json()["user_message_id"]
    assert mid

    msgs = client.get(f"/chats/{chat}/messages?after_id=0", headers=ha).json()
    mine = next(m for m in msgs if m["id"] == mid)
    assert len(mine["attachments"]) == 1
    assert mine["attachments"][0]["filename"] == "bug.png"

    dl = client.get(f"/attachments/{att['id']}", headers=ha)
    assert dl.status_code == 200
    assert dl.content == png

    # Omar cannot access A's private attachment
    assert client.get(f"/attachments/{att['id']}", headers=ho).status_code == 403
    assert (
        client.post(
            f"/chats/{chat}/attachments",
            headers=ho,
            files={"file": ("x.png", png, "image/png")},
        ).status_code
        == 403
    )


def test_reject_bad_type_and_hijack(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    chat_a = info["chat_private_a"]
    general = info["chat_general"]

    bad = client.post(
        f"/chats/{chat_a}/attachments",
        headers=ha,
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 400

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    up = client.post(
        f"/chats/{general}/attachments",
        headers=ha,
        files={"file": ("a.png", png, "image/png")},
    ).json()

    # Omar cannot link A's pending attachment on send
    r = client.post(
        f"/chats/{general}/messages",
        headers=ho,
        json={"body": "hi", "speak": False, "attachment_ids": [up["id"]]},
    )
    assert r.status_code == 400


def test_status_evidence_lists_attachment_and_private_clear_wipes(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    chat = info["chat_private_a"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    att = client.post(
        f"/chats/{chat}/attachments",
        headers=ha,
        files={"file": ("edge-case.png", png, "image/png")},
    ).json()
    assert (
        client.post(
            f"/chats/{chat}/messages",
            headers=ha,
            json={"body": "screenshot of the bug", "speak": False, "attachment_ids": [att["id"]]},
        ).status_code
        == 200
    )

    import app.db.session as sess
    from app.db.models import User
    from app.services.status_evidence import build_user_evidence

    db = sess.SessionLocal()
    try:
        user = db.query(User).filter(User.id == info["user_a"]).one()
        pack = build_user_evidence(
            db,
            tenant_id=info["tenant_a"],
            project_id=info["project_a"],
            user=user,
        )
        assert "edge-case.png" in pack
        assert "Recent attachments" in pack
    finally:
        db.close()

    # Private /clear must not 500 with attachments
    r = client.post(
        f"/chats/{chat}/messages",
        headers=ha,
        json={"body": "/clear", "speak": False},
    )
    assert r.status_code == 200
    assert "cleared" in r.json()["replies"][0]["body"].lower()
    assert client.get(f"/attachments/{att['id']}", headers=ha).status_code == 404
