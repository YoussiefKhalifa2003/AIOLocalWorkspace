"""Post invite links to a Microsoft Teams Incoming Webhook / Workflow URL.

Set TEAMS_WEBHOOK_URL in .env to the full webhook URL from Teams.
If empty, notify_invite_link() is a no-op.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


def teams_webhook_configured() -> bool:
    return bool((get_settings().teams_webhook_url or "").strip())


def build_invite_payload(*, invite_url: str, max_uses: int, workspace: str = "AIO") -> dict:
    """MessageCard payload for classic Incoming Webhooks (also works with many Workflows)."""
    uses = max(1, int(max_uses or 1))
    seats = "1 use" if uses == 1 else f"{uses} uses"
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"{workspace} invite ({seats})",
        "themeColor": "2E7D4F",
        "title": f"{workspace} workspace invite",
        "text": (
            f"Join **{workspace}** with this link ({seats}). "
            f"Open it on the same LAN as the server."
        ),
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Open invite",
                "targets": [{"os": "default", "uri": invite_url}],
            }
        ],
    }


def notify_invite_link(
    *,
    invite_url: str,
    max_uses: int = 1,
    workspace: str = "AIO",
) -> dict:
    """POST invite to Teams. Never raises — failures are returned in the dict."""
    settings = get_settings()
    hook = (settings.teams_webhook_url or "").strip()
    if not hook:
        return {"ok": False, "skipped": True, "reason": "TEAMS_WEBHOOK_URL not set"}

    payload = build_invite_payload(
        invite_url=invite_url,
        max_uses=max_uses,
        workspace=workspace or "AIO",
    )
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(hook, json=payload)
        if resp.status_code >= 300:
            log.warning("Teams webhook HTTP %s: %s", resp.status_code, resp.text[:300])
            return {
                "ok": False,
                "skipped": False,
                "status_code": resp.status_code,
                "reason": (resp.text or "")[:200],
            }
        return {"ok": True, "skipped": False, "status_code": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        log.warning("Teams webhook failed: %s", exc)
        return {"ok": False, "skipped": False, "reason": str(exc)}
