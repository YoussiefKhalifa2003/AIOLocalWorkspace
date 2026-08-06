"""Phase 1: repo / PR / branch links on board cards, never fabricated."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch, **env):
    db_path = tmp_path / "links.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", env.pop("GITHUB_REPO", ""))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
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


def _set_repo(project_id: int, slug: str | None) -> None:
    import app.db.session as sess
    from app.db.models import Project

    db = sess.SessionLocal()
    p = db.query(Project).filter(Project.id == project_id).one()
    p.github_repo = slug
    db.commit()
    db.close()


def _link_pr(objective_id: int, *, url: str, number: int, branch: str) -> None:
    import app.db.session as sess
    from app.db.models import Objective

    db = sess.SessionLocal()
    o = db.query(Objective).filter(Objective.id == objective_id).one()
    o.github_pr_url = url
    o.github_pr_number = number
    o.github_branch = branch
    o.status = "in_review"
    o.done = False
    db.commit()
    db.close()


def _cards(board: dict) -> list[dict]:
    return [c for col in board["columns"] for c in col["cards"]]


def test_board_exposes_repo_url(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    _set_repo(pid, "acme/widgets")

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    assert board["github_repo"] == "acme/widgets"
    assert board["repo_url"] == "https://github.com/acme/widgets"
    assert all(c["repo_url"] == "https://github.com/acme/widgets" for c in _cards(board))


def test_in_review_card_has_pr_and_branch_links(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    _set_repo(pid, "acme/widgets")

    oid = client.post(
        f"/projects/{pid}/objectives", headers=ha, json={"title": "Ship links"}
    ).json()["id"]
    _link_pr(oid, url="https://github.com/acme/widgets/pull/12", number=12, branch="aio/obj-1")

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    card = next(c for c in _cards(board) if c["id"] == oid)
    assert card["pr_url"] == "https://github.com/acme/widgets/pull/12"
    assert card["pr_number"] == 12
    assert card["branch_url"] == "https://github.com/acme/widgets/tree/aio/obj-1"
    assert card["can_merge"] is True


def test_no_repo_configured_means_no_invented_urls(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    _set_repo(pid, None)

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    assert board["repo_url"] is None
    assert board["github_repo"] is None
    for c in _cards(board):
        assert c["repo_url"] is None
        assert c["branch_url"] is None
        assert c["can_merge"] is False


def test_can_merge_only_for_in_review_with_pr(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    _set_repo(pid, "acme/widgets")

    no_pr = client.post(
        f"/projects/{pid}/objectives", headers=ha, json={"title": "No PR yet"}
    ).json()["id"]
    client.patch(
        f"/projects/{pid}/objectives/{no_pr}", headers=ha, json={"status": "in_review"}
    )

    doing = client.post(
        f"/projects/{pid}/objectives", headers=ha, json={"title": "Has PR but doing"}
    ).json()["id"]
    _link_pr(doing, url="https://github.com/acme/widgets/pull/9", number=9, branch="b")
    client.patch(f"/projects/{pid}/objectives/{doing}", headers=ha, json={"status": "doing"})

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    cards = {c["id"]: c for c in _cards(board)}
    assert cards[no_pr]["can_merge"] is False
    assert cards[no_pr]["pr_url"] is None
    assert cards[doing]["can_merge"] is False
    assert cards[doing]["pr_url"] == "https://github.com/acme/widgets/pull/9"
