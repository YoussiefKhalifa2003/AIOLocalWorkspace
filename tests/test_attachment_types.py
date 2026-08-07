"""Attachment type expansion + text extraction for code/docx."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.attachment_context import extract_attachment_text
from app.services.attachments import (
    AttachmentError,
    absolute_path,
    save_bytes,
    validate_upload,
)


def test_validate_upload_accepts_py_and_docx():
    assert validate_upload(filename="main.py", content_type="text/plain", size=12)[1] == (
        "text/x-python"
    )
    assert (
        validate_upload(
            filename="notes.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=100,
        )[0]
        == "notes.docx"
    )


def test_validate_upload_rejects_exe():
    with pytest.raises(AttachmentError):
        validate_upload(filename="evil.exe", content_type="application/octet-stream", size=10)


def test_validate_upload_dockerfile_basename():
    name, ctype = validate_upload(filename="Dockerfile", content_type=None, size=20)
    assert name == "Dockerfile"
    assert "docker" in ctype or ctype.startswith("text/")


def test_extract_python_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "up"))
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        code = b"def hello():\n    return 42\n"
        safe, ctype, rel, size = save_bytes(
            code, tenant_id=1, chat_id=1, filename="hello.py", content_type="text/plain"
        )
        row = SimpleNamespace(filename=safe, content_type=ctype, size_bytes=size, storage_path=rel)
        kind, body = extract_attachment_text(row)
        assert kind == "text"
        assert "def hello" in body
        assert absolute_path(rel).is_file()
    finally:
        get_settings.cache_clear()


def test_extract_docx_attachment(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    from docx import Document

    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "up"))
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        doc_path = tmp_path / "brief.docx"
        doc = Document()
        doc.add_paragraph("Auth edge case lives in chats.py")
        doc.save(doc_path)
        data = doc_path.read_bytes()
        safe, ctype, rel, size = save_bytes(
            data,
            tenant_id=1,
            chat_id=1,
            filename="brief.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        row = SimpleNamespace(filename=safe, content_type=ctype, size_bytes=size, storage_path=rel)
        kind, body = extract_attachment_text(row)
        assert kind == "docx"
        assert "chats.py" in body
    finally:
        get_settings.cache_clear()


def test_attachment_filetypes_include_code():
    from app.cli_pkg.tui.file_picker import attachment_filetypes

    types = attachment_filetypes()
    blob = " ".join(pattern for _, pattern in types)
    assert "*.py" in blob
    assert "*.docx" in blob
    assert "*.png" in blob
