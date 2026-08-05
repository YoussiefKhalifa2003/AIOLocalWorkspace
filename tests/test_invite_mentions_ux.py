from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "inv.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("INVITE_APP_URL", "http://127.0.0.1:8000")
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


def test_invite_link_register_and_login(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    general = info["chat_general"]

    ok = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!invitation", "speak": False},
    )
    body = ok.json()["replies"][0]["body"]
    assert "Single-use invite link" in body
    assert "/join/" in body

    link = client.get("/workspace/invite-link", headers=ha)
    assert link.status_code == 200
    token = link.json()["token"]
    assert link.json().get("single_use") is True

    page = client.get(f"/join/{token}")
    assert page.status_code == 200
    assert "Join AIO" in page.text

    reg = client.post(
        f"/join/{token}/register.json",
        json={"email": "newbie@example.com", "password": "secret1", "name": "Newbie"},
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["email"] == "newbie@example.com"

    # Same link cannot be reused
    again = client.post(
        f"/join/{token}/register.json",
        json={"email": "other@example.com", "password": "secret1", "name": "Other"},
    )
    assert again.status_code == 400
    dead = client.get(f"/join/{token}")
    assert dead.status_code == 400

    # Mint a fresh link for the next person
    link2 = client.post("/workspace/invite-link", headers=ha)
    assert link2.status_code == 200
    token2 = link2.json()["token"]
    assert token2 != token
    reg2 = client.post(
        f"/join/{token2}/register.json",
        json={"email": "second@example.com", "password": "secret2", "name": "Second"},
    )
    assert reg2.status_code == 200

    # Name required
    link3 = client.post("/workspace/invite-link", headers=ha)
    token3 = link3.json()["token"]
    no_name = client.post(
        f"/join/{token3}/register.json",
        json={"email": "noname@example.com", "password": "secret3", "name": ""},
    )
    assert no_name.status_code == 422 or no_name.status_code == 400

    spaced = client.post(
        f"/join/{token3}/register.json",
        json={"email": "spaced@example.com", "password": "secret3", "name": "Two Words"},
    )
    assert spaced.status_code == 400

    login = client.post(
        "/auth/login",
        json={"email": "newbie@example.com", "password": "secret1"},
    )
    assert login.status_code == 200

    demo = client.post(
        "/auth/login",
        json={"email": "a@local.test", "password": "demo"},
    )
    assert demo.status_code == 200


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
