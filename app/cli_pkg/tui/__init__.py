"""AIO as a full-screen terminal app: chat, board, agents, dashboard."""

from __future__ import annotations

__all__ = ["run_app", "run_tui"]


def run_app(*args, **kwargs):  # pragma: no cover - thin re-export
    from app.cli_pkg.tui.app import run_app as _run

    return _run(*args, **kwargs)


def run_tui(*args, **kwargs):  # pragma: no cover - thin re-export
    from app.cli_pkg.tui.app import run_app as _run

    return _run(*args, **kwargs)
