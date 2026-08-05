import pytest
from app.services.tts import TTSError, synthesize_speech
from app.config import get_settings


def test_tts_requires_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(TTSError, match="GROQ_API_KEY"):
        synthesize_speech("hello")
