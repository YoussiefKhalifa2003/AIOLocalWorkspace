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


def repo_root() -> Path:
    """WORK/ repo root (stable even if the shell cwd is elsewhere)."""
    return Path(__file__).resolve().parents[2]


def outlook_storage_path() -> Path:
    """Session file path; relative paths resolve against the repo root."""
    settings = get_settings()
    raw = (settings.outlook_storage_state or "data/outlook_auth.json").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo_root() / path
    return path


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
        f"After signup, on macOS/Linux run: ./aio\n"
        f"On Windows PowerShell run: .\\aio.cmd\n"
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
            "(Do not use Homebrew `playwright` - use the venv command above.)"
        )

    with sync_playwright() as p:
        exe = Path(p.chromium.executable_path)
    if not exe.is_file():
        raise RuntimeError(
            f"Chromium still missing at {exe}.\n"
            "Run: .venv/bin/python -m playwright install chromium"
        )
    return cache


def _outlook_looks_logged_out(page) -> bool:
    url = (page.url or "").lower()
    if any(x in url for x in ("login.microsoftonline.com", "login.live.com", "login.microsoft.com")):
        return True
    return False


def _fill_compose_fields(page, *, to: str, subject: str, body: str) -> None:
    """Best-effort fill when the compose deeplink leaves fields empty."""

    def _type_into(selectors: tuple[str, ...], text: str, *, clear: bool = True) -> bool:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if not loc.count():
                    continue
                loc.click(timeout=2000)
                if clear:
                    loc.fill("")  # may no-op on contenteditable
                    page.keyboard.press("Meta+A")
                    page.keyboard.press("Backspace")
                page.keyboard.type(text, delay=15)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    _type_into(
        (
            'input[aria-label="To"]',
            'div[aria-label="To"] [contenteditable="true"]',
            'div[aria-label="To"]',
            '[aria-label="To"]',
        ),
        to,
    )
    page.keyboard.press("Tab")
    _type_into(
        (
            'input[aria-label="Subject"]',
            'input[placeholder="Add a subject"]',
            '[aria-label="Subject"]',
        ),
        subject,
    )
    _type_into(
        (
            'div[aria-label="Message body"]',
            'div[aria-label="Message body, press Alt+F10 to exit"]',
            'div[role="textbox"][aria-label*="Message"]',
            'div[contenteditable="true"][aria-label*="Message"]',
        ),
        body,
        clear=True,
    )


def _try_click_send(page) -> bool:
    for selector in (
        'button[aria-label="Send"]',
        'button[title="Send"]',
        '[data-automation-id="sendButton"]',
        'button:has-text("Send")',
    ):
        try:
            btn = page.locator(selector).first
            if btn.count() and btn.is_visible(timeout=2500):
                btn.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _confirm_outlook_sent(page, *, timeout_ms: int = 12_000) -> bool:
    """True only when Outlook shows a real sent signal (never assume)."""
    import time

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    toast_needles = (
        "Message sent",
        "Your message was sent",
        "Email sent",
    )
    while time.monotonic() < deadline:
        if _outlook_looks_logged_out(page):
            return False
        for text in toast_needles:
            try:
                loc = page.get_by_text(text, exact=False).first
                if loc.is_visible(timeout=300):
                    return True
            except Exception:  # noqa: BLE001
                continue
        # Compose deeplink usually closes / leaves compose after a real send.
        url = (page.url or "").lower()
        if "deeplink/compose" not in url and "/mail/" in url and "compose" not in url:
            return True
        page.wait_for_timeout(350)
    return False


def _show_manual_send_hint(page) -> None:
    """Visible banner in the Chromium window so the host knows to click Send."""
    try:
        page.evaluate(
            """() => {
              if (document.getElementById('aio-outlook-hint')) return;
              const el = document.createElement('div');
              el.id = 'aio-outlook-hint';
              el.textContent = 'AIO: click Send in Outlook (or Cmd+Enter). Waiting…';
              Object.assign(el.style, {
                position: 'fixed', top: '12px', left: '50%', transform: 'translateX(-50%)',
                zIndex: '2147483647', background: '#0f172a', color: '#f8fafc',
                padding: '10px 16px', borderRadius: '8px', font: '14px/1.4 system-ui,sans-serif',
                boxShadow: '0 8px 24px rgba(0,0,0,.35)', pointerEvents: 'none'
              });
              document.documentElement.appendChild(el);
            }"""
        )
    except Exception:  # noqa: BLE001
        pass


def send_invite_via_outlook(
    *,
    to_email: str,
    invite_url: str,
    max_uses: int = 1,
    workspace: str = "AIO",
    headless: bool | None = None,
) -> dict:
    """Compose + send an Outlook mail. Never raises - result is in the returned dict."""
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
            "reason": (
                f"Outlook session missing ({storage}). "
                "From the WORK folder run: ./aio outlook-login "
                "(not ./aio run outlook-login)"
            ),
        }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "skipped": False,
            "reason": (
                "playwright not installed - "
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
    # Default visible browser so you can watch / click Send yourself.
    use_headless = settings.outlook_headless if headless is None else headless
    timeout_ms = int(settings.outlook_timeout_seconds * 1000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=use_headless)
            context = browser.new_context(storage_state=str(storage))
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3500)

            if _outlook_looks_logged_out(page):
                browser.close()
                return {
                    "ok": False,
                    "skipped": False,
                    "to": to,
                    "reason": (
                        "Outlook session expired — run ./aio outlook-login again, "
                        "then remint !invite"
                    ),
                }

            try:
                _fill_compose_fields(page, to=to, subject=subject, body=body)
            except Exception:  # noqa: BLE001
                pass

            clicked = _try_click_send(page)
            if not clicked:
                page.keyboard.press("Meta+Enter")
                page.wait_for_timeout(400)
                page.keyboard.press("Control+Enter")

            # Short auto-confirm window first.
            confirmed = _confirm_outlook_sent(page, timeout_ms=max(6_000, min(timeout_ms, 12_000)))

            # Headed: leave the window up so the host can click Send manually.
            if not confirmed and not use_headless:
                _show_manual_send_hint(page)
                log.info("Waiting for manual Outlook Send to %s (up to 2 minutes)", to)
                print(
                    "\nAIO: Chromium is open — click Send in Outlook (or press Cmd+Enter).\n"
                    "Waiting up to 2 minutes for confirmation…\n",
                    flush=True,
                )
                confirmed = _confirm_outlook_sent(page, timeout_ms=120_000)

            context.storage_state(path=str(storage))
            browser.close()

        if not confirmed:
            return {
                "ok": False,
                "skipped": False,
                "to": to,
                "reason": (
                    "Outlook did not confirm send. In the Chromium window click Send "
                    "(or Cmd+Enter), or share the join link from chat — it still works."
                ),
                "headless": use_headless,
            }

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
