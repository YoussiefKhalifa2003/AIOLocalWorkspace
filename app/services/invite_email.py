"""Optional SMTP invite emails for personal addresses."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import get_settings


def send_invite_email(*, to_email: str, inviter_email: str, join_key: str) -> tuple[bool, str]:
    """Returns (sent, detail). Never raises — invite should still succeed without mail."""
    settings = get_settings()
    host = (settings.smtp_host or "").strip()
    user = (settings.smtp_user or "").strip()
    password = (settings.smtp_password or "").strip()
    if not host or not user or not password:
        return False, "SMTP not configured (set SMTP_HOST/USER/PASSWORD in .env to email invites)"

    from_addr = (settings.smtp_from or user).strip()
    app_url = (settings.invite_app_url or settings.api_base_url or "http://127.0.0.1:8000").rstrip("/")
    if not app_url.endswith("/app"):
        app_url = f"{app_url}/app"

    msg = EmailMessage()
    msg["Subject"] = "You're invited to AIO"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(
        f"{inviter_email} invited you to an AIO workspace.\n\n"
        f"Open: {app_url}\n"
        f"Email: {to_email}\n"
        f"Join key: {join_key}\n\n"
        "Use that email + join key on the login screen.\n"
    )

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
