"""Workspace invite links: N-use tokens (default 1), mint via !invite / !invite N."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Tenant

MAX_INVITE_USES = 50


def invite_public_base_url() -> str:
    """Public base for join links. Ensures a port (defaults to :8000)."""
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
        port = 8000
    netloc = f"{host}:{port}"
    return urlunparse((scheme, netloc, "", "", "", ""))


def clamp_invite_uses(n: int | None) -> int:
    try:
        uses = int(n if n is not None else 1)
    except (TypeError, ValueError):
        uses = 1
    return max(1, min(uses, MAX_INVITE_USES))


def rotate_invite_token(db: Session, tenant: Tenant, *, max_uses: int = 1) -> str:
    uses = clamp_invite_uses(max_uses)
    tenant.invite_token = secrets.token_urlsafe(18)
    tenant.invite_max_uses = uses
    tenant.invite_uses_left = uses
    db.flush()
    return tenant.invite_token  # type: ignore[return-value]


def mint_invite_link(
    db: Session,
    tenant: Tenant,
    *,
    max_uses: int = 1,
    email: str | None = None,
    send_email: bool = False,
) -> dict:
    """Create a new invite link with max_uses seats (invalidates any previous link).

    When send_email=True, Outlook Web (Playwright) delivers the link. If
    INVITE_ALLOWED_DOMAIN is set, the address must match that domain. Failures
    are returned in the `outlook` field — the link is still minted.
    """
    uses = clamp_invite_uses(max_uses)
    token = rotate_invite_token(db, tenant, max_uses=uses)
    url = f"{invite_public_base_url()}/join/{token}"
    from app.services.teams_notify import notify_invite_link

    workspace = tenant.name or "AIO"
    teams = notify_invite_link(
        invite_url=url,
        max_uses=uses,
        workspace=workspace,
    )
    outlook: dict = {"ok": False, "skipped": True, "reason": "not requested"}
    if send_email:
        from app.services.outlook_invite import send_invite_via_outlook

        outlook = send_invite_via_outlook(
            to_email=email or "",
            invite_url=url,
            max_uses=uses,
            workspace=workspace,
        )
    return {
        "invite_url": url,
        "token": token,
        "tenant_id": tenant.id,
        "max_uses": uses,
        "uses_left": uses,
        "single_use": uses == 1,
        "teams": teams,
        "outlook": outlook,
        "emailed_to": (outlook.get("to") if outlook.get("ok") else None),
    }


def consume_invite_token(db: Session, tenant: Tenant, token: str) -> None:
    """Consume one seat after successful registration; clear token when exhausted."""
    if (tenant.invite_token or "").strip() != (token or "").strip():
        return
    left = tenant.invite_uses_left
    if left is None:
        left = 1
    left = max(0, int(left) - 1)
    tenant.invite_uses_left = left
    if left <= 0:
        tenant.invite_token = None
        tenant.invite_uses_left = 0
    db.flush()


def tenant_by_invite_token(db: Session, token: str) -> Tenant | None:
    token = (token or "").strip()
    if not token:
        return None
    tenant = db.query(Tenant).filter(Tenant.invite_token == token).one_or_none()
    if tenant is None:
        return None
    left = tenant.invite_uses_left
    if left is not None and int(left) <= 0:
        return None
    return tenant


def invite_link_for_tenant(db: Session, tenant: Tenant) -> dict:
    return mint_invite_link(db, tenant, max_uses=1)
