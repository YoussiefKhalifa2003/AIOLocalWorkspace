from app.db.session import SessionLocal, init_db
from app.services.isolation import IsolationError, artifacts_for_project, get_artifact
from app.services.seed import seed_demo_data
from app.db.models import Artifact


def test_cross_tenant_artifact_isolated(tmp_path, monkeypatch):
    db_path = tmp_path / "iso.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.config import get_settings

    get_settings.cache_clear()
    # rebind engine for this test DB
    import app.db.session as sess

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    art = Artifact(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        job_id=0,
        agent_type="research",
        title="secret",
        content="tenant A only",
    )
    # job_id FK - create minimal job/request or disable: use raw insert without FK for sqlite?
    # Better create proper request/job
    from app.db.models import Job, WorkRequest

    req = WorkRequest(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        user_id=info["user_a"],
        text="x",
        status="routed",
        pipeline_json='["research"]',
    )
    db.add(req)
    db.flush()
    job = Job(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        request_id=req.id,
        agent_type="research",
        status="done",
        payload_json="{}",
    )
    db.add(job)
    db.flush()
    art.job_id = job.id
    db.add(art)
    db.commit()

    # tenant B cannot see tenant A artifacts when scoped to B project
    found = artifacts_for_project(db, info["tenant_b"], info["project_b"])
    assert found == []

    try:
        get_artifact(db, info["tenant_b"], info["project_b"], art.id)
        assert False, "should raise"
    except IsolationError:
        pass

    # correct scope works
    got = get_artifact(db, info["tenant_a"], info["project_a"], art.id)
    assert got.content == "tenant A only"
    db.close()
