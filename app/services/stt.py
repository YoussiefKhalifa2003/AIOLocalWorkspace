from __future__ import annotations

import httpx

from app.config import Settings, get_settings


class STTError(RuntimeError):
    pass


def transcribe_audio(
    data: bytes,
    *,
    filename: str = "audio.webm",
    content_type: str = "audio/webm",
    settings: Settings | None = None,
) -> str:
    """Call Groq Whisper and return transcript text."""
    cfg = settings or get_settings()
    if not cfg.groq_api_key:
        raise STTError("GROQ_API_KEY is not set")
    if not data:
        raise STTError("audio is empty")

    url = f"{cfg.groq_base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {cfg.groq_api_key}"}
    files = {"file": (filename, data, content_type)}
    form = {
        "model": cfg.groq_stt_model,
        "response_format": "json",
        "temperature": "0",
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, files=files, data=form)
    if resp.status_code >= 400:
        raise STTError(f"Groq STT HTTP {resp.status_code}: {resp.text[:400]}")
    payload = resp.json()
    text = (payload.get("text") or "").strip()
    if not text:
        raise STTError("empty transcript")
    return text
