from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "inv.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("INVITE_APP_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", "")
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
        json={"body": "!invite", "speak": False},
    )
    body = ok.json()["replies"][0]["body"]
    assert "Invite link (1 use)" in body
    assert "/join/" in body

    multi = client.post(
        f"/chats/{general}/messages",
        headers=ha,
        json={"body": "!invite 3", "speak": False},
    )
    mbody = multi.json()["replies"][0]["body"]
    assert "Invite link (3 uses)" in mbody
    assert "/join/" in mbody
    token_multi = mbody.strip().split("/join/")[-1].split()[0].strip()

    r1 = client.post(
        f"/join/{token_multi}/register.json",
        json={"email": "seat1@example.com", "password": "secret1", "name": "SeatOne"},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/join/{token_multi}/register.json",
        json={"email": "seat2@example.com", "password": "secret1", "name": "SeatTwo"},
    )
    assert r2.status_code == 200, r2.text
    r3 = client.post(
        f"/join/{token_multi}/register.json",
        json={"email": "seat3@example.com", "password": "secret1", "name": "SeatThree"},
    )
    assert r3.status_code == 200, r3.text
    r4 = client.post(
        f"/join/{token_multi}/register.json",
        json={"email": "seat4@example.com", "password": "secret1", "name": "SeatFour"},
    )
    assert r4.status_code == 400

    link = client.get("/workspace/invite-link", headers=ha)
    assert link.status_code == 200
    token = link.json()["token"]
    assert link.json().get("single_use") is True
    assert link.json().get("max_uses") == 1

    page = client.get(f"/join/{token}")
    assert page.status_code == 200
    assert "Join AIO" in page.text
    assert "CLI" in page.text or "aio login" in page.text

    html_reg = client.post(
        f"/join/{token}/register",
        data={
            "email": "formuser@gmail.com",
            "password": "secret1",
            "password2": "secret1",
            "name": "FormUser",
        },
    )
    assert html_reg.status_code == 200, html_reg.text
    assert "Done" in html_reg.text
    assert "Account created" in html_reg.text
    assert "aio login" in html_reg.text
    assert "formuser@gmail.com" in html_reg.text
    assert "localStorage" not in html_reg.text
    assert "location.replace" not in html_reg.text

    link_b = client.post("/workspace/invite-link?max_uses=1", headers=ha)
    token_b = link_b.json()["token"]
    reg = client.post(
        f"/join/{token_b}/register.json",
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

    # Mint a fresh multi-use link via API
    link2 = client.post("/workspace/invite-link?max_uses=2", headers=ha)
    assert link2.status_code == 200
    assert link2.json()["max_uses"] == 2
    assert link2.json().get("single_use") is False
    token2 = link2.json()["token"]
    assert token2 != token
    reg2 = client.post(
        f"/join/{token2}/register.json",
        json={"email": "second@example.com", "password": "secret2", "name": "Second"},
    )
    assert reg2.status_code == 200
    reg2b = client.post(
        f"/join/{token2}/register.json",
        json={"email": "third@example.com", "password": "secret2", "name": "Third"},
    )
    assert reg2b.status_code == 200

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
