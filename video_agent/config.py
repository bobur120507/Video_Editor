from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    telegram_api_base: str = ""

    data_dir: Path = Path("./data")
    database_url: str = ""
    redis_url: str = ""

    llm_providers: str = "anthropic,openrouter,gemini,openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-opus-5-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-5"

    pexels_api_key: str = ""

    video_encoder: str = "libx264"
    output_fps: int = 30
    rife_bin: str = ""
    max_parallel_renders: int = 1

    ytdlp_cookies_file: str = ""

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""

    @property
    def meta_enabled(self) -> bool:
        return bool(self.meta_access_token)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{(self.data_dir / 'video_agent.db').resolve()}"

    def dir(self, *parts: str) -> Path:
        path = self.data_dir.joinpath(*parts).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
