from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter (free models for Models tab)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Gemini (legacy name openrouter_* was misused; prefer gemini_*)
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    router_model: str = "gemini-3.1-flash-lite"
    agent_model: str = "gemini-3.1-flash-lite"
    agent_model_fast: str = ""
    agent_model_strong: str = ""
    checklist_model: str = "gemini-3.1-flash-lite"
    database_url: str = "sqlite:///./aio.db"
    api_base_url: str = "http://127.0.0.1:8000"
    general_status_posts: bool = False
    github_webhook_secret: str = "dev-secret"
    github_token: str = ""
    github_repo: str = ""
    coding_backend: str = "llm"
    opencode_bin: str = "opencode"
    opencode_timeout_seconds: int = 120
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    agent_llm_backend: str = "auto"  # auto | gemini | openrouter | opencode
    demo_api_key: str = "demo-key-a"
    demo_api_key_b: str = "demo-key-b"
    workspace_join_key: str = "demo-key-a"
    llm_max_retries: int = 3
    llm_retry_backoff_seconds: float = 1.0
    groq_api_key: str = ""
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "austin"
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    tts_dir: str = "data/tts"
    host_bind: str = "0.0.0.0"

    def resolve_gemini_key(self) -> str:
        return (self.gemini_api_key or "").strip()

    def resolve_gemini_base(self) -> str:
        return (self.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip(
            "/"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
