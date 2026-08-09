"""Tour step lists: member shared path + owner extras."""

from __future__ import annotations

from app.cli_pkg.tui.tutorial import MEMBER_STEPS, OWNER_EXTRA_STEPS, build_tour_steps


def test_member_tour_excludes_owner_steps():
    steps = build_tour_steps(is_owner=False)
    assert steps == MEMBER_STEPS
    assert all(not s.owner_only for s in steps)
    ids = {s.id for s in steps}
    assert "dash" not in ids and "live" not in ids and "people" not in ids
    assert "owner-board" not in ids
    # Chrome covered for every member
    assert {"attach", "voice", "edit", "logout", "new-channel", "pings", "agents", "board"} <= ids


def test_owner_tour_appends_extras():
    steps = build_tour_steps(is_owner=True)
    assert steps[: len(MEMBER_STEPS)] == MEMBER_STEPS
    assert steps[len(MEMBER_STEPS) :] == OWNER_EXTRA_STEPS
    assert len(steps) == len(MEMBER_STEPS) + len(OWNER_EXTRA_STEPS)
    assert any(s.id == "dash" for s in steps)
    assert any(s.id == "live" for s in steps)
    assert any(s.id == "people" for s in steps)
    assert any(s.id == "owner-board" for s in steps)
    assert all(s.owner_only for s in OWNER_EXTRA_STEPS)


def test_tour_steps_have_spotlights():
    for s in build_tour_steps(is_owner=True):
        assert s.title
        assert s.body
        assert len(s.body) <= 120
        # Mentions step explains @N next to Tour — no spotlight on purpose
        if s.id == "pings":
            assert s.spotlight is None
            assert "tour" in s.body.lower()
            continue
        assert s.spotlight  # every other planned step has a target
    by_id = {s.id: s for s in MEMBER_STEPS}
    assert by_id["tabs"].spotlight == "#tabs-row"
    assert by_id["type"].spotlight == "#composer-row"
    assert by_id["attach"].spotlight == "#chat-attach"
    assert by_id["voice"].spotlight == "#chat-mic"
    assert by_id["edit"].spotlight == "#transcript"
    assert by_id["pings"].spotlight is None
    assert by_id["board"].spotlight == "#columns"
    assert by_id["agents"].spotlight == "#agents"
    assert by_id["logout"].spotlight == "#logout-btn"
    assert by_id["new-channel"].spotlight == "#chat-new"
    assert "press a" in by_id["board"].body.lower() or " a " in f" {by_id['board'].body} "
    assert "codex" in by_id["board"].body.lower()
    assert "!claude" in by_id["type"].body.lower()


def test_tour_spotlight_targets_exist_in_app():
    """Smoke: member + owner spotlights resolve after boot."""
    import asyncio

    import pytest

    pytest.importorskip("textual")

    async def _run() -> None:
        from app.cli_pkg.prefs import mark_tutorial_done
        from app.cli_pkg.tui.app import AioApp
        from tests.test_tui_app import _StubClient

        mark_tutorial_done("a@local.test")
        app = AioApp(_StubClient(owner=True), poll_seconds=60.0)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()
            for step in build_tour_steps(is_owner=True):
                if not step.spotlight:
                    continue
                # Owner-only tabs must be reachable before query
                if step.tab:
                    app.show_tab(step.tab)
                    await pilot.pause()
                node = app.query_one(step.spotlight)
                assert node is not None, step.spotlight

    asyncio.run(_run())
