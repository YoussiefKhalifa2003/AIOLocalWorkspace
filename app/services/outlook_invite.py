"""Send workspace invite emails through Outlook Web via Playwright (free, no SMTP API).

Requires:
  pip install playwright && playwright install chromium
  aio outlook-login   # one-time interactive sign-in; saves session to data/outlook_auth.json

Only addresses allowed by INVITE_ALLOWED_DOMAIN (default tatweermea.com) are accepted.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from app.config import get_settings
from app.services.invite_domain import assert_allowed_invite_email, invite_allowed_domain

log = logging.getLogger(__name__)

OUTLOOK_COMPOSE = "https://outlook.office.com/mail/deeplink/compose"
OUTLOOK_HOME = "https://outlook.office.com/mail/"


def outlook_storage_path() -> Path:
    settings = get_settings()
    raw = (settings.outlook_storage_state or "data/outlook_auth.json").strip()
    return Path(raw).expanduser()


def outlook_configured() -> bool:
    settings = get_settings()
    if not settings.outlook_invite_enabled:
        return False
    return outlook_storage_path().is_file()


def build_invite_email(*, invite_url: str, max_uses: int, workspace: str) -> tuple[str, str]:
    uses = max(1, int(max_uses or 1))
    seats = "1 use" if uses == 1 else f"{uses} uses"
    domain = invite_allowed_domain() or "tatweermea.com"
    subject = f"Join {workspace} workspace"
    body = (
        f"You've been invited to the {workspace} workspace ({seats}).\n\n"
        f"Open this link on the same network as the server to register:\n"
        f"{invite_url}\n\n"
        f"Use your @{domain} email."
    )
    return subject, body


def send_invite_via_outlook(
    *,
    to_email: str,
    invite_url: str,
    max_uses: int = 1,
    workspace: str = "AIO",
    headless: bool | None = None,
) -> dict:
    """Compose + send an Outlook mail. Never raises — result is in the returned dict."""
    try:
        to = assert_allowed_invite_email(to_email)
    except ValueError as exc:
        return {"ok": False, "skipped": False, "reason": str(exc)}

    settings = get_settings()
    if not settings.outlook_invite_enabled:
        return {"ok": False, "skipped": True, "reason": "OUTLOOK_INVITE_ENABLED is false"}

    storage = outlook_storage_path()
    if not storage.is_file():
        return {
            "ok": False,
            "skipped": False,
            "reason": f"Outlook session missing ({storage}). Run: aio outlook-login",
        }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "skipped": False,
            "reason": "playwright not installed — pip install playwright && playwright install chromium",
        }

    subject, body = build_invite_email(
        invite_url=invite_url, max_uses=max_uses, workspace=workspace or "AIO"
    )
    qs = urllib.parse.urlencode({"to": to, "subject": subject, "body": body})
    url = f"{OUTLOOK_COMPOSE}?{qs}"
    use_headless = settings.outlook_headless if headless is None else headless
    timeout_ms = int(settings.outlook_timeout_seconds * 1000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=use_headless)
            context = browser.new_context(storage_state=str(storage))
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)

            sent = False
            for selector in (
                'button[aria-label="Send"]',
                'button[title="Send"]',
                '[data-automation-id="sendButton"]',
                'button:has-text("Send")',
            ):
                try:
                    btn = page.locator(selector).first
                    if btn.count() and btn.is_visible(timeout=2000):
                        btn.click(timeout=5000)
                        sent = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not sent:
                page.keyboard.press("Control+Enter")
                page.wait_for_timeout(800)
                page.keyboard.press("Meta+Enter")

            page.wait_for_timeout(2000)
            context.storage_state(path=str(storage))
            browser.close()
        log.info("Outlook invite sent to %s", to)
        return {"ok": True, "skipped": False, "to": to}
    except Exception as exc:  # noqa: BLE001
        log.warning("Outlook invite failed: %s", exc)
        return {"ok": False, "skipped": False, "reason": str(exc)[:300], "to": to}


def interactive_outlook_login(*, headed: bool = True) -> Path:
    """Open Outlook so the user can sign in; persist cookies for later sends."""
    from playwright.sync_api import sync_playwright

    storage = outlook_storage_path()
    storage.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(get_settings().outlook_timeout_seconds * 1000)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context()
        page = context.new_page()
        page.goto(OUTLOOK_HOME, wait_until="domcontentloaded", timeout=timeout_ms)
        print(
            "\nSign in to Outlook in the browser window.\n"
            "When your inbox is visible, return here and press Enter.\n"
        )
        try:
            input()
        except EOFError:
            page.wait_for_timeout(60_000)
        context.storage_state(path=str(storage))
        browser.close()
    return storage
