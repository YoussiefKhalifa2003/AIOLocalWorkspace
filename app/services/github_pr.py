from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.config import get_settings
from app.db.models import Project

_EXT = {
    "python": "py",
    "py": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "tsx": "tsx",
    "jsx": "jsx",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yml",
    "yml": "yml",
    "markdown": "md",
    "md": "md",
    "go": "go",
    "rust": "rs",
    "sql": "sql",
}


def resolve_github_token(project: Project) -> str:
    settings = get_settings()
    return (project.github_token or settings.github_token or "").strip()


def create_pr_from_artifact(
    *,
    project: Project,
    objective_id: int,
    title: str,
    body: str,
    content: str | None = None,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Commit generated files under aio/objectives/obj-N/ and open a GitHub PR.

    Falls back to a manual message when token/repo is missing or the API fails.
    """
    token = resolve_github_token(project)
    repo = _repo_slug(project)
    branch = branch_name or f"aio/obj-{objective_id}-{_slug(title)}-{int(time.time()) % 100000}"
    artifact = (content if content is not None else body) or ""
    if not token or not repo:
        return {
            "ok": False,
            "manual": True,
            "branch": branch,
            "message": (
                "No GITHUB_TOKEN / github_repo configured. Create a PR manually.\n\n"
                f"Suggested branch: `{branch}`\n\n{body}"
            ),
        }

    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    files = artifact_files_for_objective(objective_id, title, artifact)
    pr_body = (
        f"AIO generated for objective #{objective_id}: {title}\n\n"
        f"#obj-{objective_id}\n\n"
        f"Files written under `aio/objectives/obj-{objective_id}/`.\n"
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            repo_r = client.get(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
            if repo_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": (
                        f"GitHub repo lookup failed ({repo_r.status_code}). Create PR manually.\n\n{body}"
                    ),
                }
            default_branch = (repo_r.json() or {}).get("default_branch") or "main"
            ref_r = client.get(
                f"https://api.github.com/repos/{owner}/{name}/git/ref/heads/{default_branch}",
                headers=headers,
            )
            if ref_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": f"Could not read default branch. Create PR manually.\n\n{body}",
                }
            base_sha = ref_r.json()["object"]["sha"]
            commit_r = client.get(
                f"https://api.github.com/repos/{owner}/{name}/git/commits/{base_sha}",
                headers=headers,
            )
            if commit_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": f"Could not read base commit. Create PR manually.\n\n{body}",
                }
            base_tree = commit_r.json()["tree"]["sha"]

            tree_items: list[dict[str, str]] = []
            for path, file_body in files.items():
                blob_r = client.post(
                    f"https://api.github.com/repos/{owner}/{name}/git/blobs",
                    headers=headers,
                    json={"content": file_body, "encoding": "utf-8"},
                )
                if blob_r.status_code >= 400:
                    return {
                        "ok": False,
                        "manual": True,
                        "branch": branch,
                        "message": (
                            f"Blob create failed ({blob_r.status_code}): {blob_r.text[:300]}\n\n{body}"
                        ),
                    }
                tree_items.append(
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_r.json()["sha"],
                    }
                )

            tree_r = client.post(
                f"https://api.github.com/repos/{owner}/{name}/git/trees",
                headers=headers,
                json={"base_tree": base_tree, "tree": tree_items},
            )
            if tree_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": (
                        f"Tree create failed ({tree_r.status_code}): {tree_r.text[:300]}\n\n{body}"
                    ),
                }

            new_commit_r = client.post(
                f"https://api.github.com/repos/{owner}/{name}/git/commits",
                headers=headers,
                json={
                    "message": f"[AIO #{objective_id}] {title}"[:200],
                    "tree": tree_r.json()["sha"],
                    "parents": [base_sha],
                },
            )
            if new_commit_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": (
                        f"Commit create failed ({new_commit_r.status_code}): "
                        f"{new_commit_r.text[:300]}\n\n{body}"
                    ),
                }
            new_sha = new_commit_r.json()["sha"]

            ref_create = client.post(
                f"https://api.github.com/repos/{owner}/{name}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": new_sha},
            )
            if ref_create.status_code >= 400:
                # Branch may already exist from a prior attempt - force-update tip.
                ref_upd = client.patch(
                    f"https://api.github.com/repos/{owner}/{name}/git/refs/heads/{branch}",
                    headers=headers,
                    json={"sha": new_sha, "force": True},
                )
                if ref_upd.status_code >= 400:
                    return {
                        "ok": False,
                        "manual": True,
                        "branch": branch,
                        "message": (
                            f"Branch create/update failed ({ref_upd.status_code}): "
                            f"{ref_upd.text[:300]}\n\n{body}"
                        ),
                    }

            pr_r = client.post(
                f"https://api.github.com/repos/{owner}/{name}/pulls",
                headers=headers,
                json={
                    "title": f"[AIO #{objective_id}] {title}"[:200],
                    "head": branch,
                    "base": default_branch,
                    "body": pr_body[:60000],
                },
            )
            if pr_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "message": (
                        f"PR create failed ({pr_r.status_code}): {pr_r.text[:300]}\n"
                        f"Branch may exist: `{branch}`\n\n{body}"
                    ),
                    "files": list(files.keys()),
                }
            data = pr_r.json()
            return {
                "ok": True,
                "manual": False,
                "branch": branch,
                "pr_url": data.get("html_url"),
                "pr_number": data.get("number"),
                "files": list(files.keys()),
            }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "manual": True,
            "branch": branch,
            "message": f"GitHub unreachable ({exc}). Create PR manually.\n\n{body}",
        }


def open_pr_for_branch(
    *,
    project: Project,
    objective_id: int,
    title: str,
    branch: str,
    files: list[str] | None = None,
    workspace_note: str = "",
) -> dict[str, Any]:
    """Open a PR for an already-pushed branch (no blob/tree API)."""
    token = resolve_github_token(project)
    repo = _repo_slug(project)
    if not token or not repo:
        return {
            "ok": False,
            "manual": True,
            "branch": branch,
            "files": list(files or []),
            "message": (
                "No GITHUB_TOKEN / github_repo configured. Create a PR manually.\n\n"
                f"Suggested branch: `{branch}`"
            ),
        }

    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    file_lines = "\n".join(f"- `{p}`" for p in (files or [])[:40])
    pr_body = (
        f"AIO generated for objective #{objective_id}: {title}\n\n"
        f"#obj-{objective_id}\n\n"
        f"Committed from local agent workspace"
        f"{f' (`{workspace_note}`)' if workspace_note else ''}.\n"
    )
    if file_lines:
        pr_body += f"\n### Files\n{file_lines}\n"

    try:
        with httpx.Client(timeout=45.0) as client:
            repo_r = client.get(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
            if repo_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "files": list(files or []),
                    "message": f"GitHub repo lookup failed ({repo_r.status_code}).",
                }
            default_branch = (repo_r.json() or {}).get("default_branch") or "main"
            pr_r = client.post(
                f"https://api.github.com/repos/{owner}/{name}/pulls",
                headers=headers,
                json={
                    "title": f"[AIO #{objective_id}] {title}"[:200],
                    "head": branch,
                    "base": default_branch,
                    "body": pr_body[:60000],
                },
            )
            if pr_r.status_code >= 400:
                return {
                    "ok": False,
                    "manual": True,
                    "branch": branch,
                    "files": list(files or []),
                    "message": (
                        f"PR create failed ({pr_r.status_code}): {pr_r.text[:300]}\n"
                        f"Branch may exist: `{branch}`"
                    ),
                }
            data = pr_r.json()
            return {
                "ok": True,
                "manual": False,
                "branch": branch,
                "pr_url": data.get("html_url"),
                "pr_number": data.get("number"),
                "files": list(files or []),
            }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "manual": True,
            "branch": branch,
            "files": list(files or []),
            "message": f"GitHub unreachable ({exc}).",
        }


def _repo_slug(project: Project) -> str:
    return (project.github_repo or get_settings().github_repo or "").strip()


def pull_request_status(*, project: Project, pr_number: int) -> dict[str, Any]:
    """Read merge readiness for a PR. Never raises on HTTP errors."""
    from app.services.agent_workspace import scrub_secrets

    token = resolve_github_token(project)
    repo = _repo_slug(project)
    if not token or not repo:
        return {"ok": False, "reason_code": "not_configured", "message": "No GITHUB_TOKEN / repo configured."}
    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "reason_code": "unreachable",
            "message": scrub_secrets(f"GitHub unreachable ({exc}).", token),
        }
    if r.status_code == 404:
        return {"ok": False, "reason_code": "not_found", "message": f"PR #{pr_number} not found."}
    if r.status_code >= 400:
        return {
            "ok": False,
            "reason_code": "api_error",
            "message": scrub_secrets(f"GitHub error {r.status_code}: {r.text[:300]}", token),
        }
    data = r.json() or {}
    return {
        "ok": True,
        "state": data.get("state"),
        "merged": bool(data.get("merged")),
        "mergeable": data.get("mergeable"),
        "mergeable_state": data.get("mergeable_state"),
        "base": ((data.get("base") or {}).get("ref")) or "main",
        "head": ((data.get("head") or {}).get("ref")) or "",
        "title": data.get("title") or "",
        "html_url": data.get("html_url") or "",
    }


_MERGE_BLOCKERS = {
    "dirty": "the PR has merge conflicts",
    "blocked": "branch protection is blocking the merge",
}


def merge_pull_request(
    *,
    project: Project,
    pr_number: int,
    commit_title: str = "",
    commit_message: str = "",
    merge_method: str = "",
) -> dict[str, Any]:
    """Merge a PR after a readiness pre-flight. Returns a structured result."""
    from app.services.agent_workspace import scrub_secrets

    token = resolve_github_token(project)
    repo = _repo_slug(project)
    method = (merge_method or get_settings().merge_method or "squash").strip().lower()
    if method not in {"squash", "merge", "rebase"}:
        return {
            "ok": False,
            "merged": False,
            "reason_code": "bad_method",
            "message": f"Unsupported merge method {method!r}.",
        }
    if not token or not repo:
        return {
            "ok": False,
            "merged": False,
            "reason_code": "not_configured",
            "message": "No GITHUB_TOKEN / github_repo configured, cannot merge.",
        }

    pre = pull_request_status(project=project, pr_number=pr_number)
    if not pre.get("ok"):
        return {"ok": False, "merged": False, **pre}
    if pre.get("merged"):
        return {
            "ok": False,
            "merged": True,
            "reason_code": "already_merged",
            "message": f"PR #{pr_number} is already merged.",
            "base": pre.get("base"),
        }
    if (pre.get("state") or "") != "open":
        return {
            "ok": False,
            "merged": False,
            "reason_code": "closed",
            "message": f"PR #{pr_number} is {pre.get('state')}, not open.",
            "base": pre.get("base"),
        }
    if pre.get("mergeable") is False:
        state = (pre.get("mergeable_state") or "").lower()
        why = _MERGE_BLOCKERS.get(state, f"GitHub reports mergeable_state={state or 'unknown'}")
        return {
            "ok": False,
            "merged": False,
            "reason_code": "not_mergeable",
            "message": f"PR #{pr_number} cannot be merged: {why}.",
            "base": pre.get("base"),
        }
    state = (pre.get("mergeable_state") or "").lower()
    if state in _MERGE_BLOCKERS:
        return {
            "ok": False,
            "merged": False,
            "reason_code": "not_mergeable",
            "message": f"PR #{pr_number} cannot be merged: {_MERGE_BLOCKERS[state]}.",
            "base": pre.get("base"),
        }

    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload: dict[str, Any] = {"merge_method": method}
    if commit_title:
        payload["commit_title"] = commit_title[:250]
    if commit_message:
        payload["commit_message"] = commit_message[:60000]
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.put(
                f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}/merge",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "merged": False,
            "reason_code": "unreachable",
            "message": scrub_secrets(f"GitHub unreachable ({exc}).", token),
            "base": pre.get("base"),
        }

    if r.status_code == 200:
        data = r.json() or {}
        return {
            "ok": True,
            "merged": bool(data.get("merged", True)),
            "sha": data.get("sha"),
            "message": data.get("message") or "merged",
            "reason_code": "merged",
            "base": pre.get("base"),
            "head": pre.get("head"),
            "merge_method": method,
        }

    codes = {
        403: ("forbidden", "the token is not allowed to merge this PR"),
        404: ("not_found", "the PR or repository is not visible to this token"),
        405: ("not_mergeable", "GitHub refused the merge (not mergeable)"),
        409: ("head_changed", "the head branch moved since the last check"),
        422: ("invalid", "GitHub rejected the merge request as invalid"),
    }
    code, why = codes.get(r.status_code, ("api_error", f"GitHub returned {r.status_code}"))
    detail = ""
    try:
        detail = (r.json() or {}).get("message") or ""
    except ValueError:
        detail = r.text[:200]
    return {
        "ok": False,
        "merged": False,
        "reason_code": code,
        "message": scrub_secrets(f"Merge failed: {why}. {detail}".strip(), token),
        "base": pre.get("base"),
    }


def delete_remote_branch(*, project: Project, branch: str) -> dict[str, Any]:
    """Best-effort cleanup of a merged branch."""
    from app.services.agent_workspace import scrub_secrets

    token = resolve_github_token(project)
    repo = _repo_slug(project)
    if not token or not repo or not branch:
        return {"ok": False, "message": "not configured"}
    owner, name = repo.split("/", 1) if "/" in repo else (repo, repo)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.delete(
                f"https://api.github.com/repos/{owner}/{name}/git/refs/heads/{branch}",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "message": scrub_secrets(str(exc), token)}
    return {"ok": r.status_code in (204, 422), "status": r.status_code}


def artifact_files_for_objective(objective_id: int, title: str, content: str) -> dict[str, str]:
    """Map generated agent output to paths under aio/objectives/obj-N/."""
    base = f"aio/objectives/obj-{objective_id}"
    files: dict[str, str] = {
        f"{base}/README.md": (
            f"# Objective #{objective_id}: {title}\n\n"
            "Generated by the AIO coding agent.\n"
        )
    }
    text = content or ""
    fences = re.findall(r"```([^\n`]*)\n(.*?)```", text, flags=re.S)
    if fences:
        for i, (meta, code) in enumerate(fences):
            meta = (meta or "").strip()
            lang = meta.split()[0] if meta else ""
            path_hint = None
            if ":" in meta:
                # ```python:app/hello.py
                maybe = meta.split(":", 1)[1].strip()
                if maybe and ("/" in maybe or maybe.endswith(tuple(_EXT.values()))):
                    path_hint = maybe.lstrip("/")
            elif "/" in meta and not meta.startswith("http"):
                path_hint = meta.lstrip("/")
            body = code if code.endswith("\n") else code + "\n"
            if path_hint and ".." not in path_hint:
                files[f"{base}/{path_hint}"] = body
            else:
                ext = _EXT.get(lang.lower(), "txt")
                name = f"main.{ext}" if len(fences) == 1 else f"snippet_{i + 1}.{ext}"
                files[f"{base}/{name}"] = body
    else:
        files[f"{base}/output.md"] = text if text.endswith("\n") else text + "\n"
    return files


def branch_slug(title: str) -> str:
    return _slug(title)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "work").lower()).strip("-")
    return (s or "work")[:40]
