"""Native OS file dialog for CLI chat attachments."""

from __future__ import annotations

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


def pick_attachment_files(*, title: str = "Attach file", max_files: int = 1) -> list[Path]:
    """Open a native file chooser. Returns [] if cancelled.

    Blocks the calling thread until the dialog closes — call from a worker thread
    so the Textual UI stays responsive.
    """
    if max_files < 1:
        return []
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

    out: list[Path] = []
    for p in paths:
        if p.is_file():
            out.append(p)
        if len(out) >= max_files:
            break
    return out
