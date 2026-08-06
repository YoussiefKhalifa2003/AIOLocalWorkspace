"""Credential store for the AIO CLI (~/.aio/credentials.json, mode 600)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import get_settings

CREDENTIALS_ENV = "AIO_CREDENTIALS"


@dataclass
class Credentials:
    api_key: str = ""
    email: str = ""
    user_id: int = 0
    project_id: int = 0
    api_base_url: str = ""

    def is_empty(self) -> bool:
        return not (self.api_key and self.email)


def credentials_path() -> Path:
    override = os.environ.get(CREDENTIALS_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".aio" / "credentials.json"


def load_credentials() -> Credentials:
    path = credentials_path()
    if not path.exists():
        return Credentials()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Credentials()
    if not isinstance(data, dict):
        return Credentials()
    return Credentials(
        api_key=str(data.get("api_key") or ""),
        email=str(data.get("email") or ""),
        user_id=int(data.get("user_id") or 0),
        project_id=int(data.get("project_id") or 0),
        api_base_url=str(data.get("api_base_url") or ""),
    )


def save_credentials(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(creds), indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def clear_credentials() -> bool:
    path = credentials_path()
    if path.exists():
        path.unlink()
        return True
    return False


def resolve_base_url(creds: Credentials | None = None) -> str:
    creds = creds if creds is not None else load_credentials()
    return (creds.api_base_url or get_settings().api_base_url).rstrip("/")


def resolve_project_id(explicit: int | None = None) -> int:
    if explicit:
        return int(explicit)
    stored = load_credentials().project_id
    return int(stored or 1)


def auth_headers(api_key: str | None = None, email: str | None = None) -> dict[str, str]:
    """Stored credentials, with explicit overrides and a demo-key fallback."""
    settings = get_settings()
    creds = load_credentials()
    key = (api_key or creds.api_key or settings.demo_api_key or "").strip()
    mail = (email or creds.email or "").strip()
    headers = {"X-API-Key": key}
    join = (settings.workspace_join_key or settings.demo_api_key or "").strip()
    if key == join:
        # Shared join key needs an email to identify the user.
        headers["X-User-Email"] = mail or "a@local.test"
    elif mail:
        headers["X-User-Email"] = mail
    return headers
