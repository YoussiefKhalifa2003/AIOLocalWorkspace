"""Polling client for the owner TUI.

Mirrors the web board's fingerprint diffing so the terminal only redraws when
something actually changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.cli_pkg.session import auth_headers, resolve_base_url


class OwnerRequired(RuntimeError):
    """The signed-in user does not own the workspace."""


@dataclass
class Snapshot:
    board: dict[str, Any] = field(default_factory=dict)
    jobs_today: int = 0
    unread_mentions: int = 0
    error: str = ""
    fingerprint: str = ""

    @property
    def cards(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for col in self.board.get("columns", []):
            for card in col.get("cards", []):
                out.append({**card, "status": card.get("status") or col["id"]})
        return out


def board_fingerprint(board: dict[str, Any], jobs_today: Any = "") -> str:
    return json.dumps(
        {
            "jobs": jobs_today,
            "cols": [
                [
                    col.get("id"),
                    [
                        [
                            c.get("id"),
                            c.get("title"),
                            c.get("progress_percent"),
                            c.get("pr_url") or "",
                            c.get("pr_number") or 0,
                            c.get("repo_url") or "",
                            c.get("github_branch") or "",
                            1 if c.get("can_merge") else 0,
                            c.get("open_issue_count") or 0,
                            ",".join(c.get("claimed_paths") or []),
                            c.get("owner_email") or "",
                        ]
                        for c in col.get("cards", [])
                    ],
                ]
                for col in board.get("columns", [])
            ],
        },
        sort_keys=True,
    )


class BoardClient:
    """Thin, forgiving HTTP client: network errors become `Snapshot.error`."""

    def __init__(
        self,
        project_id: int,
        *,
        api_key: str | None = None,
        email: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.project_id = int(project_id)
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        self.headers = auth_headers(api_key, email)
        self.timeout = timeout

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            timeout=timeout or self.timeout,
        )

    def me(self) -> dict[str, Any]:
        with self._client(timeout=10.0) as client:
            r = client.get("/auth/me")
        if r.status_code >= 400:
            raise OwnerRequired(f"the API rejected these credentials ({r.status_code})")
        return r.json()

    def require_owner(self) -> dict[str, Any]:
        me = self.me()
        if not me.get("is_owner"):
            raise OwnerRequired(
                "The TUI dashboard is owner-only. Use `aio board` / `aio card` instead."
            )
        return me

    def snapshot(self) -> Snapshot:
        try:
            with self._client() as client:
                r = client.get(f"/projects/{self.project_id}/board")
                if r.status_code >= 400:
                    return Snapshot(error=f"board: HTTP {r.status_code}")
                board = r.json()
                jobs = 0
                s = client.get(f"/projects/{self.project_id}/jobs/summary")
                if s.status_code < 400:
                    jobs = int((s.json() or {}).get("total") or 0)
                mentions = 0
                m = client.get("/workspace/mentions")
                if m.status_code < 400:
                    mentions = int((m.json() or {}).get("unread") or 0)
        except httpx.HTTPError as exc:
            return Snapshot(error=f"{exc.__class__.__name__}: {exc}")
        return Snapshot(
            board=board,
            jobs_today=jobs,
            unread_mentions=mentions,
            fingerprint=board_fingerprint(board, jobs),
        )

    def set_status(self, objective_id: int, status: str, runner: str = "") -> str:
        payload: dict[str, Any] = {"status": status}
        if runner:
            payload["coding_runner"] = runner
        try:
            with self._client(timeout=30.0) as client:
                r = client.patch(
                    f"/projects/{self.project_id}/objectives/{objective_id}", json=payload
                )
        except httpx.HTTPError as exc:
            return f"{exc.__class__.__name__}: {exc}"
        if r.status_code >= 400:
            return _detail(r)
        return ""

    def merge(self, objective_id: int, *, merge_method: str = "") -> tuple[bool, str]:
        payload: dict[str, Any] = {"confirm": True}
        if merge_method:
            payload["merge_method"] = merge_method
        try:
            with self._client(timeout=120.0) as client:
                r = client.post(
                    f"/projects/{self.project_id}/objectives/{objective_id}/merge",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return False, f"{exc.__class__.__name__}: {exc}"
        if r.status_code >= 400:
            return False, _detail(r)
        data = r.json()
        return True, f"merged into {data.get('base')} ({str(data.get('sha') or '')[:8]})"


def _detail(response: httpx.Response) -> str:
    try:
        return str((response.json() or {}).get("detail") or response.text)
    except ValueError:
        return response.text
