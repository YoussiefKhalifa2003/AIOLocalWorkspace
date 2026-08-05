"""Optional SMTP invite emails for personal addresses."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import get_settings


def invite_public_base_url() -> str:
    """Public base for Accept links. Ensures a port (defaults to :8000)."""
    from urllib.parse import urlparse, urlunparse

    settings = get_settings()
    raw = (settings.invite_app_url or settings.api_base_url or "http://127.0.0.1:8000").strip()
    if raw.endswith("/"):
        raw = raw.rstrip("/")
    if raw.endswith("/app"):
        raw = raw[:-4].rstrip("/")
    if not raw or raw in ("http:", "https:", "http://", "https://"):
        raw = "http://127.0.0.1:8000"
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    port = parsed.port
    if port is None:
        # Bare http://10.x.x.x without :8000 breaks Accept links (uvicorn is not on :80)
        port = 8000
    netloc = f"{host}:{port}"
    return urlunparse((scheme, netloc, "", "", "", ""))


def send_invite_email(
    *,
    to_email: str,
    inviter_email: str,
    accept_url: str,
) -> tuple[bool, str]:
    """Returns (sent, detail). Never raises - invite should still succeed without mail."""
    settings = get_settings()
    host = (settings.smtp_host or "").strip()
    user = (settings.smtp_user or "").strip()
    password = (settings.smtp_password or "").strip()
    if not host or not user or not password:
        return False, "SMTP not configured (set SMTP_HOST/USER/PASSWORD in .env - Outlook/M365)"

    from_addr = (settings.smtp_from or user).strip()
    app_url = f"{invite_public_base_url()}/app"

    msg = EmailMessage()
    msg["Subject"] = "You're invited to AIO - Accept invite"
    msg["From"] = from_addr
    msg["To"] = to_email
    plain = (
        f"{inviter_email} invited you to an AIO workspace.\n\n"
        f"Accept invite:\n{accept_url}\n\n"
        "You cannot log in until you accept.\n"
        f"After accepting, open {app_url} and sign in with this email + the join key shown on the accept page.\n"
    )
    html = f"""\
<html><body style="font-family:system-ui,sans-serif;line-height:1.5">
  <p><strong>{inviter_email}</strong> invited you to an AIO workspace.</p>
  <p><a href="{accept_url}" style="display:inline-block;padding:10px 16px;background:#1a73e8;color:#fff;text-decoration:none;border-radius:6px">Accept invite</a></p>
  <p style="color:#555;font-size:14px">Or open this link:<br>{accept_url}</p>
  <p style="color:#555;font-size:14px">You cannot log in until you accept. After accepting, open <a href="{app_url}">{app_url}</a> with this email and the join key shown on the accept page.</p>
</body></html>
"""
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        port = int(settings.smtp_port or 587)
        use_ssl = bool(settings.smtp_ssl)
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)
        return True, f"invite email sent to {to_email}"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not send email: {exc}"
