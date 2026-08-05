from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.auth import AuthContext, get_auth
from app.services.tts import TTSError, synthesize_speech

router = APIRouter(tags=["tts"])


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SpeakOut(BaseModel):
    path: str
    url: str


@router.post("/tts", response_model=SpeakOut)
def create_speech(
    body: SpeakIn,
    auth: AuthContext = Depends(get_auth),
):
    _ = auth
    try:
        path = synthesize_speech(body.text)
    except TTSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = get_settings()
    rel = path.name
    return SpeakOut(path=str(path), url=f"/media/tts/{rel}")


@router.get("/media/tts/{filename}")
def get_tts_file(filename: str):
    settings = get_settings()
    # prevent path traversal
    safe = Path(filename).name
    path = Path(settings.tts_dir) / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/wav", filename=safe)
