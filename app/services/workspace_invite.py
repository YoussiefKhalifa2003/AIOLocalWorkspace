"""Workspace invite links: single-use tokens, mint a fresh one when needed."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Tenant


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


def rotate_invite_token(db: Session, tenant: Tenant) -> str:
    tenant.invite_token = secrets.token_urlsafe(18)
    db.flush()
    return tenant.invite_token  # type: ignore[return-value]


def mint_invite_link(db: Session, tenant: Tenant) -> dict:
    """Create a new single-use invite link (invalidates any previous unused link)."""
    token = rotate_invite_token(db, tenant)
    url = f"{invite_public_base_url()}/join/{token}"
    return {"invite_url": url, "token": token, "tenant_id": tenant.id, "single_use": True}


def consume_invite_token(db: Session, tenant: Tenant, token: str) -> None:
    """Invalidate the invite after successful registration."""
    if (tenant.invite_token or "").strip() == (token or "").strip():
        tenant.invite_token = None
        db.flush()


def tenant_by_invite_token(db: Session, token: str) -> Tenant | None:
    token = (token or "").strip()
    if not token:
        return None
    return db.query(Tenant).filter(Tenant.invite_token == token).one_or_none()


# Back-compat alias used by seed / older call sites
def invite_link_for_tenant(db: Session, tenant: Tenant) -> dict:
    return mint_invite_link(db, tenant)
