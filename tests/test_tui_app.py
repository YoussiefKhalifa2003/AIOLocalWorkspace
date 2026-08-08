"""The terminal app: client surface, rendering rules, tabs, and gating."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.cli_pkg.tui.client import (
    ApiClient,
    ApiError,
    RingBuffer,
    Workspace,
    live_fingerprint,
    login,
)
from app.cli_pkg.tui.views.chat import (
    active_prefix,
    candidates_for,
    looks_like_agent_work,
    render_markdown,
)
from textual.widgets import Tab, Tabs

@pytest.fixture(autouse=True)
def _isolate_aio_prefs(tmp_path, monkeypatch):
    monkeypatch.setenv("AIO_PREFS", str(tmp_path / "prefs.json"))


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


def test_edit_and_delete_message_shapes(mock_http):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content) if request.content else {}
        seen.append({"method": request.method, "path": request.url.path, "body": body})
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "message": {"id": 5, "body": "edited"},
                    "removed_ids": [6, 7],
                    "replies": [{"id": 8, "body": "ok", "agent": "ask"}],
                },
            )
        return httpx.Response(
            200, json={"message": {"id": 5, "deleted_at": "x"}, "removed_ids": [6]}
        )

    mock_http(handler)
    client = ApiClient(project_id=1, api_key="k", base_url="http://api")
    edited = client.edit_message(3, 5, "edited")
    assert edited["removed_ids"] == [6, 7]
    deleted = client.delete_message(3, 5)
    assert deleted["removed_ids"] == [6]
    assert seen[0]["method"] == "PATCH"
    assert seen[0]["path"] == "/chats/3/messages/5"
    assert seen[0]["body"] == {"body": "edited"}
    assert seen[1]["method"] == "DELETE"


def test_transcribe_posts_multipart(tmp_path, mock_http):
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFF....WAVE")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stt"
        assert b"voice.wav" in request.content
        return httpx.Response(200, json={"text": "hello team"})

    mock_http(handler)
    text = ApiClient(project_id=1, api_key="k", base_url="http://api").transcribe(wav)
    assert text == "hello team"


def test_mention_open_marks_single_id(mock_http):
    """Opening one mention should POST only that mention id (not mark-all)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/mentions/read"):
            import json as _json

            seen["body"] = _json.loads(request.content or b"{}")
            return httpx.Response(200, json={"status": "ok", "marked": 1})
        return httpx.Response(404, json={"detail": "no"})

    mock_http(handler)
    client = ApiClient(project_id=1, api_key="k", base_url="http://api")
    out = client.mark_mentions_read([42])
    assert out.get("status") == "ok"
    assert seen["body"] == {"ids": [42]}


def test_resolve_attach_path_and_upload(tmp_path, monkeypatch, mock_http):
    from app.cli_pkg.tui.client import resolve_attach_path

    missing = tmp_path / "nope.py"
    with pytest.raises(ApiError, match="not found"):
        resolve_attach_path(missing)

    py = tmp_path / "sample.py"
    py.write_text("x = 1\n", encoding="utf-8")
    assert resolve_attach_path(py) == py.resolve()
    assert resolve_attach_path(f'"{py}"') == py.resolve()

    monkeypatch.chdir(tmp_path)
    assert resolve_attach_path("sample.py").name == "sample.py"

    with pytest.raises(ApiError, match="not a file"):
        resolve_attach_path(tmp_path)

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        assert b"sample.py" in request.content or b"filename" in request.headers.get(
            "content-type", ""
        ).encode()
        # multipart body contains filename
        assert b"sample.py" in request.content
        return httpx.Response(
            200,
            json={
                "id": 9,
                "filename": "sample.py",
                "content_type": "text/x-python",
                "url": "/attachments/9",
            },
        )

    mock_http(handler)
    out = ApiClient(project_id=1, api_key="k", base_url="http://api").upload_attachment(3, py)
    assert seen["method"] == "POST"
    assert seen["path"] == "/chats/3/attachments"
    assert out["id"] == 9
    assert out["filename"] == "sample.py"


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


def test_jobs_summary_returns_full_dict(mock_http):
    mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "project_id": 1,
                "total": 4,
                "by_status": {"done": 3},
                "by_model": [{"model": "m", "total": 4, "done": 3, "failed": 1}],
            },
        )
    )
    client = ApiClient(project_id=1, api_key="k", base_url="http://api")
    data = client.jobs_summary()
    assert data["total"] == 4
    assert data["by_status"]["done"] == 3
    assert client.jobs_total() == 4


def test_ring_buffer_caps_and_preserves_order():
    buf = RingBuffer(3)
    buf.extend([1, 2, 3, 4])
    assert buf.values() == [2.0, 3.0, 4.0]
    buf.append(5)
    assert buf.values() == [3.0, 4.0, 5.0]


def test_live_fingerprint_changes_when_tokens_move():
    a = {"summary": {"tokens_total": 1}, "people": [], "models": []}
    s = {"buckets": {"tokens": [1]}}
    cols = {"todo": 1}
    jobs = {"total": 1, "by_status": {}}
    first = live_fingerprint(a, s, cols, jobs)
    a2 = {"summary": {"tokens_total": 2}, "people": [], "models": []}
    assert first != live_fingerprint(a2, s, cols, jobs)


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


def test_markdown_ordered_list_and_hr():
    out = render_markdown("1. first\n2. second\n---\nmore")
    assert "1." in out
    assert "2." in out
    assert "────" in out


def test_markdown_simple_table():
    out = render_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert "│" in out
    assert "a" in out
    assert "1" in out


def test_message_grouping_same_speaker_stacks():
    from app.cli_pkg.tui.views.chat import should_group_with_previous

    a = {
        "id": 1,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:00:00Z",
        "body": "hi",
    }
    b = {
        "id": 2,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:00:30Z",
        "body": "again",
    }
    c = {
        "id": 3,
        "sender_email": "b@x.test",
        "created_at": "2026-08-08T16:01:00Z",
        "body": "other",
    }
    agent = {
        "id": 4,
        "agent": "ask",
        "created_at": "2026-08-08T16:01:10Z",
        "body": "ok",
    }
    assert should_group_with_previous(None, a) is False
    assert should_group_with_previous(a, b) is True
    assert should_group_with_previous(b, c) is False
    assert should_group_with_previous(c, agent) is False
    # Still same speaker, but under 4 minutes → still grouped
    almost = {
        "id": 5,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:03:59Z",
        "body": "almost 4m",
    }
    assert should_group_with_previous(a, almost) is True
    # Same speaker after 4+ minutes alone → new block
    later = {
        "id": 6,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:04:01Z",
        "body": "new block",
    }
    assert should_group_with_previous(a, later) is False


def test_group_messages_block_boundaries():
    from app.cli_pkg.tui.views.chat import blocks_fingerprint, group_messages

    a = {
        "id": 1,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:00:00Z",
        "body": "hi",
    }
    b = {
        "id": 2,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:00:30Z",
        "body": "again",
    }
    c = {
        "id": 3,
        "sender_email": "b@x.test",
        "created_at": "2026-08-08T16:01:00Z",
        "body": "other",
    }
    agent = {
        "id": 4,
        "agent": "ask",
        "created_at": "2026-08-08T16:01:10Z",
        "body": "ok",
    }
    later = {
        "id": 5,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:10:00Z",
        "body": "new block",
    }
    groups = group_messages([a, b, c, agent, later])
    assert [[m["id"] for m in g] for g in groups] == [[1, 2], [3], [4], [5]]
    fp1 = blocks_fingerprint(groups)
    # Body-only edits must not change the structure fingerprint (poll early-return).
    b2 = {**b, "body": "edited"}
    fp2 = blocks_fingerprint(group_messages([a, b2, c, agent, later]))
    assert fp1 == fp2
    # New message id changes fingerprint
    extra = {
        "id": 6,
        "sender_email": "a@x.test",
        "created_at": "2026-08-08T16:10:05Z",
        "body": "cont",
    }
    fp3 = blocks_fingerprint(group_messages([a, b, c, agent, later, extra]))
    assert fp3 != fp1


def test_color_for_member_stable_and_distinct():
    from app.cli_pkg.tui.views.chat import color_for_member, color_for_message

    a = color_for_member("sara@x.test")
    b = color_for_member("sara@x.test")
    assert a == b
    assert a.startswith("#") and len(a) == 7
    emails = ["sara@x.test", "mo@x.test", "ooo@x.test"]
    assert color_for_member("sara@x.test", member_emails=emails) != color_for_member(
        "mo@x.test", member_emails=emails
    )
    assert color_for_message({"agent": "coding"}) == "#44aaff"
    assert color_for_message({"sender_email": "sara@x.test"}, member_emails=emails) == (
        color_for_member("sara@x.test", member_emails=emails)
    )


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
    items = candidates_for("/", "", members=[], chat_kind="private", chat_mode="llm")
    labels = [c.label for c in items]
    assert labels[:3] == ["/ask", "/deepresearch", "/code"]
    assert "/checklist" in labels
    assert all(c.blurb for c in items), "every row explains itself"


def test_slash_menu_ops_chat_only_offers_clear():
    labels = [
        c.label
        for c in candidates_for("/", "", members=[], chat_kind="channel", chat_mode="ops")
    ]
    assert labels == ["/clear"]


def test_slash_menu_llm_channel_offers_skills():
    labels = [
        c.label
        for c in candidates_for("/", "", members=[], chat_kind="channel", chat_mode="llm")
    ]
    assert "/ask" in labels
    assert "/clear" in labels


def test_bang_menu_filters_as_you_type():
    labels = [c.label for c in candidates_for("!", "i", members=[], chat_kind="channel")]
    assert labels == ["!issue", "!issues", "!invite"]


def test_menu_closes_once_a_command_is_complete():
    assert candidates_for("!", "list", members=[], chat_kind="channel") == []
    assert candidates_for("/", "ask", members=[], chat_kind="private", chat_mode="llm") == []


def test_mention_menu_offers_team_and_people():
    items = candidates_for("@", "", members=["Alice", "omar@local.test"], chat_kind="channel")
    assert [c.label for c in items] == ["@team", "@Alice", "@omar"]
    items = candidates_for("@", "al", members=["Alice", "Omar"], chat_kind="channel")
    assert [c.label for c in items] == ["@Alice"]


def test_candidates_insert_a_trailing_space_so_you_can_keep_typing():
    only = candidates_for("/", "deep", members=[], chat_kind="private", chat_mode="llm")[0]
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
                {"id": "doing", "cards": []},
                {"id": "blocked", "cards": []},
                {"id": "agent_backlog", "cards": [{"id": 9, "title": "Agent"}]},
                {"id": "in_review", "cards": []},
                {"id": "done", "cards": []},
            ],
        }

    def jobs_summary(self):
        return {
            "project_id": 1,
            "total": 7,
            "by_status": {"done": 5, "failed": 1, "queued": 1},
            "by_model": [],
        }

    def jobs_total(self):
        return int(self.jobs_summary().get("total") or 0)

    def agent_models(self):
        return {
            "agents": ["ask", "coding"],
            "models": [{"id": "gemini-env", "label": "Gemini"}],
            "prefs": {"ask": "gemini-env", "coding": "gemini-env"},
            "gemini_configured": True,
        }

    def analytics(self):
        return {
            "summary": {
                "members": 2,
                "open_tasks": 3,
                "jobs_total": 7,
                "jobs_done": 5,
                "jobs_failed": 1,
                "tokens_total": 1200,
                "model_count": 1,
            },
            "people": [{"name": "Alice", "role": "owner", "jobs": 1, "tokens": 1200, "models": []}],
            "models": [
                {
                    "model": "gemini-env",
                    "backend": "gemini",
                    "runs": 7,
                    "tokens": 1200,
                    "success": 5,
                    "fail": 1,
                }
            ],
            "open_tasks": [],
        }

    def metrics_series(self, limit: int = 60):
        return {
            "project_id": 1,
            "points": [
                {"t": "2026-08-07T08:00:00Z", "tokens": 10, "duration_ms": 100, "success": True},
                {"t": "2026-08-07T08:01:00Z", "tokens": 40, "duration_ms": 200, "success": False},
                {"t": "2026-08-07T08:02:00Z", "tokens": 90, "duration_ms": 300, "success": True},
            ],
            "buckets": {
                "tokens": [10, 40, 90],
                "duration_ms": [100, 200, 300],
                "success_rate": [1.0, 0.0, 1.0],
            },
        }


async def _boot(owner: bool):
    from app.cli_pkg.prefs import mark_tutorial_done
    from app.cli_pkg.tui.app import AioApp

    # Avoid first-run Tour modal stealing focus in interactive pilots.
    mark_tutorial_done("a@local.test")
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
        attach = app.query_one("#chat-attach")
        assert str(attach.label) == "+"
        assert app.query_one("#chat-mic") is not None
        # + and mic live inside the unified composer shell
        row = app.query_one("#composer-row")
        kids = [c.id for c in row.children]
        assert kids[0] == "chat-attach"
        assert "composer" in kids
        assert kids[-1] == "chat-mic"
        assert app.query_one("#logout-btn") is not None
        assert app.query_one("#tour-btn") is not None
        # Speaker blocks (not per-row MessageView rails)
        from app.cli_pkg.tui.views.chat import MessageLine, SpeakerBlock

        assert app.chat_view.transcript.query(SpeakerBlock)
        assert isinstance(next(iter(app.chat_view._views.values())), MessageLine)

        # Same snapshot again must not remount MessageLines (fingerprint early-return).
        line_ids = {id(v) for v in app.chat_view._views.values()}
        stamp = app.chat_view._last_sync or "z"
        app.chat_view._apply_messages(
            int(app.chat_view.chat_id),
            list(app.chat_view._ordered_rows()),
            stamp,
        )
        await pilot.pause()
        assert {id(v) for v in app.chat_view._views.values()} == line_ids

        # New message must append in place — existing MessageLine widgets stay alive
        # (no blank flash from remove_children).
        before = dict(app.chat_view._views)
        max_id = max(before)
        app.chat_view._apply_messages(
            int(app.chat_view.chat_id),
            [
                {
                    "id": max_id + 1,
                    "body": "append me",
                    "sender": "Demo",
                    "sender_email": "a@local.test",
                    "created_at": "2026-08-08T18:00:00Z",
                }
            ],
            stamp,
        )
        await pilot.pause()
        for mid, view in before.items():
            assert app.chat_view._views.get(mid) is view
        assert max_id + 1 in app.chat_view._views

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

        app.show_tab("live")
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert app.switcher.current == "live"
        assert list(app.live_view.tokens_spark.data) == [10.0, 40.0, 90.0]
        app.live_view.stop_polling()


@pytest.mark.asyncio
async def test_owner_only_tabs_hidden_from_members():
    app = await _boot(owner=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        for tab in ("people", "dashboard", "live"):
            app.show_tab(tab)
            await pilot.pause()
            assert app.switcher.current != tab

        for tab in ("board", "agents", "chat"):
            app.show_tab(tab)
            await pilot.pause()
            assert app.switcher.current == tab

        tabs = app.query_one("#tabs", Tabs)
        for key in ("people", "dashboard", "live"):
            assert tabs.query_one(f"#{key}", Tab).display is False


@pytest.mark.asyncio
async def test_live_fingerprint_skips_noop_redraw():
    app = await _boot(owner=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.show_tab("live")
        await pilot.pause()
        await asyncio.sleep(0.4)
        await pilot.pause()
        first = app.live_view.snapshot_fingerprint()
        assert first
        before_wip = len(app.live_view._wip_ring)
        # Second apply with identical data should keep the fingerprint.
        app.live_view._apply(
            app.client.analytics(),
            app.client.metrics_series(),
            app.client.board(),
            app.client.jobs_summary(),
            "",
        )
        assert app.live_view.snapshot_fingerprint() == first
        # WIP ring still advances even on a no-op fingerprint path.
        assert len(app.live_view._wip_ring) == before_wip + 1
        app.live_view.stop_polling()


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
