from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from app.config import Settings, get_settings


class TTSError(RuntimeError):
    pass


def synthesize_speech(
    text: str,
    *,
    settings: Settings | None = None,
    filename: str | None = None,
) -> Path:
    """Call Groq TTS and write a WAV file. Returns absolute path."""
    cfg = settings or get_settings()
    if not cfg.groq_api_key:
        raise TTSError("GROQ_API_KEY is not set")
    clean = (text or "").strip()
    if not clean:
        raise TTSError("text is empty")
    # Groq TTS has input limits; keep replies short for speech
    if len(clean) > 1200:
        clean = clean[:1200]

    out_dir = Path(cfg.tts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = filename or f"{uuid.uuid4().hex}.wav"
    if not name.endswith(".wav"):
        name = f"{name}.wav"
    out_path = out_dir / name

    url = f"{cfg.groq_base_url.rstrip('/')}/audio/speech"
    headers = {
        "Authorization": f"Bearer {cfg.groq_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.groq_tts_model,
        "voice": cfg.groq_tts_voice,
        "input": clean,
        "response_format": "wav",
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise TTSError(f"Groq TTS HTTP {resp.status_code}: {resp.text[:400]}")
    out_path.write_bytes(resp.content)
    return out_path.resolve()
