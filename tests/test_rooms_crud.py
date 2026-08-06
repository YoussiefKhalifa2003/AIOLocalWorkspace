from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "rooms.db"
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
    from app.db.session import init_db
    from app.services.seed import seed_demo_data

    init_db()
    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info


def test_rename_channel_disabled_and_block_general_delete(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}

    created = client.post(
        "/chats",
        headers=ha,
        json={"name": "design-room", "kind": "channel"},
    )
    assert created.status_code == 200
    cid = created.json()["id"]

    ren = client.patch(f"/chats/{cid}", headers=ha, json={"name": "design"})
    assert ren.status_code == 403

    del_general = client.delete(f"/chats/{info['chat_general']}", headers=ha)
    assert del_general.status_code == 400

    gone = client.delete(f"/chats/{cid}", headers=ha)
    assert gone.status_code == 200
