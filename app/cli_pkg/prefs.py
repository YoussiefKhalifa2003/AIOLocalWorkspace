"""Local CLI preferences (~/.aio/prefs.json) — non-secret UI flags."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PREFS_ENV = "AIO_PREFS"
TUTORIAL_VERSION = 1


def prefs_path() -> Path:
    override = os.environ.get(PREFS_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".aio" / "prefs.json"


def _load_raw() -> dict[str, Any]:
    path = prefs_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict[str, Any]) -> Path:
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def is_tutorial_done(email: str, *, version: int = TUTORIAL_VERSION) -> bool:
    key = (email or "").strip().lower()
    if not key:
        return False
    tutorial = _load_raw().get("tutorial") or {}
    if not isinstance(tutorial, dict):
        return False
    row = tutorial.get(key) or {}
    if not isinstance(row, dict):
        return False
    if not row.get("completed"):
        return False
    return int(row.get("version") or 0) >= int(version)


def mark_tutorial_done(email: str, *, version: int = TUTORIAL_VERSION) -> Path:
    key = (email or "").strip().lower()
    data = _load_raw()
    tutorial = data.get("tutorial")
    if not isinstance(tutorial, dict):
        tutorial = {}
    tutorial[key] = {
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": int(version),
    }
    data["tutorial"] = tutorial
    return _save_raw(data)


def clear_tutorial_done(email: str) -> Path:
    key = (email or "").strip().lower()
    data = _load_raw()
    tutorial = data.get("tutorial")
    if isinstance(tutorial, dict) and key in tutorial:
        tutorial.pop(key, None)
        data["tutorial"] = tutorial
        return _save_raw(data)
    return prefs_path()
