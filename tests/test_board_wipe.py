"""Board wipe clears objectives + workspaces for a project."""

from __future__ import annotations


def test_board_wipe_owner_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    db_path = tmp_path / "wipe.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data
    from app.main import app

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    client = TestClient(app)
    pid = info["project_a"]
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}

    before = client.get(f"/projects/{pid}/board", headers=ha).json()
    n = sum(len(c["cards"]) for c in before["columns"])
    assert n > 0

    denied = client.post(f"/projects/{pid}/board/wipe", headers=ho, json={"confirm": True})
    assert denied.status_code == 403

    bad = client.post(f"/projects/{pid}/board/wipe", headers=ha, json={"confirm": False})
    assert bad.status_code == 400

    ok = client.post(f"/projects/{pid}/board/wipe", headers=ha, json={"confirm": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["deleted_objectives"] == n

    after = client.get(f"/projects/{pid}/board", headers=ha).json()
    assert sum(len(c["cards"]) for c in after["columns"]) == 0
