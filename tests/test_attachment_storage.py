"""Phase 1 gate: attachment storage roundtrip."""

from __future__ import annotations

import pytest

from app.services.attachments import (
    AttachmentError,
    absolute_path,
    delete_file,
    save_bytes,
    validate_upload,
)


def test_validate_and_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings

    get_settings.cache_clear()

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    name, ctype, rel, size = save_bytes(
        png,
        tenant_id=1,
        chat_id=2,
        filename="shot.png",
        content_type="image/png",
    )
    assert name == "shot.png"
    assert ctype == "image/png"
    assert size == len(png)
    path = absolute_path(rel)
    assert path.is_file()
    assert path.read_bytes() == png

    delete_file(rel)
    assert not path.exists()


def test_reject_bad_type_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(AttachmentError):
        validate_upload(filename="x.exe", content_type="application/octet-stream", size=10)

    with pytest.raises(AttachmentError):
        validate_upload(
            filename="big.pdf",
            content_type="application/pdf",
            size=20 * 1024 * 1024,
        )

    pdf = b"%PDF-1.4 tiny"
    name, ctype, rel, _ = save_bytes(
        pdf,
        tenant_id=1,
        chat_id=1,
        filename="notes.pdf",
        content_type="application/pdf",
    )
    assert ctype == "application/pdf"
    assert absolute_path(rel).read_bytes() == pdf
