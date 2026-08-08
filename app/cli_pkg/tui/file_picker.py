"""Native OS file dialog for CLI chat attachments (macOS / Windows / Linux)."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from app.services.attachments import ALLOWED_EXTENSIONS


def attachment_filetypes() -> list[tuple[str, str]]:
    """Tk filetypes filter derived from server-allowed extensions."""
    code = " ".join(
        f"*{ext}"
        for ext in sorted(ALLOWED_EXTENSIONS)
        if ext
        not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".pdf",
            ".docx",
            ".txt",
            ".md",
        }
    )
    return [
        ("Allowed files", " ".join(f"*{e}" for e in sorted(ALLOWED_EXTENSIONS))),
        ("Code / config", code),
        ("Images", "*.png *.jpg *.jpeg *.gif *.webp"),
        ("Documents", "*.pdf *.docx *.txt *.md"),
        ("All files", "*.*"),
    ]


def _as_existing_files(paths: list[Path], max_files: int) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        try:
            resolved = p.expanduser().resolve(strict=False)
        except OSError:
            resolved = p
        if resolved.is_file():
            out.append(resolved)
        if len(out) >= max_files:
            break
    return out


def _escape_applescript(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')


def _pick_macos(*, title: str, max_files: int) -> list[Path]:
    """Native macOS chooser via osascript (works from any thread; no Tk needed)."""
    prompt = _escape_applescript(title)
    if max_files > 1:
        script = f'''
try
    set theFiles to choose file with prompt "{prompt}" with multiple selections allowed
    set out to ""
    repeat with f in theFiles
        set out to out & (POSIX path of f) & linefeed
    end repeat
    return out
on error number -128
    return ""
on error
    return ""
end try
'''
    else:
        script = f'''
try
    set f to choose file with prompt "{prompt}"
    return POSIX path of f
on error number -128
    return ""
on error
    return ""
end try
'''
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "osascript failed").strip())
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return _as_existing_files([Path(ln) for ln in lines], max_files)


def _pick_zenity(*, title: str, max_files: int) -> list[Path]:
    """Linux portal / Zenity file chooser when available."""
    zenity = shutil.which("zenity")
    if not zenity:
        raise RuntimeError("zenity not found")
    cmd = [
        zenity,
        "--file-selection",
        f"--title={title}",
        "--separator=\n",
    ]
    if max_files > 1:
        cmd.append("--multiple")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    # Cancel → exit 1 with empty stdout
    if proc.returncode != 0:
        return []
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return _as_existing_files([Path(ln) for ln in lines], max_files)


def _pick_tk(*, title: str, max_files: int) -> list[Path]:
    """Tk filedialog — fine on Windows; last-resort on macOS/Linux."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError(
            "file picker unavailable — install a Python build with tkinter"
        ) from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        root.update()
    except tk.TclError:
        pass

    filetypes = attachment_filetypes()
    try:
        if max_files > 1:
            raw = filedialog.askopenfilenames(parent=root, title=title, filetypes=filetypes)
            paths = [Path(p) for p in (raw or ()) if p]
        else:
            raw = filedialog.askopenfilename(parent=root, title=title, filetypes=filetypes)
            paths = [Path(raw)] if raw else []
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

    return _as_existing_files(paths, max_files)


def pick_attachment_files(*, title: str = "Attach file", max_files: int = 1) -> list[Path]:
    """Open a native file chooser on macOS, Windows, or Linux. Returns [] if cancelled.

    Blocks the calling thread until the dialog closes — call from a worker thread
    so the Textual UI stays responsive.
    """
    if max_files < 1:
        return []

    errors: list[str] = []
    system = sys.platform

    if system == "darwin":
        try:
            return _pick_macos(title=title, max_files=max_files)
        except Exception as exc:
            errors.append(f"macOS picker: {exc}")

    if system.startswith("linux"):
        try:
            return _pick_zenity(title=title, max_files=max_files)
        except Exception as exc:
            errors.append(f"zenity: {exc}")

    try:
        return _pick_tk(title=title, max_files=max_files)
    except Exception as exc:
        errors.append(f"tkinter: {exc}")

    detail = "; ".join(errors) if errors else "no backend available"
    raise RuntimeError(
        f"file picker unavailable on {platform.system()} ({system}): {detail}"
    )
