"""Headless smoke test: drive the terminal app like a user and dump each tab.

    .venv/bin/python scripts/tui_smoke.py [--shot]

Requires the API running and credentials in ~/.aio/credentials.json.
"""

from __future__ import annotations

import asyncio
import sys

from app.cli_pkg.session import load_credentials, resolve_project_id
from app.cli_pkg.tui.app import AioApp
from app.cli_pkg.tui.client import ApiClient

SHOT = "--shot" in sys.argv


async def main() -> int:
    creds = load_credentials()
    client = ApiClient(
        project_id=int(resolve_project_id() or 1), api_key=creds.api_key, email=creds.email
    )
    app = AioApp(client, poll_seconds=1.0)
    problems: list[str] = []

    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await asyncio.sleep(3)
        await pilot.pause()

        chat = app.chat_view
        print(f"chats={len(chat.chats)} members={len(chat.members)} selected={chat.chat_id}")
        print(f"messages rendered={len(chat._views)}")
        if not chat.chats:
            problems.append("no chats loaded")
        if not chat._views:
            problems.append("no messages rendered")

        for key in ("board", "agents", "dashboard", "chat"):
            app.show_tab(key)
            await pilot.pause()
            await asyncio.sleep(2.0)
            await pilot.pause()
            print(f"--- tab {key}: current={app.switcher.current}")
            if app.switcher.current != key and not (key == "dashboard" and not app.ws.is_owner):
                problems.append(f"tab {key} did not activate")
            if SHOT:
                app.save_screenshot(f"/tmp/aio-{key}.svg")

        cards = sum(len(c.list_view.children) for c in app.board_view.columns.values())
        print(f"board cards={cards} agent_working={app.board_view.agent_working}")
        if not cards:
            problems.append("board rendered no cards")

        print(f"agent rows={len(app.agents_view.selects)}")
        if not app.agents_view.selects:
            problems.append("agents view empty")

        print(f"dashboard people rows={app.dashboard_view.people.row_count}")
        if app.ws.is_owner and app.dashboard_view.people.row_count == 0:
            problems.append("dashboard has no people")

        app.show_tab("board")
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("l")
        await pilot.pause()
        print(f"board selection={app.board_view.current_card and app.board_view.current_card['id']}")

        app.show_tab("chat")
        await pilot.pause()
        await pilot.pause()
        if not app.chat_view.composer.has_focus:
            problems.append(f"composer not focused (focus={app.focused})")
        await pilot.press("h", "i")
        await pilot.pause()
        if app.chat_view.composer.value != "hi":
            problems.append(f"typing went to bindings, not the composer: {app.chat_view.composer.value!r}")
        app.chat_view.composer.value = ""

    print("\nPROBLEMS:" if problems else "\nOK: no problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


raise SystemExit(asyncio.run(main()))
