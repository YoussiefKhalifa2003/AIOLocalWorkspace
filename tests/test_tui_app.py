"""The terminal app: client surface, rendering rules, tabs, and gating."""

from __future__ import annotations

import httpx
import pytest

from app.cli_pkg.tui.client import ApiClient, ApiError, Workspace, login
from app.cli_pkg.tui.views.chat import (
    active_prefix,
    candidates_for,
    looks_like_agent_work,
    render_markdown,
)

@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.Client through a handler the test provides."""

    def install(handler):
        original = httpx.Client.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched)

    return install


# client ------------------------------------------------------------------


def test_workspace_poll_collects_identity_chats_members_mentions(mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/me":
            return httpx.Response(200, json={"email": "a@local.test", "is_owner": True})
        if path == "/chats":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "general", "kind": "channel"},
                    {"id": 2, "name": "private - a", "kind": "private"},
                ],
            )
        if path == "/workspace/members":
            return httpx.Response(200, json=[{"user_id": 1, "email": "a@local.test", "role": "owner"}])
        if path == "/workspace/mentions":
            return httpx.Response(200, json={"unread": 2, "mentions": [{"id": 9, "chat_id": 1}]})
        return httpx.Response(404, json={"detail": "nope"})

    mock_http(handler)
    ws = ApiClient(project_id=1, api_key="k", base_url="http://api").workspace()

    assert ws.is_owner is True
    assert [c["name"] for c in ws.channels] == ["general"]
    assert [c["id"] for c in ws.rooms] == [2]
    assert ws.unread == 2
    assert ws.error == ""


def test_workspace_reports_error_rather_than_raising(mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    mock_http(handler)
    ws = ApiClient(project_id=1, api_key="k", base_url="http://api").workspace()
    assert ws.error
    assert ws.chats == []


def test_side_calls_degrade_but_identity_still_loads(mock_http):
    """A failing mentions endpoint must not blank out the whole app."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/me":
            return httpx.Response(200, json={"email": "a@local.test", "is_owner": False})
        return httpx.Response(500, json={"detail": "boom"})

    mock_http(handler)
    ws = ApiClient(project_id=1, api_key="k", base_url="http://api").workspace()
    assert ws.me["email"] == "a@local.test"
    assert ws.unread == 0
    assert ws.error == ""


def test_send_message_request_shape(mock_http):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.update({"path": request.url.path, "body": _json.loads(request.content)})
        return httpx.Response(200, json={"user_message_id": 5, "replies": []})

    mock_http(handler)
    ApiClient(project_id=1, api_key="k", base_url="http://api").send_message(3, "/ask hi")
    assert seen["path"] == "/chats/3/messages"
    assert seen["body"] == {"body": "/ask hi", "speak": False, "attachment_ids": []}


def test_messages_uses_after_id_and_since_cursors(mock_http):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    mock_http(handler)
    client = ApiClient(project_id=1, api_key="k", base_url="http://api")
    client.messages(3, after_id=12, since="2026-01-01T00:00:00Z")
    assert seen["after_id"] == "12"
    assert seen["since"] == "2026-01-01T00:00:00Z"


def test_save_agent_models_sends_bulk_prefs(mock_http):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.update({"method": request.method, "body": _json.loads(request.content)})
        return httpx.Response(200, json={"status": "ok"})

    mock_http(handler)
    ApiClient(project_id=1, api_key="k", base_url="http://api").save_agent_models(
        {"ask": "gemini-env", "coding": "x/y:free"}
    )
    assert seen["method"] == "PATCH"
    assert seen["body"] == {
        "prefs": [
            {"agent_type": "ask", "model_id": "gemini-env"},
            {"agent_type": "coding", "model_id": "x/y:free"},
        ]
    }


def test_api_error_carries_server_detail(mock_http):
    mock_http(lambda request: httpx.Response(403, json={"detail": "owner only"}))
    with pytest.raises(ApiError) as exc:
        ApiClient(project_id=1, api_key="k", base_url="http://api").analytics()
    assert "owner only" in str(exc.value)


def test_login_returns_api_key(mock_http):
    mock_http(lambda r: httpx.Response(200, json={"api_key": "abc", "email": "a@x", "user_id": 1}))
    assert login("a@x", "pw", "http://api")["api_key"] == "abc"


def test_login_error_is_readable(mock_http):
    mock_http(lambda r: httpx.Response(401, json={"detail": "bad credentials"}))
    with pytest.raises(ApiError) as exc:
        login("a@x", "nope", "http://api")
    assert "bad credentials" in str(exc.value)


# rendering ----------------------------------------------------------------


def test_markdown_becomes_terminal_markup():
    out = render_markdown("## Sources\n- **Bold** item\n`code`\n> quoted")
    assert "[b #7dd3fc]Sources[/]" in out
    assert "•" in out
    assert "[b]Bold[/b]" in out
    assert "**" not in out


def test_markdown_escapes_square_brackets_so_markup_cannot_be_injected():
    out = render_markdown("see [red]not-a-tag[/red] and [[confirm:3]]")
    assert "\\[red]" in out


def test_code_fences_are_marked_not_stripped():
    out = render_markdown("text\n```python\nx = 1\n```\nafter")
    assert "python" in out
    assert "x = 1" in out


@pytest.mark.parametrize(
    "body,kind,expected",
    [
        ("/ask what is up", "channel", True),
        ("/deepresearch topic", "private", True),
        ("/clear", "private", False),
        ("!add something", "channel", False),
        ("just chatting", "channel", False),
        ("/notaskill", "private", True),
        ("/notaskill", "channel", False),
        ("", "private", False),
    ],
)
def test_agent_work_detection(body, kind, expected):
    assert looks_like_agent_work(body, kind) is expected


# autocomplete dropdown ----------------------------------------------------


@pytest.mark.parametrize(
    "text,cursor,expected",
    [
        ("/dee", 4, ("/", 0, "dee")),
        ("hi @al", 6, ("@", 3, "al")),
        ("!", 1, ("!", 0, "")),
        ("say hello", 9, None),
        ("a@b.com", 7, None),  # an email is not a mention
        ("/ask now", 8, None),  # the token ended at the space
        ("/ask now", 2, ("/", 0, "a")),  # cursor back inside the token
    ],
)
def test_active_prefix(text, cursor, expected):
    assert active_prefix(text, cursor) == expected


def test_slash_menu_lists_every_skill_in_a_private_room():
    items = candidates_for("/", "", members=[], chat_kind="private")
    labels = [c.label for c in items]
    assert labels[:3] == ["/ask", "/deepresearch", "/code"]
    assert "/checklist" in labels
    assert all(c.blurb for c in items), "every row explains itself"


def test_slash_menu_in_a_channel_only_offers_what_works_there():
    labels = [c.label for c in candidates_for("/", "", members=[], chat_kind="channel")]
    assert labels == ["/status", "/clear"]


def test_bang_menu_filters_as_you_type():
    labels = [c.label for c in candidates_for("!", "i", members=[], chat_kind="channel")]
    assert labels == ["!issue", "!issues", "!invite"]


def test_menu_closes_once_a_command_is_complete():
    assert candidates_for("!", "list", members=[], chat_kind="channel") == []
    assert candidates_for("/", "ask", members=[], chat_kind="private") == []


def test_mention_menu_offers_team_and_people():
    items = candidates_for("@", "", members=["Alice", "omar@local.test"], chat_kind="channel")
    assert [c.label for c in items] == ["@team", "@Alice", "@omar"]
    items = candidates_for("@", "al", members=["Alice", "Omar"], chat_kind="channel")
    assert [c.label for c in items] == ["@Alice"]


def test_candidates_insert_a_trailing_space_so_you_can_keep_typing():
    only = candidates_for("/", "deep", members=[], chat_kind="private")[0]
    assert only.insert == "/deepresearch "


# app behaviour ------------------------------------------------------------


class _StubClient(ApiClient):
    def __init__(self, *, owner: bool) -> None:
        super().__init__(project_id=1, api_key="k", email="a@local.test", base_url="http://api")
        self._owner = owner

    def workspace(self) -> Workspace:
        return Workspace(
            me={"email": "a@local.test", "is_owner": self._owner},
            chats=[{"id": 1, "name": "general", "kind": "channel"}],
            members=[{"user_id": 1, "email": "a@local.test", "role": "owner"}],
        )

    def messages(self, chat_id, **kw):
        return [
            {
                "id": 1,
                "body": "hello **team**",
                "sender": "Alice",
                "sender_email": "a@local.test",
                "created_at": "2026-08-06T10:00:00Z",
            }
        ]

    def board(self):
        return {
            "github_repo": "acme/widgets",
            "columns": [
                {"id": "todo", "cards": [{"id": 5, "title": "Ship", "owner_email": "a@local.test"}]},
                {"id": "in_review", "cards": []},
            ],
        }

    def jobs_summary(self):
        return 7

    def agent_models(self):
        return {
            "agents": ["ask", "coding"],
            "models": [{"id": "gemini-env", "label": "Gemini"}],
            "prefs": {"ask": "gemini-env", "coding": "gemini-env"},
            "gemini_configured": True,
        }

    def analytics(self):
        return {
            "summary": {"members": 2, "tokens_total": 10},
            "people": [{"name": "Alice", "role": "owner", "jobs": 1, "tokens": 10, "models": []}],
            "models": [],
            "open_tasks": [],
        }


async def _boot(owner: bool):
    from app.cli_pkg.tui.app import AioApp

    app = AioApp(_StubClient(owner=owner), poll_seconds=60.0)
    return app


@pytest.mark.asyncio
async def test_app_renders_every_tab_for_the_owner():
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.chat_view.chats, "chat list should populate"
        assert app.chat_view._views, "messages should render"

        app.show_tab("board")
        await pilot.pause()
        assert app.switcher.current == "board"
        assert app.board_view.current_card["id"] == 5

        app.show_tab("agents")
        await pilot.pause()
        await pilot.pause()
        assert set(app.agents_view.selects) == {"ask", "coding"}

        app.show_tab("dashboard")
        await pilot.pause()
        await pilot.pause()
        assert app.switcher.current == "dashboard"
        assert app.dashboard_view.people.row_count == 1


@pytest.mark.asyncio
async def test_dashboard_is_owner_only_but_the_rest_of_the_app_is_not():
    app = await _boot(owner=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        app.show_tab("dashboard")
        await pilot.pause()
        assert app.switcher.current != "dashboard"

        for tab in ("board", "agents", "chat"):
            app.show_tab(tab)
            await pilot.pause()
            assert app.switcher.current == tab


@pytest.mark.asyncio
async def test_status_line_reports_repo_jobs_and_runner():
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        line = str(app.status_line.render())
        assert "acme/widgets" in line
        assert "jobs 7" in line
        assert "runner" in line


@pytest.mark.asyncio
async def test_typing_in_the_composer_does_not_trigger_board_shortcuts():
    """`j`, `m`, `a` are board keys; inside the chat box they must be letters."""
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.show_tab("chat")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("j", "a", "m")
        await pilot.pause()
        assert app.chat_view.composer.value == "jam"
        assert app.switcher.current == "chat"


@pytest.mark.asyncio
async def test_board_keys_move_the_selection():
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.show_tab("board")
        await pilot.pause()
        await pilot.pause()
        assert app.board_view.current_column.status == "todo"
        await pilot.press("l")
        await pilot.pause()
        assert app.board_view.current_column.status != "todo"
        await pilot.press("h")
        await pilot.pause()
        assert app.board_view.current_column.status == "todo"


@pytest.mark.asyncio
async def test_merge_key_refuses_a_card_without_a_pr():
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.show_tab("board")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert "not mergeable" in app._message
        assert len(app.screen_stack) == 1, "no confirm dialog for an unmergeable card"
