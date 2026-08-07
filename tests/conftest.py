"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _relax_invite_domain(monkeypatch):
    """Demo tests use @local.test; production defaults to @tatweermea.com."""
    monkeypatch.setenv("INVITE_ALLOWED_DOMAIN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
