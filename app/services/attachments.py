"""Chat attachment storage: validate, save, resolve, delete."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.config import get_settings

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# extension -> canonical content-type
ALLOWED_EXTENSIONS: dict[str, str] = {
    # images
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    # docs
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": DOCX_CONTENT_TYPE,
    # code / config (read as text for the LLM)
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".jsx": "text/javascript",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".cc": "text/x-c++",
    ".cxx": "text/x-c++",
    ".hpp": "text/x-c++",
    ".cs": "text/x-csharp",
    ".java": "text/x-java",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".swift": "text/x-swift",
    ".kt": "text/x-kotlin",
    ".kts": "text/x-kotlin",
    ".scala": "text/x-scala",
    ".sql": "application/sql",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".less": "text/x-less",
    ".sh": "application/x-sh",
    ".bash": "application/x-sh",
    ".zsh": "application/x-sh",
    ".ps1": "application/x-powershell",
    ".bat": "application/x-bat",
    ".cmd": "application/x-bat",
    ".ipynb": "application/x-ipynb+json",
    ".r": "text/x-r",
    ".lua": "text/x-lua",
    ".pl": "text/x-perl",
    ".pm": "text/x-perl",
    ".vue": "text/x-vue",
    ".svelte": "text/x-svelte",
    ".graphql": "application/graphql",
    ".gql": "application/graphql",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".env": "text/plain",
    ".env.example": "text/plain",
    ".gitignore": "text/plain",
    ".dockerignore": "text/plain",
    ".editorconfig": "text/plain",
    ".dockerfile": "text/x-dockerfile",
}

# basename (no/odd extension) -> content-type
ALLOWED_BASENAMES: dict[str, str] = {
    "dockerfile": "text/x-dockerfile",
    "makefile": "text/x-makefile",
    "gemfile": "text/x-ruby",
    "procfile": "text/plain",
    "rakefile": "text/x-ruby",
    "cmakelists.txt": "text/plain",
    ".gitignore": "text/plain",
    ".dockerignore": "text/plain",
    ".editorconfig": "text/plain",
    ".env": "text/plain",
    ".env.example": "text/plain",
}

# Extensions whose bytes are read as UTF-8 text for the LLM
TEXT_EXTRACT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".cs",
        ".java",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".less",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".ipynb",
        ".r",
        ".lua",
        ".pl",
        ".pm",
        ".vue",
        ".svelte",
        ".graphql",
        ".gql",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".env.example",
        ".gitignore",
        ".dockerignore",
        ".editorconfig",
        ".dockerfile",
    }
)

ALLOWED_CONTENT_TYPES = frozenset(ALLOWED_EXTENSIONS.values()) | frozenset(
    ALLOWED_BASENAMES.values()
) | frozenset(
    {
        "image/jpg",  # browsers sometimes send this
        "text/x-markdown",
        "text/plain",
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


def _lookup_key(filename: str) -> str | None:
    """Return an ALLOWED_EXTENSIONS / basename key for this filename."""
    name = Path(filename or "").name
    lower = name.lower()
    if lower in ALLOWED_BASENAMES:
        return lower
    # multi-suffix specials
    for multi in (".env.example", ".gitignore", ".dockerignore", ".editorconfig"):
        if lower == multi or lower.endswith(multi):
            return multi
    ext = Path(lower).suffix
    if ext in ALLOWED_EXTENSIONS:
        return ext
    return None


def _extension(filename: str) -> str:
    key = _lookup_key(filename)
    if key:
        return key
    return Path(filename or "").suffix.lower()


def is_text_extractable(filename: str, content_type: str | None = None) -> bool:
    key = _lookup_key(filename) or Path(filename or "").suffix.lower()
    if key in TEXT_EXTRACT_EXTENSIONS or key in ALLOWED_BASENAMES:
        return True
    ctype = (content_type or "").lower()
    return ctype.startswith("text/") or ctype in {
        "application/json",
        "application/yaml",
        "application/toml",
        "application/xml",
        "application/sql",
        "application/graphql",
        "application/x-sh",
        "application/x-powershell",
        "application/x-bat",
        "application/x-ipynb+json",
    }


def validate_upload(*, filename: str, content_type: str | None, size: int) -> tuple[str, str]:
    """Return (safe_name, canonical_content_type) or raise AttachmentError."""
    if size <= 0:
        raise AttachmentError("empty file")
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(f"file too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")
    safe = safe_filename(filename)
    key = _lookup_key(safe)
    if key is None:
        raise AttachmentError(
            "unsupported file type - use images, pdf, docx, txt/md, or common code/config files"
        )
    if key in ALLOWED_BASENAMES:
        canonical = ALLOWED_BASENAMES[key]
    else:
        canonical = ALLOWED_EXTENSIONS[key]
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
