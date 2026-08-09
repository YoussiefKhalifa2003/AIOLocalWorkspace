"""Invite link signup must produce credentials that work in the CLI login."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "invite_login.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("INVITE_APP_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info


def test_web_form_signup_then_cli_login_works(tmp_path, monkeypatch):
    """The exact path new teammates take: join form -> Done -> aio sign-in."""
    client, info = _boot(tmp_path, monkeypatch)
    owner = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}

    link = client.post("/workspace/invite-link", headers=owner, params={"max_uses": 1})
    assert link.status_code == 200, link.text
    token = link.json()["token"]
    assert token
    assert client.get(f"/join/{token}").status_code == 200

    email = "teammate.join@example.com"
    password = "join-secret-99"
    name = "Teammate"

    # HTML form POST (browser Create account button)
    reg = client.post(
        f"/join/{token}/register",
        data={
            "name": name,
            "email": email,
            "password": password,
            "password2": password,
        },
    )
    assert reg.status_code == 200, reg.text
    assert "Done" in reg.text
    assert email in reg.text
    assert "aio" in reg.text.lower()
    assert "Server" in reg.text
    assert "http://127.0.0.1:8000" in reg.text
    assert "paste" in reg.text.lower() or "Paste" in reg.text

    # Link is single-use - second signup must fail
    dead = client.get(f"/join/{token}")
    assert dead.status_code == 400

    # Password login (what the terminal Sign in screen calls)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["email"] == email
    assert body["name"] == name
    assert body["api_key"]

    # API key from login opens the workspace (CLI after Sign in)
    headers = {"X-API-Key": body["api_key"], "X-User-Email": email}
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email
    assert me.json().get("is_owner") is False

    chats = client.get("/chats", headers=headers)
    assert chats.status_code == 200
    names = {c["name"] for c in chats.json()}
    assert "general" in names
    assert any(str(c.get("name") or "").lower().startswith("private") for c in chats.json())

    members = client.get("/workspace/members", headers=headers)
    assert members.status_code == 200
    emails = {m["email"] for m in members.json()}
    assert email in emails
    assert info["email_a"] in emails


def test_wrong_password_rejected_after_signup(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    owner = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    token = client.post("/workspace/invite-link", headers=owner).json()["token"]
    email = "wrong.pw@example.com"
    client.post(
        f"/join/{token}/register.json",
        json={"email": email, "password": "good-pass", "name": "WrongPw"},
    )
    bad = client.post("/auth/login", json={"email": email, "password": "nope"})
    assert bad.status_code == 401


def test_invite_public_base_url_https_tunnel_omits_default_port(monkeypatch):
    monkeypatch.setenv("INVITE_APP_URL", "https://demo.trycloudflare.com")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.workspace_invite import invite_public_base_url

    assert invite_public_base_url() == "https://demo.trycloudflare.com"


def test_invite_public_base_url_http_defaults_to_8000(monkeypatch):
    monkeypatch.setenv("INVITE_APP_URL", "http://10.205.70.120")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.workspace_invite import invite_public_base_url

    assert invite_public_base_url() == "http://10.205.70.120:8000"
