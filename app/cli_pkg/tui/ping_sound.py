"""CLI ping beep when unread @mentions increase (web parity)."""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
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
    """Play a short soft ping off the UI thread. Never raises."""
    try:
        threading.Thread(target=_play_ping_sound_sync, daemon=True).start()
    except Exception:
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass


def _play_ping_sound_sync() -> None:
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


def _soft_ping_wav_bytes() -> bytes:
    """Soft two-tone chirp similar to the web app's 880→660 sine ping."""
    rate = 22050
    chunks: list[float] = []
    # Tone 1: 880 Hz soft fade
    for i in range(int(rate * 0.07)):
        t = i / rate
        env = min(1.0, i / (rate * 0.008)) * max(0.0, 1.0 - (t / 0.07))
        chunks.append(0.22 * env * math.sin(2 * math.pi * 880 * t))
    # Brief gap
    chunks.extend([0.0] * int(rate * 0.015))
    # Tone 2: 660 Hz fade
    for i in range(int(rate * 0.11)):
        t = i / rate
        env = min(1.0, i / (rate * 0.008)) * max(0.0, 1.0 - (t / 0.11))
        chunks.append(0.18 * env * math.sin(2 * math.pi * 660 * t))

    buf = tempfile.SpooledTemporaryFile(max_size=64_000)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in chunks
        )
        wf.writeframes(frames)
    buf.seek(0)
    data = buf.read()
    buf.close()
    return data


def _ping_windows() -> None:
    import winsound

    path: Path | None = None
    try:
        data = _soft_ping_wav_bytes()
        fd, name = tempfile.mkstemp(suffix=".wav")
        path = Path(name)
        with open(fd, "wb") as f:
            f.write(data)
        # Async so the UI thread (caller) returns immediately when threaded.
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return
    except Exception:
        try:
            winsound.Beep(880, 60)
            winsound.Beep(660, 90)
            return
        except Exception:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    finally:
        # Async playback needs the file briefly; delete on a short delay thread.
        if path is not None:

            def _cleanup(p: Path = path) -> None:
                import time

                time.sleep(0.6)
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

            threading.Thread(target=_cleanup, daemon=True).start()


def _ping_macos() -> None:
    tink = Path("/System/Library/Sounds/Tink.aiff")
    pop = Path("/System/Library/Sounds/Pop.aiff")
    afplay = shutil.which("afplay")
    sound = tink if tink.is_file() else pop if pop.is_file() else None
    if afplay and sound is not None:
        subprocess.run(
            [afplay, str(sound)],
            check=False,
            capture_output=True,
            timeout=3,
        )
        return
    print("\a", end="", flush=True)


def _ping_linux() -> None:
    paplay = shutil.which("paplay")
    for candidate in (
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
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
