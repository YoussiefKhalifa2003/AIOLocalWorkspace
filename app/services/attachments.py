"""Chat attachment storage: validate, save, resolve, delete."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.config import get_settings

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5

# extension -> canonical content-type
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

ALLOWED_CONTENT_TYPES = frozenset(ALLOWED_EXTENSIONS.values()) | frozenset(
    {
        "image/jpg",  # browsers sometimes send this
        "text/x-markdown",
        "application/octet-stream",  # only accepted if extension is allowed
    }
)


class AttachmentError(ValueError):
    pass


def uploads_root() -> Path:
    root = Path(get_settings().uploads_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\-()+ ]+", "_", base).strip(" ._") or "file"
    if len(base) > 180:
        stem = Path(base).stem[:140]
        suffix = Path(base).suffix[:20]
        base = f"{stem}{suffix}"
    return base


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def validate_upload(*, filename: str, content_type: str | None, size: int) -> tuple[str, str]:
    """Return (safe_name, canonical_content_type) or raise AttachmentError."""
    if size <= 0:
        raise AttachmentError("empty file")
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(f"file too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")
    safe = safe_filename(filename)
    ext = _extension(safe)
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentError(
            "unsupported file type — use png, jpeg, gif, webp, pdf, txt, or md"
        )
    canonical = ALLOWED_EXTENSIONS[ext]
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in ALLOWED_CONTENT_TYPES and ctype != canonical:
        # Allow mismatch only when extension is trusted and browser sent octet-stream
        if ctype != "application/octet-stream":
            raise AttachmentError(f"content type not allowed: {ctype or '(missing)'}")
    return safe, canonical


def relative_storage_path(*, tenant_id: int, chat_id: int, filename: str) -> str:
    return f"{tenant_id}/{chat_id}/{uuid.uuid4().hex}_{safe_filename(filename)}"


def absolute_path(storage_path: str) -> Path:
    """Resolve a stored relative path under uploads_root (no path escape)."""
    root = uploads_root().resolve()
    rel = Path(storage_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise AttachmentError("invalid storage path")
    full = (root / rel).resolve()
    if not str(full).startswith(str(root)):
        raise AttachmentError("invalid storage path")
    return full


def save_bytes(
    data: bytes,
    *,
    tenant_id: int,
    chat_id: int,
    filename: str,
    content_type: str | None,
) -> tuple[str, str, str, int]:
    """Validate and write file. Returns (safe_name, content_type, storage_path, size)."""
    safe, ctype = validate_upload(
        filename=filename, content_type=content_type, size=len(data)
    )
    rel = relative_storage_path(tenant_id=tenant_id, chat_id=chat_id, filename=safe)
    path = absolute_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return safe, ctype, rel, len(data)


def delete_file(storage_path: str) -> None:
    try:
        path = absolute_path(storage_path)
    except AttachmentError:
        return
    if path.is_file():
        path.unlink(missing_ok=True)


def attachment_url(attachment_id: int) -> str:
    return f"/attachments/{attachment_id}"


def is_image_content_type(content_type: str) -> bool:
    return (content_type or "").lower().startswith("image/")
