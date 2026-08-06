import hashlib
import hmac
import json

from fastapi.testclient import TestClient


def test_health():
    from app.main import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "wh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "dev-secret")
    # host .env must not decide which repo the seeded project maps to
    monkeypatch.setenv("GITHUB_REPO", "")
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
    seed_demo_data(db)
    db.close()

    from app.main import app

    client = TestClient(app)
    payload = {
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {"title": "t", "diff_text": "+x"},
        "diff_text": "+x",
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "delivery-1",
        "X-GitHub-Event": "pull_request",
    }
    r1 = client.post("/webhooks/github", content=body, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["status"] == "queued"
    r2 = client.post("/webhooks/github", content=body, headers=headers)
    assert r2.json()["status"] == "duplicate"
