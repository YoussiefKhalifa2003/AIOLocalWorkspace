from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.services.auth import AuthContext, get_auth
from app.services.stt import STTError, transcribe_audio

router = APIRouter(tags=["stt"])


@router.post("/stt")
async def create_transcription(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_auth),
):
    _ = auth
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio upload")
    name = file.filename or "audio.webm"
    ctype = file.content_type or "application/octet-stream"
    try:
        text = transcribe_audio(data, filename=name, content_type=ctype)
    except STTError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"text": text}
