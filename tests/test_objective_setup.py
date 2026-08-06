from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "setup.db"
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


def test_add_includes_setup_marker_and_setup_api(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]
    project_id = info["project_a"]

    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!add Ship station notes", "speak": False},
    )
    assert r.status_code == 200
    body = r.json()["replies"][0]["body"]
    assert "Added objective #" in body
    assert "[[setup:" in body
    oid = int(body.split("[[setup:")[1].split("]]")[0])

    setup = client.put(
        f"/projects/{project_id}/objectives/{oid}/setup",
        headers=ha,
        json={
            "description": "Write clear station notes for the demo.",
            "subtasks": ["Draft outline", "Review with Omar", ""],
        },
    )
    assert setup.status_code == 200, setup.text
    assert setup.json()["description"] == "Write clear station notes for the demo."

    # Marker stripped so the setup card does not remount after refresh
    hist = client.get(f"/chats/{general}/messages", headers=ha).json()
    assert not any("[[setup:" in (m.get("body") or "") for m in hist)

    board = client.get(f"/projects/{project_id}/board", headers=ha).json()
    card = None
    for col in board["columns"]:
        for c in col["cards"]:
            if c["id"] == oid:
                card = c
                break
    assert card is not None
    assert card["description"].startswith("Write clear")
    assert card["checklist_total"] == 2
    assert card["checklist_closed"] == 0
    assert len(card["subtasks"]) == 2
    assert {t["title"] for t in card["subtasks"]} == {"Draft outline", "Review with Omar"}
