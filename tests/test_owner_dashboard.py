from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "dash.db"
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


def test_owner_dashboard_and_assign(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    pid = info["project_a"]

    denied = client.get(f"/projects/{pid}/analytics", headers=ho)
    assert denied.status_code == 403

    data = client.get(f"/projects/{pid}/analytics", headers=ha).json()
    assert "summary" in data
    assert data["summary"]["members"] >= 2
    assert isinstance(data["people"], list)
    assert isinstance(data["models"], list)
    emails = {p["email"] for p in data["people"]}
    assert info["email_a"] in emails
    assert info["email_omar"] in emails

    projects = client.get("/projects", headers=ha).json()
    assert len(projects) >= 2
    names = {p["name"] for p in projects}
    assert "demo-project" in names
    assert "ops" in names

    created = client.post("/projects", headers=ha, json={"name": "mobile"})
    assert created.status_code == 200
    assert created.json()["name"] == "mobile"
    assert client.post("/projects", headers=ho, json={"name": "nope"}).status_code == 403

    # Seed a metric row attributed to Omar
    from app.db.session import SessionLocal
    from app.db.models import AgentMetric, WorkRequest, Job

    db = SessionLocal()
    try:
        req = WorkRequest(
            tenant_id=info["tenant_a"],
            project_id=pid,
            user_id=info["user_omar"],
            text="dash test",
            status="routed",
        )
        db.add(req)
        db.flush()
        job = Job(
            tenant_id=info["tenant_a"],
            project_id=pid,
            request_id=req.id,
            agent_type="research",
            status="done",
            payload_json="{}",
            model_used="openrouter:test/model:free",
        )
        db.add(job)
        db.flush()
        db.add(
            AgentMetric(
                tenant_id=info["tenant_a"],
                project_id=pid,
                job_id=job.id,
                user_id=info["user_omar"],
                backend="openrouter",
                model="openrouter:test/model:free",
                success=True,
                tokens=1234,
            )
        )
        db.commit()
    finally:
        db.close()

    data = client.get(f"/projects/{pid}/analytics", headers=ha).json()
    assert data["summary"]["tokens_total"] >= 1234
    omar = next(p for p in data["people"] if p["email"] == info["email_omar"])
    assert omar["tokens"] >= 1234
    assert any("test/model" in m for m in omar["models"])

    assigned = client.post(
        f"/projects/{pid}/dashboard/assign",
        headers=ha,
        json={"title": "Ship dashboard polish", "assignee_user_id": info["user_omar"]},
    )
    assert assigned.status_code == 200
    body = assigned.json()
    assert body["assignee_email"] == info["email_omar"]
    assert "Ship dashboard polish" in body["title"]

    blocked = client.post(
        f"/projects/{pid}/dashboard/assign",
        headers=ho,
        json={"title": "nope", "assignee_user_id": info["user_a"]},
    )
    assert blocked.status_code == 403
