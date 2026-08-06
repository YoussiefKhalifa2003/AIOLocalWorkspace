from app.services.teams_notify import build_invite_payload, notify_invite_link


def test_teams_notify_skipped_without_url(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    result = notify_invite_link(invite_url="http://127.0.0.1:8000/join/abc", max_uses=3)
    assert result["skipped"] is True
    assert result["ok"] is False


def test_teams_payload_has_open_action():
    payload = build_invite_payload(
        invite_url="http://10.0.0.1:8000/join/tok",
        max_uses=5,
        workspace="Demo",
    )
    assert payload["@type"] == "MessageCard"
    assert "5 uses" in payload["summary"]
    assert payload["potentialAction"][0]["targets"][0]["uri"].endswith("/join/tok")
