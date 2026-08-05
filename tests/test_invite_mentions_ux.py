from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "inv.db"
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


def test_invite_requires_accept_before_login(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]
    r = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!invite friend@gmail.com", "speak": False},
    )
    assert r.status_code == 200
    body = r.json()["replies"][0]["body"]
    assert "Invited friend@gmail.com" in body
    assert "Accept invite" in body
    assert "Invite failed" not in body

    # Cannot log in before accepting
    blocked = client.post(
        "/auth/login",
        json={"email": "friend@gmail.com", "api_key": "demo-key-a"},
    )
    assert blocked.status_code == 403
    assert "accept" in blocked.json()["detail"].lower()

    # Extract accept link from whisper (shown when SMTP missing)
    accept_url = None
    for line in body.splitlines():
        if "Accept link:" in line:
            accept_url = line.split("Accept link:", 1)[1].strip()
            break
    assert accept_url
    token = accept_url.rstrip("/").split("/")[-1]

    page = client.get(f"/invite/accept/{token}")
    assert page.status_code == 200
    assert "Invite accepted" in page.text or "already in" in page.text
    assert "friend@gmail.com" in page.text

    login = client.post(
        "/auth/login",
        json={"email": "friend@gmail.com", "api_key": "demo-key-a"},
    )
    assert login.status_code == 200
    assert login.json()["email"] == "friend@gmail.com"


def test_no_mention_in_private(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    priv = info["chat_private_a"]
    client.post(
        f"/chats/{priv}/messages",
        headers=ha,
        json={"body": "@Omar secret ping", "speak": False},
    )
    assert client.get("/workspace/mentions", headers=ho).json()["unread"] == 0

    general = info["chat_general"]
    client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "@Omar public ping", "speak": False},
    )
    data = client.get("/workspace/mentions", headers=ho).json()
    assert data["unread"] >= 1
    assert data["mentions"][0]["chat_name"] == "general"
    assert "public ping" in data["mentions"][0]["snippet"]
