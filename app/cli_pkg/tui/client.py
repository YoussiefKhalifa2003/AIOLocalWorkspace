"""HTTP client for the terminal app.

One place for every endpoint the web UI uses, so the TUI can be a full
replacement rather than a board viewer. Network errors are returned as values
(``ApiError``) instead of raised, because a dropped connection should show up
as a red status line, never a crashed dashboard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.cli_pkg.session import auth_headers, resolve_base_url


class ApiError(RuntimeError):
    """A request failed; the message is already human readable."""


def resolve_attach_path(raw: str | Path) -> Path:
    """Resolve a user-typed path for CLI attach (cwd-relative, ~, Windows paths)."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise ApiError("path required")
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = p.resolve(strict=False)
    except OSError as exc:
        raise ApiError(f"invalid path: {exc}") from exc
    if not p.exists():
        raise ApiError(f"file not found: {p}")
    if not p.is_file():
        raise ApiError(f"not a file: {p}")
    return p


@dataclass
class Workspace:
    """Everything the app needs for one render pass."""

    me: dict[str, Any] = field(default_factory=dict)
    chats: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    mentions: list[dict[str, Any]] = field(default_factory=list)
    unread: int = 0
    presence: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def is_owner(self) -> bool:
        return bool(self.me.get("is_owner"))

    @property
    def channels(self) -> list[dict[str, Any]]:
        return [c for c in self.chats if c.get("kind") == "channel"]

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return [c for c in self.chats if c.get("kind") == "private"]


def login(email: str, password: str, base_url: str = "") -> dict[str, Any]:
    """Exchange email + password for an API key. Raises ApiError on failure."""
    url = (base_url or resolve_base_url()).rstrip("/")
    try:
        r = httpx.post(f"{url}/auth/login", json={"email": email, "password": password}, timeout=15.0)
    except httpx.HTTPError as exc:
        raise ApiError(f"cannot reach {url}: {exc}") from exc
    if r.status_code >= 400:
        raise ApiError(_detail(r) or "login failed")
    return r.json()


class ApiClient:
    def __init__(
        self,
        *,
        project_id: int = 1,
        api_key: str | None = None,
        email: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.project_id = int(project_id)
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        self.headers = auth_headers(api_key, email)
        self.timeout = timeout

    # plumbing ------------------------------------------------------------

    def _request(self, method: str, path: str, *, timeout: float | None = None, **kw) -> Any:
        try:
            with httpx.Client(
                base_url=self.base_url, headers=self.headers, timeout=timeout or self.timeout
            ) as client:
                r = client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(f"{exc.__class__.__name__}: {exc}") from exc
        if r.status_code >= 400:
            raise ApiError(_detail(r))
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    def get(self, path: str, **kw) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw) -> Any:
        return self._request("POST", path, **kw)

    def put(self, path: str, **kw) -> Any:
        return self._request("PUT", path, **kw)

    def patch(self, path: str, **kw) -> Any:
        return self._request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self._request("DELETE", path, **kw)

    def download_bytes(self, path: str, *, timeout: float | None = None) -> bytes:
        """GET a binary path (e.g. /attachments/12)."""
        try:
            with httpx.Client(
                base_url=self.base_url, headers=self.headers, timeout=timeout or self.timeout
            ) as client:
                r = client.get(path)
        except httpx.HTTPError as exc:
            raise ApiError(f"{exc.__class__.__name__}: {exc}") from exc
        if r.status_code >= 400:
            raise ApiError(_detail(r) or f"download failed ({r.status_code})")
        return r.content

    def _safe(self, path: str, default: Any) -> Any:
        try:
            return self.get(path)
        except ApiError:
            return default

    # identity ------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        return self.get("/auth/me", timeout=10.0)

    def projects(self) -> list[dict[str, Any]]:
        return self._safe("/projects", [])

    def members(self) -> list[dict[str, Any]]:
        return self._safe("/workspace/members", [])

    def workspace(self) -> Workspace:
        """One poll: identity, chat list, members, mentions, presence."""
        try:
            me = self.me()
        except ApiError as exc:
            return Workspace(error=str(exc))
        chats = self._safe("/chats", [])
        members = self._safe("/workspace/members", [])
        mentions = self._safe("/workspace/mentions", {}) or {}
        presence_payload = self._safe("/workspace/presence", {}) or {}
        presence = presence_payload.get("users") if isinstance(presence_payload, dict) else []
        return Workspace(
            me=me,
            chats=chats if isinstance(chats, list) else [],
            members=members if isinstance(members, list) else [],
            mentions=mentions.get("mentions") or [],
            unread=int(mentions.get("unread") or 0),
            presence=presence if isinstance(presence, list) else [],
        )

    def post_presence(
        self,
        *,
        chat_id: int | None = None,
        typing: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id}
        if typing is not None:
            body["typing"] = typing
        return self.post("/workspace/presence", json=body, timeout=10.0)

    def get_presence(self) -> list[dict[str, Any]]:
        out = self.get("/workspace/presence", timeout=10.0)
        users = out.get("users") if isinstance(out, dict) else []
        return users if isinstance(users, list) else []

    # chat ----------------------------------------------------------------

    def messages(self, chat_id: int, *, after_id: int = 0, since: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"after_id": after_id}
        if since:
            params["since"] = since
        out = self.get(f"/chats/{chat_id}/messages", params=params)
        return out if isinstance(out, list) else []

    def send_message(self, chat_id: int, body: str, attachment_ids: list[int] | None = None) -> dict:
        return self.post(
            f"/chats/{chat_id}/messages",
            json={"body": body, "speak": False, "attachment_ids": attachment_ids or []},
            timeout=300.0,  # a /deepresearch runs synchronously inside this call
        )

    def edit_message(self, chat_id: int, message_id: int, body: str) -> dict[str, Any]:
        return self.patch(
            f"/chats/{chat_id}/messages/{message_id}",
            json={"body": body},
            timeout=300.0,
        )

    def delete_message(self, chat_id: int, message_id: int) -> dict[str, Any]:
        return self.delete(f"/chats/{chat_id}/messages/{message_id}")

    def transcribe(self, path: str | Path) -> str:
        """Upload audio to POST /stt and return transcript text."""
        p = resolve_attach_path(path)
        data = p.read_bytes()
        try:
            with httpx.Client(
                base_url=self.base_url, headers=self.headers, timeout=120.0
            ) as client:
                r = client.post(
                    "/stt",
                    files={"file": (p.name, data, None)},
                )
        except httpx.HTTPError as exc:
            raise ApiError(f"{exc.__class__.__name__}: {exc}") from exc
        if r.status_code >= 400:
            raise ApiError(_detail(r) or "transcription failed")
        out = r.json()
        if not isinstance(out, dict):
            raise ApiError("transcription failed: bad response")
        text = str(out.get("text") or "").strip()
        if not text:
            raise ApiError("transcription returned empty text")
        return text

    def upload_attachment(self, chat_id: int, path: str | Path) -> dict[str, Any]:
        """Upload a local file to the chat (multipart). Returns attachment dict."""
        p = resolve_attach_path(path)
        data = p.read_bytes()
        try:
            with httpx.Client(
                base_url=self.base_url, headers=self.headers, timeout=60.0
            ) as client:
                r = client.post(
                    f"/chats/{chat_id}/attachments",
                    files={"file": (p.name, data, None)},
                )
        except httpx.HTTPError as exc:
            raise ApiError(f"{exc.__class__.__name__}: {exc}") from exc
        if r.status_code >= 400:
            raise ApiError(_detail(r) or "upload failed")
        out = r.json()
        if not isinstance(out, dict):
            raise ApiError("upload failed: bad response")
        return out

    def create_chat(self, name: str) -> dict[str, Any]:
        return self.post("/chats", json={"name": name, "kind": "channel"})

    def delete_chat(self, chat_id: int) -> dict[str, Any]:
        return self.delete(f"/chats/{chat_id}")

    def mark_mentions_read(self, ids: list[int] | None = None) -> dict[str, Any]:
        payload = {"ids": ids} if ids else {}
        return self.post("/workspace/mentions/read", json=payload)

    # people (owner-only on the server) -----------------------------------

    def set_member_role(self, user_id: int, role: str) -> dict[str, Any]:
        return self.patch(f"/workspace/members/{user_id}", json={"role": role})

    def remove_member(self, user_id: int) -> dict[str, Any]:
        return self.delete(f"/workspace/members/{user_id}")

    def invite_link(self, max_uses: int = 1) -> dict[str, Any]:
        return self.post("/workspace/invite-link", params={"max_uses": max_uses})

    def invite_email(self, email: str, max_uses: int = 1) -> dict[str, Any]:
        return self.post(
            "/workspace/invite-email",
            json={"email": email, "max_uses": max_uses},
            timeout=120.0,
        )

    # board ---------------------------------------------------------------

    def board(self) -> dict[str, Any]:
        return self.get(f"/projects/{self.project_id}/board")

    def jobs_summary(self) -> dict[str, Any]:
        data = self._safe(f"/projects/{self.project_id}/jobs/summary", {}) or {}
        if not isinstance(data, dict):
            return {"total": 0, "by_status": {}, "by_model": []}
        return data

    def jobs_total(self) -> int:
        return int(self.jobs_summary().get("total") or 0)

    def add_objective(self, title: str) -> dict[str, Any]:
        return self.post(f"/projects/{self.project_id}/objectives", json={"title": title})

    def setup_objective(
        self,
        objective_id: int,
        *,
        description: str = "",
        subtasks: list[str] | None = None,
        dismiss: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any]
        if dismiss:
            payload = {"dismiss": True}
        else:
            payload = {"description": description, "subtasks": list(subtasks or [])}
        return self.put(
            f"/projects/{self.project_id}/objectives/{objective_id}/setup",
            json=payload,
        )

    def set_status(self, objective_id: int, status: str, runner: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"status": status}
        if runner:
            payload["coding_runner"] = runner
        return self.patch(
            f"/projects/{self.project_id}/objectives/{objective_id}", json=payload, timeout=60.0
        )

    def merge(self, objective_id: int, *, merge_method: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"confirm": True}
        if merge_method:
            payload["merge_method"] = merge_method
        return self.post(
            f"/projects/{self.project_id}/objectives/{objective_id}/merge",
            json=payload,
            timeout=180.0,
        )

    # agents / dashboard --------------------------------------------------

    def agent_models(self) -> dict[str, Any]:
        return self.get("/workspace/agent-models")

    def save_agent_models(self, prefs: dict[str, str]) -> dict[str, Any]:
        body = {"prefs": [{"agent_type": k, "model_id": v} for k, v in prefs.items()]}
        return self.patch("/workspace/agent-models", json=body, timeout=60.0)

    def analytics(self) -> dict[str, Any]:
        return self.get(f"/projects/{self.project_id}/analytics")

    def metrics_series(self, limit: int = 60) -> dict[str, Any]:
        return self.get(
            f"/projects/{self.project_id}/metrics/series", params={"limit": limit}
        )

    def assign(self, objective_id: int, assignee_user_id: int) -> dict[str, Any]:
        return self.post(
            f"/projects/{self.project_id}/dashboard/assign",
            json={"objective_id": objective_id, "assignee_user_id": assignee_user_id},
        )


class RingBuffer:
    """Fixed-size series for client-side WIP animation between polls."""

    def __init__(self, size: int = 60) -> None:
        self.size = max(1, int(size))
        self._data: list[float] = []

    def extend(self, values: list[float] | list[int]) -> None:
        self._data.extend(float(v) for v in values)
        if len(self._data) > self.size:
            self._data = self._data[-self.size :]

    def append(self, value: float | int) -> None:
        self.extend([value])

    def values(self) -> list[float]:
        return list(self._data)

    def __len__(self) -> int:
        return len(self._data)


def column_counts(board: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for col in board.get("columns") or []:
        out[str(col.get("id") or "")] = len(col.get("cards") or [])
    return out


def live_fingerprint(
    analytics: dict[str, Any],
    series: dict[str, Any],
    columns: dict[str, int],
    jobs_summary: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "summary": (analytics or {}).get("summary") or {},
            "people": [
                [p.get("email"), p.get("tokens"), p.get("jobs")]
                for p in (analytics or {}).get("people") or []
            ],
            "models": [
                [m.get("model"), m.get("runs"), m.get("success"), m.get("fail")]
                for m in (analytics or {}).get("models") or []
            ],
            "buckets": (series or {}).get("buckets") or {},
            "cols": columns,
            "jobs": {
                "total": (jobs_summary or {}).get("total"),
                "by_status": (jobs_summary or {}).get("by_status") or {},
            },
        },
        sort_keys=True,
    )


def board_fingerprint(board: dict[str, Any], jobs_today: Any = "") -> str:
    """Stable digest so the terminal only redraws when something changed."""
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
                            c.get("description") or "",
                            c.get("progress_percent"),
                            c.get("checklist_closed") or 0,
                            c.get("checklist_total") or 0,
                            c.get("pr_url") or "",
                            c.get("pr_number") or 0,
                            c.get("repo_url") or "",
                            c.get("github_branch") or "",
                            1 if c.get("can_merge") else 0,
                            c.get("open_issue_count") or 0,
                            ",".join(c.get("claimed_paths") or []),
                            c.get("owner_email") or "",
                            # Subtask titles + done flags so setup/edits redraw the board.
                            [
                                [t.get("id"), t.get("title"), 1 if t.get("done") else 0]
                                for t in (c.get("subtasks") or [])
                            ],
                        ]
                        for c in col.get("cards", [])
                    ],
                ]
                for col in board.get("columns", [])
            ],
        },
        sort_keys=True,
    )


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json() or {}
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    detail = payload.get("detail")
    if isinstance(detail, list) and detail:
        detail = detail[0].get("msg") if isinstance(detail[0], dict) else str(detail[0])
    return str(detail or f"HTTP {response.status_code}")
