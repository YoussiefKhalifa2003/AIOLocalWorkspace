"""Optional invite email domain allowlist.

Empty INVITE_ALLOWED_DOMAIN = any valid email. When set (e.g. tatweermea.com),
only that domain may be invited or used to register via invite.
"""

from __future__ import annotations

from app.config import get_settings


def invite_allowed_domain() -> str:
    """Lowercase domain without @. Empty string disables the check (dev/tests)."""
    raw = (get_settings().invite_allowed_domain or "").strip().lower()
    if raw.startswith("@"):
        raw = raw[1:]
    return raw


def normalize_email(email: str) -> str:
    return (email or "").strip().lower().strip("<>\"'.,;:!?)(")


def email_domain(email: str) -> str:
    norm = normalize_email(email)
    if "@" not in norm:
        return ""
    return norm.rsplit("@", 1)[-1]


def is_allowed_invite_email(email: str) -> bool:
    """True when the address may receive an invite (or register via invite)."""
    domain = invite_allowed_domain()
    if not domain:
        return True
    return email_domain(email) == domain


def assert_allowed_invite_email(email: str) -> str:
    """Return normalized email or raise ValueError."""
    norm = normalize_email(email)
    if "@" not in norm or "." not in norm.rsplit("@", 1)[-1]:
        raise ValueError("invalid email")
    domain = invite_allowed_domain()
    if domain and email_domain(norm) != domain:
        raise ValueError(
            f"invites are restricted to @{domain} addresses only (got {norm})"
        )
    return norm
