"""Launch interactive Claude / Codex CLIs in a new terminal window."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app.config import get_settings


def resolve_cli_bin(name: str) -> str | None:
    """Return PATH entry for a CLI (Windows npm .cmd preferred)."""
    return shutil.which(name)


def launch_argv(bin_path: str, *, cwd: str | None = None) -> list[str]:
    """Build argv to open `bin_path` in a new terminal (no PowerShell)."""
    work = cwd or str(Path.cwd())
    if sys.platform == "win32":
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            return [wt, "-w", "0", "nt", "-d", work, bin_path]
        # cmd start: empty title "", then the .cmd shim (not .ps1)
        return ["cmd.exe", "/c", "start", "", bin_path]
    # macOS / Linux best-effort
    if sys.platform == "darwin":
        return ["open", "-a", "Terminal", bin_path]
    for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        t = shutil.which(term)
        if t:
            if "gnome" in term:
                return [t, "--", bin_path]
            return [t, "-e", bin_path]
    return [bin_path]


def launch_coding_cli(which: str, *, cwd: str | None = None) -> tuple[bool, str]:
    """
    Spawn interactive Claude or Codex in a new window.

    `which` is 'claude' or 'codex'. Returns (ok, status_message).
    """
    settings = get_settings()
    key = (which or "").strip().lower()
    if key in ("claude", "claude_code"):
        configured = settings.claude_bin or "claude"
        label = "Claude"
    elif key == "codex":
        configured = settings.codex_bin or "codex"
        label = "Codex"
    else:
        return False, f"unknown CLI {which!r}"

    bin_path = resolve_cli_bin(configured)
    if not bin_path:
        return (
            False,
            f"{label} CLI not on PATH — install it then run aio doctor",
        )

    work = cwd or str(Path.cwd())
    argv = launch_argv(bin_path, cwd=work)
    try:
        kwargs: dict = {"close_fds": True}
        if sys.platform == "win32" and argv and argv[0].lower().endswith("cmd.exe"):
            kwargs["cwd"] = work
        subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        return False, f"failed to open {label}: {exc}"
    return True, f"opened {label} in a new terminal"
