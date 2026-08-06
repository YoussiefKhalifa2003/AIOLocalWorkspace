"""Phase 5: Codex / Claude Code runners and workspace-aware agent backlog."""

from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest

from app.services.coding_backend import (
    ClaudeCodeBackend,
    CodexBackend,
    LlmCodingBackend,
    OpenCodeBackend,
    get_coding_backend,
    get_coding_backend_for,
)
from app.services.llm import LLMError


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_run(recorder, *, stdout="", stderr="", returncode=0, writes=None):
    def run(argv, **kwargs):
        recorder.append({"argv": argv, "kwargs": kwargs})
        out_path = None
        if "-o" in argv:
            out_path = Path(argv[argv.index("-o") + 1])
        if writes and out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(writes, encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return run


def test_codex_builds_expected_argv_and_cwd(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr(
        subprocess, "run", _fake_run(calls, writes="wrote hello.py", stdout="noise")
    )
    ws = tmp_path / "obj-1"
    ws.mkdir()

    result = CodexBackend().run(
        prompt="add a helper", model="gemini-x", llm=None, workspace=str(ws)
    )

    argv = calls[0]["argv"]
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--ask-for-approval") + 1] == "never"
    assert "--skip-git-repo-check" in argv
    assert argv[-1] == "add a helper"
    assert calls[0]["kwargs"]["cwd"] == str(ws)
    assert result.backend == "codex"
    assert result.workspace_used is True
    assert result.content == "wrote hello.py"


def test_codex_auth_goes_through_env_not_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_API_KEY", "sk-secret-value")
    from app.config import get_settings

    get_settings.cache_clear()
    calls: list[dict] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, writes="ok"))

    CodexBackend().run(prompt="x", model="m", llm=None, workspace=str(tmp_path))

    assert all("sk-secret-value" not in str(a) for a in calls[0]["argv"])
    assert calls[0]["kwargs"]["env"]["CODEX_API_KEY"] == "sk-secret-value"
    assert calls[0]["kwargs"]["env"]["OPENAI_API_KEY"] == "sk-secret-value"


def test_claude_builds_argv_and_parses_json_result(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(calls, stdout=json.dumps({"result": "edited two files", "cost": 1})),
    )

    result = ClaudeCodeBackend().run(
        prompt="fix the bug", model="m", llm=None, workspace=str(tmp_path)
    )

    argv = calls[0]["argv"]
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[2] == "fix the bug"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert calls[0]["kwargs"]["cwd"] == str(tmp_path)
    assert result.content == "edited two files"
    assert result.backend == "claude_code"


def test_claude_falls_back_to_raw_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _fake_run([], stdout="plain text answer"))
    out = ClaudeCodeBackend().run(prompt="x", model="m", llm=None, workspace=str(tmp_path))
    assert out.content == "plain text answer"


def test_missing_binary_raises_readable_error(monkeypatch, tmp_path):
    def boom(*a, **kw):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(LLMError) as exc:
        CodexBackend().run(prompt="x", model="m", llm=None, workspace=str(tmp_path))
    assert "codex CLI not found" in str(exc.value)

    with pytest.raises(LLMError) as exc2:
        ClaudeCodeBackend().run(prompt="x", model="m", llm=None, workspace=str(tmp_path))
    assert "claude CLI not found" in str(exc2.value)


def test_output_is_scrubbed_of_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run([], writes="pushed with ghp_supersecret to https://x:y@github.com/a/b"),
    )
    out = CodexBackend().run(prompt="x", model="m", llm=None, workspace=str(tmp_path))
    assert "ghp_supersecret" not in out.content
    assert "***" in out.content


@pytest.mark.parametrize(
    "name,cls",
    [
        ("llm", LlmCodingBackend),
        ("opencode", OpenCodeBackend),
        ("codex", CodexBackend),
        ("claude_code", ClaudeCodeBackend),
        ("nonsense", LlmCodingBackend),
        ("", LlmCodingBackend),
    ],
)
def test_backend_selection(monkeypatch, name, cls):
    assert isinstance(get_coding_backend_for(name), cls)
    monkeypatch.setenv("CODING_BACKEND", name)
    from app.config import get_settings

    get_settings.cache_clear()
    assert isinstance(get_coding_backend(), cls)


def test_changed_files_lists_agent_edits(tmp_path):
    from app.services.agent_workspace import changed_files, is_workspace_ready

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    assert is_workspace_ready(repo) is True
    assert changed_files(repo) == []

    (repo / "app.py").write_text("print(2)\n", encoding="utf-8")
    (repo / "new_helper.py").write_text("x = 1\n", encoding="utf-8")
    found = changed_files(repo)
    assert "app.py" in found
    assert "new_helper.py" in found


def test_workspace_edits_survive_into_the_pr(tmp_path, monkeypatch):
    """A workspace-capable agent's real edits must not be overwritten."""
    from app.services import agent_backlog as backlog

    repo = tmp_path / "obj-ws"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "existing.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    db_path = tmp_path / "runners.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()

    import app.db.session as sess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, Objective

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.services.seed import seed_demo_data

    db = sess.SessionLocal()
    info = seed_demo_data(db)
    obj = Objective(
        tenant_id=info["tenant_a"],
        project_id=info["project_a"],
        user_id=info["user_a"],
        assignee_user_id=info["user_a"],
        title="Agent edits real files",
        status="agent_backlog",
        done=False,
        sort_order=1,
    )
    db.add(obj)
    db.commit()
    oid = obj.id

    monkeypatch.setattr(
        backlog,
        "drain_queue",
        lambda **kw: (repo / "existing.py").write_text("new from agent\n", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "app.services.agent_workspace.prepare_workspace",
        lambda project, objective_id, branch: {
            "ok": True,
            "path": str(repo),
            "branch": branch,
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        "app.services.agent_workspace.push_branch", lambda *a, **kw: {"ok": True}
    )
    opened: dict = {}

    def fake_open_pr(**kw):
        opened.update(kw)
        return {
            "ok": True,
            "pr_url": "https://github.com/x/y/pull/1",
            "pr_number": 1,
            "branch": kw["branch"],
            "files": kw.get("files") or [],
        }

    monkeypatch.setattr("app.services.github_pr.open_pr_for_branch", fake_open_pr)

    backlog.finish_agent_backlog(db, objective_id=oid, job_ids=[])
    db.commit()

    assert (repo / "existing.py").read_text() == "new from agent\n"
    assert not (repo / "aio").exists(), "generated folder must not overwrite agent edits"
    assert opened["files"] == ["existing.py"]

    refreshed = db.query(Objective).filter(Objective.id == oid).one()
    assert refreshed.status == "in_review"
    assert refreshed.github_pr_number == 1
    db.close()
