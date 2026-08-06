from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "members.db"
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


def test_owner_can_delete_member_rename_disabled(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}

    denied = client.delete(f"/workspace/members/{info['user_sara']}", headers=ho)
    assert denied.status_code == 403

    self_del = client.delete(f"/workspace/members/{info['user_a']}", headers=ha)
    assert self_del.status_code == 400

    renamed = client.patch(
        f"/workspace/members/{info['user_omar']}",
        headers=ha,
        json={"name": "OmarX"},
    )
    assert renamed.status_code == 403

    kicked = client.delete(f"/workspace/members/{info['user_sara']}", headers=ha)
    assert kicked.status_code == 200, kicked.text
    assert kicked.json()["deleted"] is True

    members = client.get("/workspace/members", headers=ha).json()
    emails = {m["email"] for m in members}
    assert info["email_sara"] not in emails

    login = client.post(
        "/auth/login",
        json={"email": info["email_sara"], "password": info["demo_password"]},
    )
    assert login.status_code == 401
