from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.models import Project
from app.services.github_pr import resolve_github_token

logger = logging.getLogger(__name__)


def workspace_path(objective_id: int) -> Path:
    root = Path(get_settings().agent_work_root)
    return root / f"obj-{objective_id}"


def scrub_secrets(text: str, token: str = "") -> str:
    out = text or ""
    if token:
        out = out.replace(token, "***")
    out = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", out)
    out = re.sub(r"://[^:@/\s]+:[^@/\s]+@", "://***:***@", out)
    return out


def _timeout() -> int:
    return int(get_settings().agent_git_timeout_seconds or 120)


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    token: str = "",
) -> dict[str, Any]:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else _timeout(),
            check=False,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": 127,
            "stdout": "",
            "stderr": "git not found on PATH",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": scrub_secrets(exc.stdout or "", token) if isinstance(exc.stdout, str) else "",
            "stderr": scrub_secrets(str(exc), token),
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": scrub_secrets(proc.stdout or "", token),
        "stderr": scrub_secrets(proc.stderr or "", token),
    }


def _clone_url(repo: str, token: str) -> str:
    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    if token:
        return f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    return f"https://github.com/{owner}/{name}.git"


def _remote_url(repo: str, token: str) -> str:
    return _clone_url(repo, token)


def prepare_workspace(
    project: Project,
    objective_id: int,
    branch: str,
) -> dict[str, Any]:
    """Clone (or refresh) the project repo into data/workspaces/obj-N and create branch."""
    token = resolve_github_token(project)
    repo = (project.github_repo or get_settings().github_repo or "").strip()
    path = workspace_path(objective_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not token or not repo:
        return {
            "ok": False,
            "path": str(path),
            "branch": branch,
            "default_branch": "main",
            "error": "No GITHUB_TOKEN / github_repo configured for local workspace",
        }

    git_dir = path / ".git"
    if path.exists() and not git_dir.exists():
        shutil.rmtree(path, ignore_errors=True)

    if not git_dir.exists():
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        clone = _run_git(
            ["clone", "--depth", "1", _clone_url(repo, token), str(path)],
            token=token,
        )
        if not clone["ok"]:
            return {
                "ok": False,
                "path": str(path),
                "branch": branch,
                "default_branch": "main",
                "error": f"git clone failed: {clone['stderr'] or clone['stdout']}",
            }
    else:
        # Refresh existing checkout
        _run_git(
            ["remote", "set-url", "origin", _remote_url(repo, token)],
            cwd=path,
            token=token,
        )
        fetch = _run_git(["fetch", "origin"], cwd=path, token=token)
        if not fetch["ok"]:
            shutil.rmtree(path, ignore_errors=True)
            clone = _run_git(
                ["clone", "--depth", "1", _clone_url(repo, token), str(path)],
                token=token,
            )
            if not clone["ok"]:
                return {
                    "ok": False,
                    "path": str(path),
                    "branch": branch,
                    "default_branch": "main",
                    "error": f"git reclone failed: {clone['stderr'] or clone['stdout']}",
                }

    default_branch = "main"
    sym = _run_git(
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=path,
        token=token,
    )
    if sym["ok"] and sym["stdout"].strip():
        # refs/remotes/origin/main
        ref = sym["stdout"].strip()
        if "/" in ref:
            default_branch = ref.rsplit("/", 1)[-1]

    # Reset to default remote tip, then create/switch feature branch
    _run_git(["checkout", "-f", default_branch], cwd=path, token=token)
    _run_git(["reset", "--hard", f"origin/{default_branch}"], cwd=path, token=token)
    # Drop local branch if it already exists from a prior run
    _run_git(["branch", "-D", branch], cwd=path, token=token)
    co = _run_git(["checkout", "-b", branch], cwd=path, token=token)
    if not co["ok"]:
        # Maybe already on branch
        co2 = _run_git(["checkout", "-B", branch], cwd=path, token=token)
        if not co2["ok"]:
            return {
                "ok": False,
                "path": str(path),
                "branch": branch,
                "default_branch": default_branch,
                "error": f"git checkout branch failed: {co2['stderr'] or co['stderr']}",
            }

    return {
        "ok": True,
        "path": str(path),
        "branch": branch,
        "default_branch": default_branch,
    }


def write_artifact_files(root: Path | str, files: dict[str, str]) -> dict[str, Any]:
    """Write relative paths under root; reject path escapes."""
    base = Path(root).resolve()
    written: list[str] = []
    for rel, content in (files or {}).items():
        rel_norm = (rel or "").replace("\\", "/").lstrip("/")
        if not rel_norm or ".." in rel_norm.split("/"):
            return {
                "ok": False,
                "written": written,
                "error": f"rejected unsafe path: {rel!r}",
            }
        dest = (base / rel_norm).resolve()
        try:
            dest.relative_to(base)
        except ValueError:
            return {
                "ok": False,
                "written": written,
                "error": f"rejected path escape: {rel!r}",
            }
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = content if content.endswith("\n") else content + "\n"
        dest.write_text(body, encoding="utf-8")
        written.append(rel_norm)
    return {"ok": True, "written": written}


def is_workspace_ready(path: Path | str) -> bool:
    root = Path(path)
    return root.exists() and (root / ".git").exists()


def changed_files(path: Path | str) -> list[str]:
    """Files an agent touched: working tree changes plus commits ahead of HEAD."""
    root = Path(path)
    out: list[str] = []
    status = _run_git(["status", "--porcelain"], cwd=root)
    for line in (status.get("stdout") or "").splitlines():
        entry = line[3:].strip() if len(line) > 3 else ""
        if " -> " in entry:  # renames
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip('"')
        if entry:
            out.append(entry)
    diff = _run_git(["diff", "--name-only", "HEAD"], cwd=root)
    for line in (diff.get("stdout") or "").splitlines():
        entry = line.strip()
        if entry:
            out.append(entry)
    return sorted(dict.fromkeys(out))


def commit_all(path: Path | str, message: str, branch: str) -> dict[str, Any]:
    root = Path(path)
    _run_git(["config", "user.email", "aio@local"], cwd=root)
    _run_git(["config", "user.name", "AIO Bot"], cwd=root)
    add = _run_git(["add", "-A"], cwd=root)
    if not add["ok"]:
        return {"ok": False, "error": add["stderr"] or "git add failed", "branch": branch}

    status = _run_git(["status", "--porcelain"], cwd=root)
    if not (status.get("stdout") or "").strip():
        return {"ok": True, "empty": True, "branch": branch, "message": "nothing to commit"}

    commit = _run_git(["commit", "-m", message], cwd=root)
    if not commit["ok"]:
        return {"ok": False, "error": commit["stderr"] or "git commit failed", "branch": branch}
    return {"ok": True, "empty": False, "branch": branch}


def push_branch(
    path: Path | str,
    branch: str,
    *,
    token: str,
    repo: str,
) -> dict[str, Any]:
    root = Path(path)
    _run_git(
        ["remote", "set-url", "origin", _remote_url(repo, token)],
        cwd=root,
        token=token,
    )
    push = _run_git(["push", "-u", "origin", branch], cwd=root, token=token)
    if not push["ok"]:
        return {
            "ok": False,
            "branch": branch,
            "error": push["stderr"] or push["stdout"] or "git push failed",
        }
    return {"ok": True, "branch": branch}
