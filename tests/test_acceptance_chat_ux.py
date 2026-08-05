"""Phase 7 acceptance — dual-user flows."""

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "accept.db"
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


def test_phase7_acceptance_script(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]
    priv_a = info["chat_private_a"]

    # 1. General chat both see
    client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "hello team", "speak": False},
    )
    assert any(
        m["body"] == "hello team"
        for m in client.get(f"/chats/{general}/messages?after_id=0", headers=ho).json()
    )

    # 2. Ping
    client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "@Omar hello", "speak": False},
    )
    assert client.get("/workspace/mentions", headers=ho).json()["unread"] >= 1

    # 3. Whisper command
    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!add Ship login", "speak": False},
    )
    assert "Added objective" in r.json()["replies"][0]["body"]
    bodies_o = [m["body"] for m in client.get(f"/chats/{general}/messages?after_id=0", headers=ho).json()]
    assert "!add Ship login" not in bodies_o
    assert not any("Added objective" in b for b in bodies_o)

    # 4. Set status
    oid = int(r.json()["replies"][0]["body"].split("#")[1].split(":")[0])
    r2 = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": f"!set {oid} doing", "speak": False},
    )
    assert f"→ doing" in r2.json()["replies"][0]["body"]

    # 5. Private skill + plain quiet
    assert (
        client.post(
            f"/chats/{priv_a}/messages",
            headers=ha,
            json={"body": "thinking out loud", "speak": False},
        ).json()["replies"]
        == []
    )
    skill = client.post(
        f"/chats/{priv_a}/messages",
        headers=ha,
        json={"body": "/write short intro", "speak": False},
    )
    assert skill.json()["replies"]

    # 7. Negative: /code in general
    assert (
        client.post(
            f"/chats/{general}/messages",
            headers=ho,
            json={"body": "/code fix x", "speak": False},
        ).json()["replies"]
        == []
    )
