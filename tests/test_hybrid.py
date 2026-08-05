from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "hybrid.db"
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


def test_owned_objectives_and_issues_and_status(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    hs = {"X-API-Key": info["api_key_sara"], "X-User-Email": info["email_sara"]}
    priv_o = info["chat_private_omar"]
    priv_s = info["chat_private_sara"]
    general = info["chat_general"]

    # Omar adds objective + issue in private room
    r = client.post(
        f"/chats/{priv_o}/messages",
        headers=ho,
        json={"body": "add objective Ship metro research", "speak": False},
    )
    assert r.status_code == 200
    assert "Added objective" in r.json()["replies"][0]["body"]

    r = client.post(
        f"/chats/{priv_o}/messages",
        headers=ho,
        json={"body": "log issue API auth blocked", "speak": False},
    )
    assert r.status_code == 200
    assert "Logged issue" in r.json()["replies"][0]["body"]

    # Sara cannot see Omar's objectives
    r = client.post(
        f"/chats/{priv_s}/messages",
        headers=hs,
        json={"body": "show objectives", "speak": False},
    )
    assert "(none)" in r.json()["replies"][0]["body"] or "YOUR OBJECTIVES" in r.json()["replies"][0]["body"]
    assert "Ship metro" not in r.json()["replies"][0]["body"]

    # Sara cannot view Omar status
    r = client.post(
        f"/chats/{priv_s}/messages",
        headers=hs,
        json={"body": "@Omar status", "speak": False},
    )
    assert "owner" in r.json()["replies"][0]["body"].lower()

    # Owner can catch up on Omar
    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "/@Omar status", "speak": False},
    )
    body = r.json()["replies"][0]["body"]
    assert "Ship metro" in body
    assert "API auth" in body

    # Team report owner-only
    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "/@team report", "speak": False},
    )
    assert "TEAM REPORT" in r.json()["replies"][0]["body"]
    assert "omar@" in r.json()["replies"][0]["body"].lower() or "Omar" in r.json()["replies"][0]["body"]

    r = client.post(
        f"/chats/{priv_s}/messages",
        headers=hs,
        json={"body": "team status", "speak": False},
    )
    assert "owner" in r.json()["replies"][0]["body"].lower()

    # Resolve issue
    r = client.post(
        f"/chats/{priv_o}/messages",
        headers=ho,
        json={"body": "show issues", "speak": False},
    )
    assert "#" in r.json()["replies"][0]["body"]
