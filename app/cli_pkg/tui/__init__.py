"""Owner-only terminal dashboard for AIO."""

from __future__ import annotations

__all__ = ["run_tui"]


def run_tui(*args, **kwargs):  # pragma: no cover - thin re-export
    from app.cli_pkg.tui.app import run_tui as _run

    return _run(*args, **kwargs)
