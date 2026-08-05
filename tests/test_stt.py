import pytest
from app.services.stt import STTError, transcribe_audio
from app.config import get_settings


def test_stt_requires_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(STTError, match="GROQ_API_KEY"):
        transcribe_audio(b"fake-audio")
