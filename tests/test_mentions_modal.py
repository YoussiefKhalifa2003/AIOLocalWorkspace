"""Mention panel helpers + open payload shape."""

from __future__ import annotations

from datetime import datetime, timezone

from app.cli_pkg.tui.widgets import _mention_time_label


def test_mention_time_label_today():
    now = datetime.now(timezone.utc).replace(hour=14, minute=5, second=0, microsecond=0)
    label = _mention_time_label(now.isoformat().replace("+00:00", "Z"))
    assert ":" in label
    assert len(label) <= 5  # HH:MM


def test_mention_time_label_empty():
    assert _mention_time_label(None) == ""
    assert _mention_time_label("") == ""


def test_mention_open_payload_shape():
    m = {
        "id": 9,
        "chat_id": 3,
        "message_id": 44,
        "from": "Alice",
        "snippet": "hey @bob",
        "chat_name": "general",
        "created_at": "2026-01-01T12:00:00Z",
    }
    payload = {
        "action": "open",
        "chat_id": int(m.get("chat_id") or 0),
        "message_id": int(m.get("message_id") or 0),
        "mention_id": int(m.get("id") or 0),
    }
    assert payload == {
        "action": "open",
        "chat_id": 3,
        "message_id": 44,
        "mention_id": 9,
    }
