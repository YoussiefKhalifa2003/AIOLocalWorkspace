"""Objective-met confirm should only attach when the request clearly targets a card."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.models import Objective, TaskItem, WorkRequest
from app.services.orchestrator import _candidate_objectives


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "confirm.db"
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

    return TestClient(app), info, sess.SessionLocal


def _objs(db, *, tenant_id, project_id, user_id, specs: list[dict]) -> list[Objective]:
    out = []
    for s in specs:
        o = Objective(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            title=s["title"],
            description=s.get("description"),
            status=s.get("status", "doing"),
            done=False,
            request_id=s.get("request_id"),
        )
        db.add(o)
        db.flush()
        for t in s.get("subtasks") or []:
            db.add(
                TaskItem(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    objective_id=o.id,
                    owner_user_id=user_id,
                    title=t,
                )
            )
        out.append(o)
    db.commit()
    for o in out:
        db.refresh(o)
    return out


def test_confirm_skips_unrelated(tmp_path, monkeypatch):
    _, info, SessionLocal = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        tid, pid, uid = info["tenant_a"], info["project_a"], info["user_a"]
        _objs(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            specs=[
                {"title": "Ship landing page hero", "status": "doing"},
                {"title": "Add dark mode toggle", "status": "doing"},
                {"title": "Unrelated backlog idea", "status": "todo"},
            ],
        )

        assert (
            _candidate_objectives(
                db,
                tenant_id=tid,
                project_id=pid,
                user_id=uid,
                request_text="/code fix the bug in the api",
            )
            == []
        )

        hit = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text="/code add dark mode toggle to settings",
        )
        assert len(hit) == 1
        assert "dark mode" in hit[0].title.lower()

        assert (
            _candidate_objectives(
                db,
                tenant_id=tid,
                project_id=pid,
                user_id=uid,
                request_text="EVIDENCE PACK\nObjective #1: Ship landing\nObjective #2: Dark",
            )
            == []
        )

        oid = hit[0].id
        by_id = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text=f"Implement objective #{oid}: whatever",
        )
        assert [o.id for o in by_id] == [oid]
    finally:
        db.close()


def test_confirm_freeform_matches_todo_cnn_and_research(tmp_path, monkeypatch):
    """Omar case: todo card + /code|/research with the same topic → ask confirm."""
    _, info, SessionLocal = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        tid, pid, uid = info["tenant_a"], info["project_a"], info["user_a"]
        objs = _objs(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            specs=[
                {"title": "build a CNN code", "status": "todo"},
                {"title": "objective research about clothing brands", "status": "todo"},
                {"title": "build a portfolio website", "status": "todo"},
            ],
        )

        cnn = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text="/code write me CNN skeleton code",
        )
        assert len(cnn) == 1
        assert cnn[0].id == objs[0].id

        brands = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text="/research Clothing Brands Research Gucci Chanel",
        )
        assert len(brands) == 1
        assert brands[0].id == objs[1].id

        # Unrelated freeform should not latch onto portfolio
        assert (
            _candidate_objectives(
                db,
                tenant_id=tid,
                project_id=pid,
                user_id=uid,
                request_text="/code print hello world",
            )
            == []
        )
    finally:
        db.close()


def test_confirm_ignores_private_context_objective_mentions(tmp_path, monkeypatch):
    """Omar bug: chat context 'Added objective #13' must not steal confirm from CNN ask."""
    _, info, SessionLocal = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        tid, pid, uid = info["tenant_a"], info["project_a"], info["user_a"]
        objs = _objs(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            specs=[
                {"title": "build a CNN code", "status": "todo"},
                {"title": "obj 20", "status": "doing", "request_id": None},
            ],
        )
        prompt = (
            "Private room context (recent):\n"
            "lead: Added objective #13: obj 20 (yours)\n"
            "you: !list\n\n"
            "Skill=/code. User ask:\n"
            "build me a cnn code"
        )
        # Even if a stale link points at the wrong card for this request id
        req = WorkRequest(
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            text=prompt,
            status="queued",
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        objs[1].request_id = req.id
        db.commit()

        hit = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text=prompt,
            request_id=req.id,
        )
        assert len(hit) == 1
        assert hit[0].id == objs[0].id
        assert "cnn" in hit[0].title.lower()
    finally:
        db.close()


def test_confirm_prefers_linked_request_when_ask_matches(tmp_path, monkeypatch):
    _, info, SessionLocal = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        tid, pid, uid = info["tenant_a"], info["project_a"], info["user_a"]
        req = WorkRequest(
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            text="placeholder",
            status="queued",
        )
        db.add(req)
        db.commit()
        db.refresh(req)

        objs = _objs(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            specs=[
                {
                    "title": "Linked card alpha",
                    "status": "doing",
                    "request_id": req.id,
                },
                {"title": "Other card beta", "status": "doing"},
            ],
        )
        hit = _candidate_objectives(
            db,
            tenant_id=tid,
            project_id=pid,
            user_id=uid,
            request_text="/code finish linked card alpha",
            request_id=req.id,
        )
        assert [o.id for o in hit] == [objs[0].id]

        # Stale link + unrelated ask → no confirm (don't trust request_id alone)
        assert (
            _candidate_objectives(
                db,
                tenant_id=tid,
                project_id=pid,
                user_id=uid,
                request_text="/code totally different words",
                request_id=req.id,
            )
            == []
        )
    finally:
        db.close()
