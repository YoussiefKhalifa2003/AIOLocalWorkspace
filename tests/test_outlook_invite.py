"""Invite domain lock + Outlook email path (Playwright mocked)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch, *, domain: str = "tatweermea.com"):
    db_path = tmp_path / "invite_mail.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", domain)
    monkeypatch.setenv("OUTLOOK_INVITE_ENABLED", "true")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")
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


def test_assert_domain_allows_only_tatweer(monkeypatch):
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", "tatweermea.com")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.invite_domain import assert_allowed_invite_email, is_allowed_invite_email

    assert is_allowed_invite_email("omar@tatweermea.com")
    assert not is_allowed_invite_email("omar@gmail.com")
    assert assert_allowed_invite_email("Omar@TatweerMEA.com") == "omar@tatweermea.com"
    try:
        assert_allowed_invite_email("x@gmail.com")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "tatweermea.com" in str(exc)


def test_invite_email_rejects_foreign_domain(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    r = client.post(
        "/workspace/invite-email",
        headers=ha,
        json={"email": "friend@gmail.com", "max_uses": 1},
    )
    assert r.status_code == 400
    assert "tatweermea.com" in r.json()["detail"]


def test_invite_email_sends_when_outlook_ok(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}

    def fake_send(**kwargs):
        return {"ok": True, "skipped": False, "to": str(kwargs.get("to_email") or "").lower()}

    monkeypatch.setattr(
        "app.services.outlook_invite.send_invite_via_outlook",
        fake_send,
    )

    r = client.post(
        "/workspace/invite-email",
        headers=ha,
        json={"email": "colleague@tatweermea.com", "max_uses": 2},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["invite_url"]
    assert data["max_uses"] == 2
    assert data["outlook"]["ok"] is True
    assert data["emailed_to"] == "colleague@tatweermea.com"


def test_register_rejects_non_domain(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    link = client.post("/workspace/invite-link?max_uses=1", headers=ha).json()
    token = link["token"]
    bad = client.post(
        f"/join/{token}/register.json",
        json={"email": "outsider@gmail.com", "password": "demo", "name": "Outsider"},
    )
    assert bad.status_code == 400
    assert "tatweermea.com" in bad.json()["detail"]
