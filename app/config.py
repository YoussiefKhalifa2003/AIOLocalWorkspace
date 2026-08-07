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
    merge_method: str = "squash"  # squash | merge | rebase
    coding_backend: str = "llm"  # llm | opencode | codex | claude_code
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
    uploads_dir: str = "data/uploads"
    agent_work_root: str = "data/workspaces"
    agent_git_timeout_seconds: int = 120

    # DeepResearch live retrieval (no key = no citations, never invented links)
    tavily_api_key: str = ""
    research_max_results: int = 6
    research_fetch_timeout_seconds: int = 20
    research_max_page_chars: int = 20000

    # Agentic coding CLIs (headless), run with cwd = objective workspace
    codex_bin: str = "codex"
    codex_timeout_seconds: int = 900
    codex_sandbox: str = "workspace-write"
    codex_api_key: str = ""
    claude_bin: str = "claude"
    claude_timeout_seconds: int = 900
    claude_permission_mode: str = "acceptEdits"
    anthropic_api_key: str = ""

    tui_poll_seconds: float = 2.0
    host_bind: str = "0.0.0.0"
    # Public base for /join/{token} invite links (LAN IP + port)
    invite_app_url: str = ""
    # Microsoft Teams Incoming Webhook / Workflow URL (optional; blank = skip)
    teams_webhook_url: str = ""

    # Invite emails: optional domain lock + free Outlook Web via Playwright (no SMTP billing)
    # Empty INVITE_ALLOWED_DOMAIN = any email can be invited / register (recommended).
    # Set e.g. tatweermea.com to re-enable the lock.
    invite_allowed_domain: str = ""
    outlook_invite_enabled: bool = True
    outlook_storage_state: str = "data/outlook_auth.json"
    # False = show the Chromium window while composing/sending (recommended)
    outlook_headless: bool = False
    outlook_timeout_seconds: float = 60.0

    def resolve_gemini_key(self) -> str:
        return (self.gemini_api_key or "").strip()

    def resolve_gemini_base(self) -> str:
        return (self.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip(
            "/"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
