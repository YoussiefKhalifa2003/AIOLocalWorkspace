"""CLI ping beep when unread @mentions increase (web parity)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def unread_rise_flash(
    prev: int | None, unread: int, mentions: list[dict[str, Any]] | None = None
) -> tuple[bool, str]:
    """Return (should_ping, status_flash). First poll (prev is None) never pings."""
    if prev is None or unread <= prev or unread <= 0:
        return False, ""
    who = "?"
    rows = mentions or []
    if rows:
        who = str(rows[0].get("from") or "?")
    return True, f"{who} pinged you"


def play_ping_sound() -> None:
    """Play a short ping. Never raises — missing audio is fine."""
    try:
        if sys.platform == "win32":
            _ping_windows()
        elif sys.platform == "darwin":
            _ping_macos()
        else:
            _ping_linux()
    except Exception:
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass


def _ping_windows() -> None:
    import winsound

    try:
        winsound.Beep(880, 180)
    except Exception:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)


def _ping_macos() -> None:
    tink = Path("/System/Library/Sounds/Tink.aiff")
    afplay = shutil.which("afplay")
    if afplay and tink.is_file():
        subprocess.run(
            [afplay, str(tink)],
            check=False,
            capture_output=True,
            timeout=3,
        )
        return
    print("\a", end="", flush=True)


def _ping_linux() -> None:
    paplay = shutil.which("paplay")
    for candidate in (
        "/usr/share/sounds/freedesktop/stereo/message.oga",
        "/usr/share/sounds/freedesktop/stereo/bell.oga",
    ):
        if paplay and Path(candidate).is_file():
            subprocess.run(
                [paplay, candidate],
                check=False,
                capture_output=True,
                timeout=3,
            )
            return
    print("\a", end="", flush=True)
