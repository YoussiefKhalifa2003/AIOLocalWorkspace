from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.agent_workspace import (
    commit_all,
    scrub_secrets,
    write_artifact_files,
)
from app.services.github_pr import artifact_files_for_objective


def test_artifact_files_maps_python_fence():
    files = artifact_files_for_objective(7, "Hello", "```python\nprint(1)\n```\n")
    assert "aio/objectives/obj-7/README.md" in files
    assert "aio/objectives/obj-7/main.py" in files
    assert "print(1)" in files["aio/objectives/obj-7/main.py"]


def test_write_artifact_files_writes_nested(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    r = write_artifact_files(
        root,
        {"aio/objectives/obj-1/main.py": "print('hi')\n", "aio/objectives/obj-1/README.md": "# x\n"},
    )
    assert r["ok"] is True
    assert (root / "aio/objectives/obj-1/main.py").read_text() == "print('hi')\n"


def test_write_artifact_files_rejects_escape(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    r = write_artifact_files(root, {"../evil.txt": "nope"})
    assert r["ok"] is False
    assert "rejected" in (r.get("error") or "").lower()


def test_scrub_secrets_hides_token():
    tok = "ghp_supersecrettoken"
    assert tok not in scrub_secrets(f"fatal: {tok} bad", tok)
    assert "***" in scrub_secrets("https://x-access-token:abc123@github.com/x/y.git", "abc123")


def test_commit_all_on_temp_repo(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_WORK_ROOT", str(tmp_path / "workspaces"))
    from app.config import get_settings

    get_settings.cache_clear()

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    written = write_artifact_files(repo, {"aio/objectives/obj-3/main.py": "x = 1\n"})
    assert written["ok"]
    r = commit_all(repo, message="[AIO #3] test", branch="aio/obj-3-test")
    assert r["ok"] is True
    assert r.get("empty") is False
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "AIO #3" in log.stdout

    # second commit with no changes
    r2 = commit_all(repo, message="noop", branch="aio/obj-3-test")
    assert r2["ok"] is True
    assert r2.get("empty") is True
