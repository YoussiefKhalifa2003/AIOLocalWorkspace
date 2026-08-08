"""Seed a polished #general thread for README screenshots, then capture the hero.

Requires API on :8000.

    .venv/Scripts/python.exe scripts/capture_hero.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_SVG = ROOT / "docs" / "aio-hero.svg"
OUT_PNG = ROOT / "docs" / "aio-hero.png"

# Short, product-shaped standup that reads well in a README hero.
HERO_CHAT: list[tuple[str, str]] = [
    ("sara@local.test", "standup in 5. anything blocking merge?"),
    ("a@local.test", "PR is green. Need a quick look at the invite flow?"),
    ("sara@local.test", "send it. I'll review after this"),
    ("a@local.test", "@sara it's up. I'll take agent backlog next."),
    ("sara@local.test", "looks good. merge when ready."),
]


def _client_for(email: str):
    from app.cli_pkg.tui.client import ApiClient, login

    data = login(email, "demo")
    return ApiClient(
        project_id=1,
        api_key=str(data.get("api_key") or ""),
        email=str(data.get("email") or email),
    )


def _seed_hero_chat() -> tuple[object, int]:
    """Replace #general history with HERO_CHAT. Returns (owner client, chat_id)."""
    from datetime import UTC, datetime

    from app.db.models import Chat, ChatMessage
    from app.db.session import SessionLocal

    owner = _client_for("a@local.test")
    chats = owner.get("/chats")
    general = next(
        (c for c in chats if c.get("name") == "general" and c.get("kind") == "channel"),
        None,
    )
    if general is None:
        raise SystemExit("no #general chat — run: aio seed")
    chat_id = int(general["id"])

    # Soft-delete existing public lines so the hero isn't noisy.
    db = SessionLocal()
    try:
        chat = db.query(Chat).filter(Chat.id == chat_id).one()
        now = datetime.now(UTC)
        db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat.id,
            ChatMessage.deleted_at.is_(None),
        ).update({ChatMessage.deleted_at: now}, synchronize_session=False)
        db.commit()
    finally:
        db.close()

    # Post as each speaker so names/colors look real.
    clients = {
        "a@local.test": owner,
        "sara@local.test": _client_for("sara@local.test"),
    }
    for email, body in HERO_CHAT:
        clients[email].post(
            f"/chats/{chat_id}/messages",
            json={"body": body, "speak": False},
        )
        time.sleep(0.15)

    print(f"seeded {len(HERO_CHAT)} messages into #general")
    return owner, chat_id


async def _capture(client, chat_id: int) -> Path:
    from app.cli_pkg.tui.app import AioApp

    app = AioApp(client, poll_seconds=60.0)
    async with app.run_test(size=(148, 42)) as pilot:
        await pilot.pause()
        await asyncio.sleep(2.5)
        await pilot.pause()

        chat = app.chat_view
        chat.select_chat(int(chat_id))
        await asyncio.sleep(1.5)
        await pilot.pause()
        app.show_tab("chat")
        await pilot.pause()

        try:
            chat.composer.focus()
        except Exception:
            pass
        await pilot.pause()

        OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(OUT_SVG))
        print(f"wrote {OUT_SVG}")

    return OUT_SVG


def _svg_to_png(svg_path: Path, png_path: Path) -> bool:
    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=1600)
        print(f"wrote {png_path} (cairosvg)")
        return True
    except Exception as exc:
        print(f"cairosvg unavailable ({exc})")

    browsers = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    for browser in browsers:
        if not browser.is_file():
            continue
        uri = svg_path.resolve().as_uri()
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--window-size=1600,1000",
            f"--screenshot={png_path}",
            uri,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            time.sleep(0.5)
            if png_path.is_file() and png_path.stat().st_size > 1000:
                print(f"wrote {png_path} ({browser.name})")
                return True
        except Exception as exc:
            print(f"{browser.name} failed: {exc}")
    return False


def main() -> int:
    client, chat_id = _seed_hero_chat()
    me = client.me()
    print(f"capturing as {me.get('email')}")
    svg = asyncio.run(_capture(client, chat_id))
    if not svg.is_file() or svg.stat().st_size < 500:
        print("screenshot failed", file=sys.stderr)
        return 1
    if not _svg_to_png(svg, OUT_PNG):
        print("PNG rasterize failed - README can still use the SVG", file=sys.stderr)
        return 0
    frame = ROOT / "scripts" / "frame_hero.py"
    subprocess.run([sys.executable, str(frame)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
