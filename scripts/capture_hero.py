"""Capture a real AIO TUI screenshot for the README hero.

Requires the API on :8000. Logs in as the demo owner (a@local.test).

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


async def _capture() -> Path:
    from app.cli_pkg.tui.app import AioApp
    from app.cli_pkg.tui.client import ApiClient, login

    data = login("a@local.test", "demo")
    client = ApiClient(
        project_id=1,
        api_key=str(data.get("api_key") or ""),
        email=str(data.get("email") or "a@local.test"),
    )
    me = client.me()
    print(f"signed in as {me.get('email') or data.get('email')}")

    chats = client.get("/chats")
    general = next(
        (c for c in chats if c.get("name") == "general" and c.get("kind") == "channel"),
        None,
    )
    if general is None:
        raise SystemExit("no #general chat — run: aio seed")

    # Keep the frame lively without spamming if messages already exist.
    msgs = client.get(f"/chats/{general['id']}/messages", params={"after_id": 0})
    rows = msgs if isinstance(msgs, list) else (msgs.get("messages") or [])
    if len(rows) < 2:
        client.post(
            f"/chats/{general['id']}/messages",
            json={"body": "standup in 5?", "speak": False},
        )

    app = AioApp(client, poll_seconds=60.0)
    async with app.run_test(size=(148, 42)) as pilot:
        await pilot.pause()
        await asyncio.sleep(2.5)
        await pilot.pause()

        chat = app.chat_view
        chat.select_chat(int(general["id"]))
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
    """Best-effort rasterize for GitHub."""
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
    svg = asyncio.run(_capture())
    if not svg.is_file() or svg.stat().st_size < 500:
        print("screenshot failed", file=sys.stderr)
        return 1
    if not _svg_to_png(svg, OUT_PNG):
        print("PNG rasterize failed — README can still use the SVG", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
