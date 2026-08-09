"""Phase 4: credential store, CLI request shapes, TUI owner gate + diffing."""

from __future__ import annotations

import json
import os
import stat

import httpx
import pytest


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AIO_CREDENTIALS", str(tmp_path / "creds.json"))
    monkeypatch.setenv("API_BASE_URL", "http://testserver")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_credentials_round_trip_and_permissions(tmp_path):
    from app.cli_pkg.session import (
        Credentials,
        clear_credentials,
        credentials_path,
        load_credentials,
        save_credentials,
    )

    assert load_credentials().is_empty()

    creds = Credentials(
        api_key="k-1", email="a@local.test", user_id=7, project_id=3, api_base_url="http://x"
    )
    path = save_credentials(creds)
    assert path == credentials_path()

    again = load_credentials()
    assert again.api_key == "k-1"
    assert again.email == "a@local.test"
    assert again.user_id == 7
    assert again.project_id == 3
    assert again.api_base_url == "http://x"

    from app.cli_pkg.session import resolve_base_url

    assert resolve_base_url(again) == "http://x"

    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert clear_credentials() is True
    assert load_credentials().is_empty()


def test_corrupt_credentials_do_not_crash(tmp_path):
    from app.cli_pkg.session import credentials_path, load_credentials

    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_credentials().is_empty()


def test_auth_headers_prefer_stored_then_override():
    from app.cli_pkg.session import Credentials, auth_headers, save_credentials

    save_credentials(Credentials(api_key="stored-key", email="me@local.test", user_id=1))
    assert auth_headers() == {"X-API-Key": "stored-key", "X-User-Email": "me@local.test"}
    assert auth_headers("other-key", "you@local.test") == {
        "X-API-Key": "other-key",
        "X-User-Email": "you@local.test",
    }


def _record_transport(recorder, payload=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": json.loads(request.content) if request.content else None,
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(status, json=payload if payload is not None else {})

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch, transport):
    import app.cli_pkg.main as cli

    def fake_client(timeout: float = 60.0):
        return httpx.Client(
            base_url="http://testserver", transport=transport, timeout=timeout
        )

    monkeypatch.setattr(cli, "_client", fake_client)


def _board_payload(**card_overrides):
    card = {
        "id": 5,
        "title": "Ship it",
        "status": "in_review",
        "progress_percent": 50,
        "owner_email": "a@local.test",
        "pr_url": "https://github.com/acme/widgets/pull/3",
        "pr_number": 3,
        "repo_url": "https://github.com/acme/widgets",
        "github_branch": "aio/obj-5",
        "branch_url": "https://github.com/acme/widgets/tree/aio/obj-5",
        "can_merge": True,
        "open_issue_count": 0,
        "checklist_closed": 1,
        "checklist_total": 2,
        "subtasks": [],
        "claimed_paths": [],
        "github_merged_at": None,
    }
    card.update(card_overrides)
    return {
        "project_id": 1,
        "github_repo": "acme/widgets",
        "repo_url": "https://github.com/acme/widgets",
        "columns": [{"id": "in_review", "cards": [card]}],
    }


def test_set_command_patches_status(monkeypatch):
    import app.cli_pkg.main as cli

    calls: list[dict] = []
    _patch_client(monkeypatch, _record_transport(calls, {"id": 5, "status": "agent_backlog"}))
    cli.set_status(5, "agent_backlog", project_id=1, runner="codex", api_key="k")

    assert calls[0]["method"] == "PATCH"
    assert calls[0]["path"] == "/projects/1/objectives/5"
    assert calls[0]["body"] == {"status": "agent_backlog", "coding_runner": "codex"}


def test_merge_command_sends_confirm_after_yes(monkeypatch):
    import app.cli_pkg.main as cli

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": json.loads(request.content) if request.content else None,
            }
        )
        if request.url.path.endswith("/board"):
            return httpx.Response(200, json=_board_payload())
        return httpx.Response(200, json={"ok": True, "base": "main", "sha": "abc1234"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    cli.merge_objective_cmd(5, project_id=1, method=None, keep_branch=False, yes=True, api_key="k")

    merge_call = calls[-1]
    assert merge_call["method"] == "POST"
    assert merge_call["path"] == "/projects/1/objectives/5/merge"
    assert merge_call["body"] == {"confirm": True, "delete_branch": True}


def test_merge_command_aborts_without_confirmation(monkeypatch):
    import typer

    import app.cli_pkg.main as cli

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"path": request.url.path})
        return httpx.Response(200, json=_board_payload())

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr(typer, "confirm", lambda *a, **kw: False)

    with pytest.raises(typer.Exit):
        cli.merge_objective_cmd(
            5, project_id=1, method=None, keep_branch=False, yes=False, api_key="k"
        )
    assert all(not c["path"].endswith("/merge") for c in calls)


def test_merge_command_refuses_card_without_pr(monkeypatch):
    import typer

    import app.cli_pkg.main as cli

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_board_payload(can_merge=False, pr_url=None, pr_number=None)
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(typer.Exit):
        cli.merge_objective_cmd(
            5, project_id=1, method=None, keep_branch=False, yes=True, api_key="k"
        )


def test_board_command_prints_links(monkeypatch, capsys):
    import app.cli_pkg.main as cli

    _patch_client(monkeypatch, _record_transport([], _board_payload()))
    cli.board_show(project_id=1, api_key="k")
    out = capsys.readouterr().out
    assert "repo=https://github.com/acme/widgets" in out
    assert "pr=https://github.com/acme/widgets/pull/3" in out
    assert "branch=aio/obj-5" in out


def test_board_fingerprint_is_stable_and_change_sensitive():
    from app.cli_pkg.tui.client import board_fingerprint

    a = _board_payload()
    b = _board_payload()
    assert board_fingerprint(a, 3) == board_fingerprint(b, 3)
    assert board_fingerprint(a, 3) != board_fingerprint(a, 4)

    changed = _board_payload(pr_url="https://github.com/acme/widgets/pull/9", pr_number=9)
    assert board_fingerprint(a, 3) != board_fingerprint(changed, 3)


def test_app_start_fails_cleanly_when_api_is_down(monkeypatch, capsys):
    """No stack trace, and never a half-drawn dashboard, if the API is off."""
    from app.cli_pkg.tui import client as tui_client
    from app.cli_pkg.tui.app import run_app

    monkeypatch.setattr(
        tui_client.ApiClient, "me", lambda self: (_ for _ in ()).throw(tui_client.ApiError("refused"))
    )
    assert run_app(1, api_key="k", email="a@local.test") == 2
    out = capsys.readouterr().out
    assert "cannot start" in out
    assert "uvicorn" in out or "API" in out or "aio" in out
