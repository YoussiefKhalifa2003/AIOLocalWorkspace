"""Cross-platform transient API / SQLite lock release checks (Mac + Windows)."""

from __future__ import annotations

import threading

from app.cli_pkg.tui.client import ApiError, is_transient_api_error


def test_transient_api_error_matches_httpx_and_windows_phrasing():
    assert is_transient_api_error(ApiError("ReadTimeout: "))
    assert is_transient_api_error(ApiError("ConnectTimeout: timed out"))
    assert is_transient_api_error(ApiError("The read operation timed out"))
    assert is_transient_api_error(ApiError("WinError 10060: connection timed out"))
    assert is_transient_api_error(ApiError("database is locked"))
    assert not is_transient_api_error(ApiError("401 unauthorized"))
    assert not is_transient_api_error(ApiError("presence API missing"))


def test_execute_job_commits_before_long_handler(tmp_path, monkeypatch):
    """Presence/other threads must see job=running while LLM work is in flight."""
    db_path = tmp_path / "job_lock.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.db.models import Base, Job, Project, Tenant, WorkRequest

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = sess.SessionLocal()
    t = Tenant(name="t")
    db.add(t)
    db.flush()
    p = Project(tenant_id=t.id, name="p")
    db.add(p)
    db.flush()
    req = WorkRequest(tenant_id=t.id, project_id=p.id, user_id=1, text="x", status="queued")
    db.add(req)
    db.flush()
    job = Job(
        tenant_id=t.id,
        project_id=p.id,
        request_id=req.id,
        agent_type="ask",
        status="queued",
        payload_json="{}",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    saw_running = threading.Event()
    released = threading.Event()

    def slow_handler(db, job, llm):  # noqa: ARG001
        # Another connection should already see status=running (commit happened).
        other = sess.SessionLocal()
        try:
            row = other.get(Job, job_id)
            if row is not None and row.status == "running":
                saw_running.set()
        finally:
            other.close()
        released.wait(timeout=2.0)

    monkeypatch.setitem(__import__("app.agents.runner", fromlist=["AGENTS"]).AGENTS, "ask", slow_handler)

    from app.agents.runner import execute_job

    def run():
        d = sess.SessionLocal()
        try:
            j = d.get(Job, job_id)
            execute_job(d, j, llm=None)
            d.commit()
        finally:
            d.close()

    th = threading.Thread(target=run)
    th.start()
    assert saw_running.wait(timeout=3.0), "job should be committed as running before handler blocks"
    released.set()
    th.join(timeout=3.0)
    assert not th.is_alive()

    final = sess.SessionLocal()
    try:
        assert final.get(Job, job_id).status == "done"
    finally:
        final.close()
