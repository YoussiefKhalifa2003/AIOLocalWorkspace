from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import get_settings
from app.db.models import Project


def resolve_github_token(project: Project) -> str:
    settings = get_settings()
    return (project.github_token or settings.github_token or "").strip()


def create_pr_from_artifact(
    *,
    project: Project,
    objective_id: int,
    title: str,
    body: str,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Create a GitHub PR via API, or return a manual fallback payload.

    v1: opens PR with description containing the artifact (no local git clone).
    """
    token = resolve_github_token(project)
    repo = (project.github_repo or "").strip()
    branch = branch_name or f"aio/obj-{objective_id}-{_slug(title)}"
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
    try:
        with httpx.Client(timeout=30.0) as client:
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
            sha = ref_r.json()["object"]["sha"]
            client.post(
                f"https://api.github.com/repos/{owner}/{name}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": sha},
            )
            pr_r = client.post(
                f"https://api.github.com/repos/{owner}/{name}/pulls",
                headers=headers,
                json={
                    "title": f"[AIO #{objective_id}] {title}"[:200],
                    "head": branch,
                    "base": default_branch,
                    "body": body[:60000],
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
                }
            data = pr_r.json()
            return {
                "ok": True,
                "manual": False,
                "branch": branch,
                "pr_url": data.get("html_url"),
                "pr_number": data.get("number"),
            }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "manual": True,
            "branch": branch,
            "message": f"GitHub unreachable ({exc}). Create PR manually.\n\n{body}",
        }


def _slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "work").lower()).strip("-")
    return (s or "work")[:40]
