from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "clear.db"
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


def test_clear_personal_in_general_and_slash(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    g = info["chat_general"]

    # Create history + mention (FK that used to break wipe)
    assert (
        client.post(
            f"/chats/{g}/messages",
            headers=ha,
            json={"body": "@Omar hello from A", "speak": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/chats/{g}/messages",
            headers=ho,
            json={"body": "omar replies", "speak": False},
        ).status_code
        == 200
    )

    cleared = client.post(
        f"/chats/{g}/messages",
        headers=ha,
        json={"body": "/clear", "speak": False},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cleared"] is True
    assert "only" in cleared.json()["replies"][0]["body"].lower()

    # A no longer sees old public messages
    rows_a = client.get(f"/chats/{g}/messages", headers=ha).json()
    bodies_a = " ".join(m["body"] for m in rows_a)
    assert "hello from A" not in bodies_a
    assert "omar replies" not in bodies_a

    # Omar still sees history
    rows_o = client.get(f"/chats/{g}/messages", headers=ho).json()
    bodies_o = " ".join(m["body"] for m in rows_o)
    assert "omar replies" in bodies_o
    assert "hello from A" in bodies_o


def test_clear_private_wipes_room(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv = info["chat_private_omar"]

    client.post(
        f"/chats/{priv}/messages",
        headers=ho,
        json={"body": "private note keep?", "speak": False},
    )
    r = client.post(
        f"/chats/{priv}/messages",
        headers=ho,
        json={"body": "!clear", "speak": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["cleared"] is True
    rows = client.get(f"/chats/{priv}/messages", headers=ho).json()
    bodies = " ".join(m["body"] for m in rows)
    assert "private note keep?" not in bodies
