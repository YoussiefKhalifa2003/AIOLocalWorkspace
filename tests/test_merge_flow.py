"""Phase 2: confirm -> merge -> done. All GitHub calls are monkeypatched."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch, **env):
    db_path = tmp_path / "merge.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "dev-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", env.pop("GITHUB_TOKEN", "gh-test-token"))
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


def _make_in_review(client, headers, pid: int, title: str = "Ship the merge") -> int:
    oid = client.post(
        f"/projects/{pid}/objectives", headers=headers, json={"title": title}
    ).json()["id"]
    import app.db.session as sess
    from app.db.models import Objective

    db = sess.SessionLocal()
    o = db.query(Objective).filter(Objective.id == oid).one()
    o.status = "in_review"
    o.github_pr_url = "https://github.com/example/demo-project/pull/42"
    o.github_pr_number = 42
    o.github_branch = "aio/obj-x"
    db.commit()
    db.close()
    return oid


def _general(client, info, headers) -> list[str]:
    msgs = client.get(
        f"/chats/{info['chat_general']}/messages?after_id=0", headers=headers
    ).json()
    return [m["body"] for m in msgs]


def _signed(payload: dict, delivery: str, event: str = "pull_request"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
    }


def test_non_owner_cannot_merge(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    calls = []
    monkeypatch.setattr(
        "app.services.github_pr.merge_pull_request",
        lambda **kw: calls.append(kw) or {"ok": True},
    )
    r = client.post(
        f"/projects/{pid}/objectives/{oid}/merge", headers=ho, json={"confirm": True}
    )
    assert r.status_code == 403
    assert calls == []


def test_merge_requires_explicit_confirm(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    calls = []
    monkeypatch.setattr(
        "app.services.github_pr.merge_pull_request",
        lambda **kw: calls.append(kw) or {"ok": True},
    )
    r = client.post(f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={})
    assert r.status_code == 400
    assert "confirmation required" in r.json()["detail"]

    r = client.post(
        f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={"confirm": False}
    )
    assert r.status_code == 400
    assert calls == []


def test_merge_requires_in_review(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)
    client.patch(f"/projects/{pid}/objectives/{oid}", headers=ha, json={"status": "doing"})

    calls = []
    monkeypatch.setattr(
        "app.services.github_pr.merge_pull_request",
        lambda **kw: calls.append(kw) or {"ok": True},
    )
    r = client.post(
        f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={"confirm": True}
    )
    assert r.status_code == 400
    assert "in_review" in r.json()["detail"]
    assert calls == []


def test_merge_happy_path_moves_card_to_done(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    seen = {}

    def fake_merge(**kw):
        seen.update(kw)
        return {
            "ok": True,
            "merged": True,
            "sha": "deadbeef",
            "base": "main",
            "merge_method": "squash",
            "message": "merged",
        }

    monkeypatch.setattr("app.services.github_pr.merge_pull_request", fake_merge)
    monkeypatch.setattr(
        "app.services.github_pr.delete_remote_branch", lambda **kw: {"ok": True}
    )

    r = client.post(
        f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={"confirm": True}
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sha"] == "deadbeef"
    assert seen["pr_number"] == 42

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    card = next(c for col in board["columns"] for c in col["cards"] if c["id"] == oid)
    assert card["status"] == "done"
    assert card["done"] is True
    assert card["github_merged_at"]
    assert card["can_merge"] is False

    bodies = _general(client, info, ha)
    assert any(f"Merged PR #42 for objective #{oid}" in b for b in bodies)


def test_merge_failure_keeps_card_in_review(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    monkeypatch.setattr(
        "app.services.github_pr.merge_pull_request",
        lambda **kw: {
            "ok": False,
            "merged": False,
            "reason_code": "not_mergeable",
            "message": "PR #42 cannot be merged: the PR has merge conflicts.",
        },
    )
    r = client.post(
        f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={"confirm": True}
    )
    assert r.status_code == 409
    assert "merge conflicts" in r.json()["detail"]

    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    card = next(c for col in board["columns"] for c in col["cards"] if c["id"] == oid)
    assert card["status"] == "in_review"
    assert card["github_merged_at"] is None
    assert not any("Merged PR" in b for b in _general(client, info, ha))


def test_merge_helper_preflight_rejects_conflicts(monkeypatch):
    from app.db.models import Project
    from app.services import github_pr

    project = Project(id=1, tenant_id=1, name="p", github_repo="acme/widgets", github_token="tok")
    monkeypatch.setattr(
        github_pr,
        "pull_request_status",
        lambda **kw: {
            "ok": True,
            "state": "open",
            "merged": False,
            "mergeable": False,
            "mergeable_state": "dirty",
            "base": "main",
        },
    )
    out = github_pr.merge_pull_request(project=project, pr_number=7)
    assert out["ok"] is False
    assert out["reason_code"] == "not_mergeable"
    assert "conflicts" in out["message"]


def test_merge_helper_rejects_already_merged(monkeypatch):
    from app.db.models import Project
    from app.services import github_pr

    project = Project(id=1, tenant_id=1, name="p", github_repo="acme/widgets", github_token="tok")
    monkeypatch.setattr(
        github_pr,
        "pull_request_status",
        lambda **kw: {"ok": True, "state": "closed", "merged": True, "base": "main"},
    )
    out = github_pr.merge_pull_request(project=project, pr_number=7)
    assert out["ok"] is False
    assert out["reason_code"] == "already_merged"


def test_webhook_merged_notice_has_no_confirm_marker(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    payload = {
        "action": "closed",
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {
            "title": f"Ship it #obj-{oid}",
            "html_url": "https://github.com/example/demo-project/pull/42",
            "number": 42,
            "merged": True,
        },
    }
    body, headers = _signed(payload, "merge-hook-1")
    r = client.post("/webhooks/github", content=body, headers=headers)
    assert r.json()["status"] == "merge_notified"
    assert r.json()["deduped"] is False
    bodies = _general(client, info, ha)
    assert any("PR merged on GitHub" in b for b in bodies)
    assert not any("[[confirm:" in b for b in bodies)


def test_webhook_merged_is_silent_when_aio_merged_it(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    oid = _make_in_review(client, ha, pid)

    monkeypatch.setattr(
        "app.services.github_pr.merge_pull_request",
        lambda **kw: {"ok": True, "merged": True, "sha": "abc", "base": "main"},
    )
    monkeypatch.setattr(
        "app.services.github_pr.delete_remote_branch", lambda **kw: {"ok": True}
    )
    client.post(f"/projects/{pid}/objectives/{oid}/merge", headers=ha, json={"confirm": True})
    before = len(_general(client, info, ha))

    payload = {
        "action": "closed",
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {
            "title": f"Ship it #obj-{oid}",
            "html_url": "https://github.com/example/demo-project/pull/42",
            "number": 42,
            "merged": True,
        },
    }
    body, headers = _signed(payload, "merge-hook-2")
    r = client.post("/webhooks/github", content=body, headers=headers)
    assert r.json()["deduped"] is True
    after = _general(client, info, ha)
    assert len(after) == before
    assert not any("PR merged on GitHub" in b for b in after)
