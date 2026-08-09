"""Send workspace invite emails through Outlook Web via Playwright (free, no SMTP API).

Requires:
  .venv/bin/python -m playwright install chromium
  aio outlook-login   # one-time interactive sign-in; saves session to data/outlook_auth.json

Only addresses allowed by INVITE_ALLOWED_DOMAIN are accepted when that env is set.
Empty INVITE_ALLOWED_DOMAIN = any valid email.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

from app.config import get_settings
from app.services.invite_domain import assert_allowed_invite_email, invite_allowed_domain

log = logging.getLogger(__name__)

OUTLOOK_COMPOSE = "https://outlook.office.com/mail/deeplink/compose"
OUTLOOK_HOME = "https://outlook.office.com/mail/"


def default_browsers_path() -> Path:
    """Stable per-user Playwright cache (never Cursor sandbox temp dirs)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def fix_playwright_browsers_path() -> Path:
    """Point Playwright at the real user cache; drop Cursor/sandbox overrides."""
    target = default_browsers_path()
    target.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(target)
    return target


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
    domain = invite_allowed_domain()
    subject = f"Join {workspace} workspace"
    body = (
        f"You've been invited to the {workspace} workspace ({seats}).\n\n"
        f"Open this link to register (works off-VPN when using a public invite URL):\n"
        f"{invite_url}\n\n"
        f"After signup, run: aio\n"
        f"On Sign in, paste the Server URL from the Done page if prompted, "
        f"then use your email and password."
    )
    if domain:
        body += f"\n\nUse your @{domain} email when registering."
    return subject, body


def ensure_playwright_browsers() -> Path:
    """Install Chromium into the user cache if missing."""
    cache = fix_playwright_browsers_path()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = Path(p.chromium.executable_path)
    if exe.is_file():
        return cache

    print("Downloading Chromium for Playwright (one-time)…", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(cache)},
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Could not install Chromium.\n"
            "From the WORK folder run:\n"
            "  .venv/bin/python -m playwright install chromium\n"
            "(Do not use Homebrew `playwright` — use the venv command above.)"
        )

    with sync_playwright() as p:
        exe = Path(p.chromium.executable_path)
    if not exe.is_file():
        raise RuntimeError(
            f"Chromium still missing at {exe}.\n"
            "Run: .venv/bin/python -m playwright install chromium"
        )
    return cache


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
            "reason": (
                "playwright not installed — "
                "pip install playwright && .venv/bin/python -m playwright install chromium"
            ),
        }

    try:
        ensure_playwright_browsers()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": False, "reason": str(exc)[:300]}

    subject, body = build_invite_email(
        invite_url=invite_url, max_uses=max_uses, workspace=workspace or "AIO"
    )
    qs = urllib.parse.urlencode({"to": to, "subject": subject, "body": body})
    url = f"{OUTLOOK_COMPOSE}?{qs}"
    # Default visible browser so you can watch the send.
    use_headless = settings.outlook_headless if headless is None else headless
    timeout_ms = int(settings.outlook_timeout_seconds * 1000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=use_headless)
            context = browser.new_context(storage_state=str(storage))
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            sent = False
            for selector in (
                'button[aria-label="Send"]',
                'button[title="Send"]',
                '[data-automation-id="sendButton"]',
                'button:has-text("Send")',
            ):
                try:
                    btn = page.locator(selector).first
                    if btn.count() and btn.is_visible(timeout=3000):
                        btn.click(timeout=5000)
                        sent = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not sent:
                # Outlook on Mac: Cmd+Enter
                page.keyboard.press("Meta+Enter")
                page.wait_for_timeout(500)
                page.keyboard.press("Control+Enter")

            page.wait_for_timeout(2500)
            context.storage_state(path=str(storage))
            browser.close()
        log.info("Outlook invite sent to %s", to)
        return {"ok": True, "skipped": False, "to": to, "headless": use_headless}
    except Exception as exc:  # noqa: BLE001
        log.warning("Outlook invite failed: %s", exc)
        return {"ok": False, "skipped": False, "reason": str(exc)[:400], "to": to}


def interactive_outlook_login(*, headed: bool = True) -> Path:
    """Open Outlook so the user can sign in; persist cookies for later sends."""
    from playwright.sync_api import sync_playwright

    ensure_playwright_browsers()
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
            "When your inbox is visible, return here and press Enter.\n",
            flush=True,
        )
        try:
            input()
        except EOFError:
            page.wait_for_timeout(60_000)
        context.storage_state(path=str(storage))
        browser.close()
    return storage
