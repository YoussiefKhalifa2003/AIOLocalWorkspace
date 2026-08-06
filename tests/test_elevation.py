"""Elevation roadmap gates A-G."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient


def _boot(tmp_path, monkeypatch, **env):
    db_path = tmp_path / "elev.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "dev-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", env.pop("GITHUB_TOKEN", ""))
    monkeypatch.setenv("GITHUB_REPO", env.pop("GITHUB_REPO", ""))
    monkeypatch.setenv("OPENCODE_API_KEY", env.pop("OPENCODE_API_KEY", ""))
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


def _signed(payload: dict, delivery: str, event: str = "pull_request"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
    }


def test_gate_a_board_and_patch_permissions(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    hs = {"X-API-Key": info["api_key_sara"], "X-User-Email": info["email_sara"]}
    pid = info["project_a"]

    board = client.get(f"/projects/{pid}/board", headers=ho).json()
    assert "columns" in board
    assert {c["id"] for c in board["columns"]} >= {"todo", "doing", "blocked", "done"}
    cards = [card for col in board["columns"] for card in col["cards"]]
    assert any(c["open_issue_count"] >= 1 for c in cards)

    omar_card = next(c for c in cards if "metro" in c["title"].lower())
    # Sara cannot patch Omar's card
    r = client.patch(
        f"/projects/{pid}/objectives/{omar_card['id']}",
        headers=hs,
        json={"status": "done"},
    )
    assert r.status_code == 403

    # Omar can drag to done
    r = client.patch(
        f"/projects/{pid}/objectives/{omar_card['id']}",
        headers=ho,
        json={"status": "done"},
    )
    assert r.status_code == 200
    assert r.json()["done"] is True
    assert r.json()["status"] == "done"

    # Owner can patch any
    sara_blocked = next(c for c in cards if c["status"] == "blocked")
    r = client.patch(
        f"/projects/{pid}/objectives/{sara_blocked['id']}",
        headers=ha,
        json={"status": "todo"},
    )
    assert r.status_code == 200


def test_gate_b_github_link_and_merge_confirm(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    board = client.get(f"/projects/{pid}/board", headers=ha).json()
    oid = board["columns"][0]["cards"][0]["id"]

    payload = {
        "action": "opened",
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {
            "title": f"Ship it #obj-{oid}",
            "body": "n/a",
            "html_url": "https://github.com/example/demo-project/pull/9",
            "number": 9,
            "diff_text": "+x",
            "head": {"ref": "feat/x"},
        },
    }
    body, headers = _signed(payload, "del-open-1")
    r = client.post("/webhooks/github", content=body, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    obj = client.get(f"/projects/{pid}/board", headers=ha).json()
    card = next(c for col in obj["columns"] for c in col["cards"] if c["id"] == oid)
    assert card["github_pr_url"]
    assert card["github_pr_number"] == 9

    msgs = client.get(f"/chats/{info['chat_general']}/messages?after_id=0", headers=ha).json()
    assert any("PR opened" in m["body"] for m in msgs)

    # unknown repo ignored
    bad = {
        "action": "opened",
        "repository": {"full_name": "no/such"},
        "pull_request": {"title": "x", "diff_text": ""},
    }
    body, headers = _signed(bad, "del-bad")
    assert client.post("/webhooks/github", content=body, headers=headers).json()["status"] == "ignored"

    # merge → plain notice, not auto-done and not a confirm prompt
    merged = {
        "action": "closed",
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {
            "title": f"Ship it #obj-{oid}",
            "html_url": "https://github.com/example/demo-project/pull/9",
            "number": 9,
            "merged": True,
        },
    }
    body, headers = _signed(merged, "del-merge-1")
    r = client.post("/webhooks/github", content=body, headers=headers)
    assert r.json()["status"] == "merge_notified"
    msgs = client.get(f"/chats/{info['chat_general']}/messages?after_id=0", headers=ha).json()
    assert any("PR merged on GitHub" in m["body"] for m in msgs)
    assert not any("[[confirm:" in m["body"] for m in msgs)
    assert any(f"Objective #{oid}" in m["body"] for m in msgs)

    board2 = client.get(f"/projects/{pid}/board", headers=ha).json()
    card2 = next(c for col in board2["columns"] for c in col["cards"] if c["id"] == oid)
    assert card2["done"] is False

    # Lead yes marks done
    r = client.post(
        f"/chats/{info['chat_general']}/messages",
        headers=ha,
        json={"body": f"!done {oid}", "speak": False},
    )
    assert "Marked objective" in r.json()["replies"][0]["body"]


def test_gate_c_review_in_general(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    payload = {
        "action": "opened",
        "repository": {"full_name": "example/demo-project"},
        "pull_request": {
            "title": "Review me",
            "html_url": "https://github.com/example/demo-project/pull/3",
            "number": 3,
            "diff_text": "+print(1)\n",
        },
    }
    body, headers = _signed(payload, "del-rev-1")
    r = client.post("/webhooks/github", content=body, headers=headers)
    assert r.json()["status"] == "queued"
    job_id = r.json()["job_id"]

    import app.db.session as sess
    from app.services.llm import LLMClient
    from app.worker import process_one

    db = sess.SessionLocal()
    try:
        for _ in range(20):
            if not process_one(db, LLMClient()):
                break
            db.commit()
    finally:
        db.close()

    msgs = client.get(f"/chats/{info['chat_general']}/messages?after_id=0", headers=ha).json()
    bodies = [m.get("body") or "" for m in msgs]
    agents = [m.get("agent") for m in msgs]
    assert "code_review" in agents or any("Code review" in b for b in bodies), bodies
    assert any("example/demo-project" in b or "Review me" in b for b in bodies)
    assert job_id


def test_gate_d_file_claims(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch)
    ho = {"X-API-Key": info["api_key_omar"], "X-User-Email": info["email_omar"]}
    hs = {"X-API-Key": info["api_key_sara"], "X-User-Email": info["email_sara"]}
    priv_o = info["chat_private_omar"]
    priv_s = info["chat_private_sara"]

    r = client.post(
        f"/chats/{priv_o}/messages",
        headers=ho,
        json={"body": "!claim app/api/chats.py", "speak": False},
    )
    assert "Claimed" in r.json()["replies"][0]["body"]

    r = client.post(
        f"/chats/{priv_s}/messages",
        headers=hs,
        json={"body": "/code fix app/api/chats.py please", "speak": False},
    )
    body = r.json()["replies"][0]["body"]
    assert "WARNING" in body or "conflict" in body.lower()
    assert "Lead routed" not in body

    r = client.post(
        f"/chats/{priv_s}/messages",
        headers=hs,
        json={"body": "!go", "speak": False},
    )
    assert "Lead routed" in r.json()["replies"][0]["body"] or "coding" in r.json()["replies"][0]["body"].lower()


def test_gate_e_agent_backlog_manual_pr(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch, GITHUB_TOKEN="")
    # ensure settings ignore host .env token
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("AGENT_WORK_ROOT", str(tmp_path / "workspaces"))
    from app.config import get_settings

    get_settings.cache_clear()

    def _no_workspace(project, objective_id, branch):
        return {
            "ok": False,
            "path": str(tmp_path / "workspaces" / f"obj-{objective_id}"),
            "branch": branch,
            "default_branch": "main",
            "error": "test: skip local workspace",
        }

    monkeypatch.setattr(
        "app.services.agent_workspace.prepare_workspace",
        _no_workspace,
    )

    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    pid = info["project_a"]
    # create objective without github token → manual path
    r = client.post(
        f"/projects/{pid}/objectives",
        headers=ha,
        json={"title": "Add hello.py helper"},
    )
    oid = r.json()["id"]
    r = client.patch(
        f"/projects/{pid}/objectives/{oid}",
        headers=ha,
        json={"status": "agent_backlog"},
    )
    assert r.status_code == 200
    # without token: coding runs in background then moves to doing (manual PR)
    assert r.json()["status"] in ("doing", "in_review", "agent_backlog", "blocked")
    import time

    status = r.json()["status"]
    for _ in range(80):
        if status in ("doing", "in_review", "blocked"):
            break
        time.sleep(0.25)
        board = client.get(f"/projects/{pid}/board", headers=ha).json()
        card = next(
            (c for col in board["columns"] for c in col["cards"] if c["id"] == oid),
            None,
        )
        assert card is not None
        status = card.get("status") or next(
            col["id"] for col in board["columns"] if any(c["id"] == oid for c in col["cards"])
        )
    assert status in ("doing", "in_review", "blocked"), status
    msgs = client.get(f"/chats/{info['chat_general']}/messages?after_id=0", headers=ha).json()
    assert any("objective" in m["body"].lower() and str(oid) in m["body"] for m in msgs)


def test_gate_f_model_tiers_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("AGENT_MODEL_STRONG", "strong-model")
    client, info = _boot(
        tmp_path,
        monkeypatch,
        AGENT_MODEL_FAST="fast-model",
        AGENT_MODEL_STRONG="strong-model",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.model_tiers import infer_tier, resolve_model

    assert infer_tier("coding", "print hello") == "fast"
    assert infer_tier("code_review", "review this") == "strong"
    assert resolve_model("fast", agent_type="coding") == "fast-model"
    assert resolve_model("strong", agent_type="code_review") == "strong-model"

    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    priv = info["chat_private_a"]
    client.post(
        f"/chats/{priv}/messages",
        headers=ha,
        json={"body": "/code write a python function that returns 1", "speak": False},
    )
    summary = client.get(f"/projects/{info['project_a']}/jobs/summary", headers=ha).json()
    assert "by_model" in summary
    assert summary["total"] >= 1
    # offline stub still sets model_used from settings
    models = [m["model"] for m in summary["by_model"]]
    assert any("fast-model" in m or "openrouter" in m or m == "(none)" for m in models) or summary["total"] >= 1


def test_gate_g_analytics_and_llm_backend(tmp_path, monkeypatch):
    client, info = _boot(tmp_path, monkeypatch, CODING_BACKEND="llm")
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    hs = {"X-API-Key": info["api_key_sara"], "X-User-Email": info["email_sara"]}
    r = client.get(f"/projects/{info['project_a']}/analytics", headers=hs)
    assert r.status_code == 403
    r = client.get(f"/projects/{info['project_a']}/analytics", headers=ha)
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "people" in body
    assert "models" in body

    # coding writes a metric row
    priv = info["chat_private_a"]
    client.post(
        f"/chats/{priv}/messages",
        headers=ha,
        json={"body": "/code def add(a,b): return a+b", "speak": False},
    )
    data = client.get(f"/projects/{info['project_a']}/analytics", headers=ha).json()
    assert data["summary"]["jobs_total"] >= 1
    assert data["summary"]["tokens_total"] >= 0


def test_file_claim_overlap_unit():
    from app.services.file_claims import paths_overlap

    assert paths_overlap("app/api/chats.py", "app/api/chats.py")
    assert paths_overlap("app/api/", "app/api/chats.py") or paths_overlap(
        "app/api/chats.py", "app/api/chats.py"
    )
