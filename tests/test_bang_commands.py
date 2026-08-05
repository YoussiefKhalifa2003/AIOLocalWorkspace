from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "bang.db"
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


def test_bang_add_whisper_in_general(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]

    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!add Ship login", "speak": False},
    )
    assert r.status_code == 200
    assert "Added objective" in r.json()["replies"][0]["body"]
    assert r.json()["replies"][0].get("visibility") == "whisper"

    # Bare command does nothing in general
    bare = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "add objective Should Not Work", "speak": False},
    )
    assert bare.json()["replies"] == []

    hi = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "hey team", "speak": False},
    )
    assert hi.json()["replies"] == []

    rows_a = client.get(f"/chats/{general}/messages?after_id=0", headers=ha).json()
    assert any("!add Ship login" in (m["body"] or "") for m in rows_a)
    assert any("Added objective" in (m["body"] or "") for m in rows_a)

    rows_o = client.get(f"/chats/{general}/messages?after_id=0", headers=ho).json()
    assert not any("!add Ship login" in (m["body"] or "") for m in rows_o)
    assert not any("Added objective" in (m["body"] or "") for m in rows_o)
    assert any("hey team" in (m["body"] or "") for m in rows_o)


def test_bang_list_private(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv = info["chat_private_omar"]
    r = client.post(
        f"/chats/{priv}/messages",
        headers=ho,
        json={"body": "!list", "speak": False},
    )
    assert r.status_code == 200
    assert "OBJECTIVES" in r.json()["replies"][0]["body"]
