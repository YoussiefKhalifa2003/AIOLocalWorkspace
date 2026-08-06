"""Chat attachment upload and download."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import ChatAttachment
from app.db.session import get_db
from app.services.attachments import (
    AttachmentError,
    absolute_path,
    attachment_url,
    is_image_content_type,
    save_bytes,
)
from app.services.auth import AuthContext, get_auth
from app.services.chat_access import require_chat_access

router = APIRouter(tags=["attachments"])


def attachment_to_dict(row: ChatAttachment) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "url": attachment_url(row.id),
        "message_id": row.message_id,
        "chat_id": row.chat_id,
    }


@router.post("/chats/{chat_id}/attachments")
async def upload_attachment(
    chat_id: int,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    require_chat_access(db, auth, chat_id)
    data = await file.read()
    try:
        safe, ctype, rel, size = save_bytes(
            data,
            tenant_id=auth.tenant_id,
            chat_id=chat_id,
            filename=file.filename or "file",
            content_type=file.content_type,
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = ChatAttachment(
        tenant_id=auth.tenant_id,
        chat_id=chat_id,
        message_id=None,
        uploader_user_id=auth.user_id,
        filename=safe,
        content_type=ctype,
        size_bytes=size,
        storage_path=rel,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return attachment_to_dict(row)


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: int,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ChatAttachment)
        .filter(
            ChatAttachment.id == attachment_id,
            ChatAttachment.tenant_id == auth.tenant_id,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    require_chat_access(db, auth, row.chat_id)
    try:
        path = absolute_path(row.storage_path)
    except AttachmentError as exc:
        raise HTTPException(status_code=404, detail="file missing") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")

    inline = is_image_content_type(row.content_type)
    return FileResponse(
        path,
        media_type=row.content_type or "application/octet-stream",
        filename=row.filename,
        content_disposition_type="inline" if inline else "attachment",
    )
