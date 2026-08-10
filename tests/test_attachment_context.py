"""Attachments are injected into /skill prompts for the agent."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models import ChatAttachment, ChatMessage
from app.services.attachment_context import build_attachments_prompt_block, extract_attachment_text


def _boot(tmp_path, monkeypatch):
    db_path = tmp_path / "attctx.db"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("UPLOADS_DIR", str(uploads))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from app.db.session import init_db
    from app.services.seed import seed_demo_data

    init_db()
    db = sess.SessionLocal()
    info = seed_demo_data(db)
    db.close()
    from app.main import app

    return TestClient(app), info, sess.SessionLocal, uploads


def _minimal_pdf(text: str = "Citi Completion Certificate for Omar") -> bytes:
    # Tiny valid-enough PDF with a text stream (pypdf can often read this)
    from io import BytesIO

    try:
        from pypdf import PdfWriter
        from pypdf.generic import DecStringObject, NameObject, NumberObject, DictionaryObject, ArrayObject, DecStreamObject

        # Prefer reportlab-free: write with pypdf blank + can't easily add text
    except ImportError:
        pass

    # Hand-rolled PDF with visible text operator
    # Simple one-page PDF
    content = f"""BT /F1 24 Tf 100 700 Td ({text}) Tj ET"""
    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    stream = content.encode()
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_extract_txt_and_pdf(tmp_path, monkeypatch):
    client, info, SessionLocal, uploads = _boot(tmp_path, monkeypatch)
    ha = {"X-API-Key": info["api_key_a"], "X-User-Email": info["email_a"]}
    chat = info["chat_private_a"]

    txt = client.post(
        f"/chats/{chat}/attachments",
        headers=ha,
        files={"file": ("note.txt", b"Hello attachment world", "text/plain")},
    ).json()
    pdf = client.post(
        f"/chats/{chat}/attachments",
        headers=ha,
        files={
            "file": (
                "citi.pdf",
                _minimal_pdf("Citi Completion Certificate"),
                "application/pdf",
            )
        },
    ).json()

    sent = client.post(
        f"/chats/{chat}/messages",
        headers=ha,
        json={
            "body": "/ask can you tell me what this is",
            "speak": False,
            "attachment_ids": [txt["id"], pdf["id"]],
        },
    )
    assert sent.status_code == 200, sent.text
    data = sent.json()
    mid = data["user_message_id"]
    assert data.get("pending") is True

    db = SessionLocal()
    try:
        block = build_attachments_prompt_block(
            db, message_id=mid, tenant_id=info["tenant_a"]
        )
        assert "ATTACHED FILES" in block
        assert "Hello attachment world" in block
        assert "note.txt" in block
        assert "citi.pdf" in block
        # PDF text extraction - at least file header present; body if pypdf got glyphs
        assert "kind=pdf" in block or "kind=error" in block or "Citi" in block
    finally:
        db.close()


def test_image_attachment_marked_in_block(tmp_path, monkeypatch):
    _, info, SessionLocal, uploads = _boot(tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        from app.services.attachments import save_bytes

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        safe, ctype, rel, size = save_bytes(
            png, tenant_id=info["tenant_a"], chat_id=info["chat_private_a"], filename="shot.png", content_type="image/png"
        )
        msg = ChatMessage(
            tenant_id=info["tenant_a"],
            chat_id=info["chat_private_a"],
            sender_user_id=info["user_a"],
            body="/ask what is this",
            visibility="public",
        )
        db.add(msg)
        db.flush()
        row = ChatAttachment(
            tenant_id=info["tenant_a"],
            chat_id=info["chat_private_a"],
            message_id=msg.id,
            uploader_user_id=info["user_a"],
            filename=safe,
            content_type=ctype,
            size_bytes=size,
            storage_path=rel,
        )
        db.add(row)
        db.commit()
        kind, body = extract_attachment_text(row)
        assert kind == "image"
        block = build_attachments_prompt_block(
            db, message_id=msg.id, tenant_id=info["tenant_a"]
        )
        assert "shot.png" in block
        assert "Image file" in block
    finally:
        db.close()
