from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "mentions.db"
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


def test_people_ping_not_agent(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    general = info["chat_general"]

    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "@Omar can you take login?", "speak": False},
    )
    assert r.status_code == 200
    assert r.json()["replies"] == []

    mentions = client.get("/workspace/mentions", headers=ho).json()
    assert mentions["unread"] >= 1

    # @Research is not an agent in general
    r2 = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "@Research please dig", "speak": False},
    )
    assert r2.json()["replies"] == []

    client.post("/workspace/mentions/read", headers=ho, json={})
    after = client.get("/workspace/mentions", headers=ho).json()
    assert after["unread"] == 0
