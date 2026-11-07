import re
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str
    webhook_url: str | None = None
    public_base_url: str | None = None
    webhook_secret: str | None = None
    default_emoji: str = "⭐"
    max_media_bytes: int = 10 * 1024 * 1024
    port: int = 8000
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        value = re.sub(r"([?&])sslmode=", r"\1ssl=", value)
        value = re.sub(r"([?&])channel_binding=[^&]*", r"\1", value)
        return value.replace("?&", "?").replace("&&", "&").rstrip("?&")

    @property
    def resolved_webhook_url(self) -> str | None:
        if self.webhook_url:
            return self.webhook_url
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/webhook"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
