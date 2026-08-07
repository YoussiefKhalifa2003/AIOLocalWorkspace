"""Owner-only metrics series for Live sparklines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "series.db"
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


def test_metrics_series_owner_only_and_empty(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    pid = info["project_a"]

    assert client.get(f"/projects/{pid}/metrics/series", headers=ho).status_code == 403

    data = client.get(f"/projects/{pid}/metrics/series", headers=ha).json()
    assert data["project_id"] == pid
    assert data["points"] == []
    assert data["buckets"] == {"tokens": [], "duration_ms": [], "success_rate": []}


def test_metrics_series_orders_ascending_and_limits(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]

    from app.db.models import AgentMetric
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        base = datetime.now(timezone.utc)
        for i in range(5):
            db.add(
                AgentMetric(
                    tenant_id=info["tenant_a"],
                    project_id=pid,
                    backend="llm",
                    model=f"m{i}",
                    success=i % 2 == 0,
                    duration_ms=100 * (i + 1),
                    tokens=10 * (i + 1),
                    user_id=info["user_a"],
                    created_at=base + timedelta(seconds=i),
                )
            )
        db.commit()
    finally:
        db.close()

    data = client.get(f"/projects/{pid}/metrics/series?limit=3", headers=ha).json()
    assert len(data["points"]) == 3
    assert data["buckets"]["tokens"] == [30, 40, 50]
    assert data["buckets"]["duration_ms"] == [300, 400, 500]
    assert data["buckets"]["success_rate"] == [1.0, 0.0, 1.0]
    # Chronological: earlier timestamps first
    times = [p["t"] for p in data["points"]]
    assert times == sorted(times)
