"""Application settings loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CLOUD_API_LIMIT_MB = 50  # Telegram cloud Bot API upload cap
LOCAL_API_LIMIT_MB = 2000  # self-hosted telegram-bot-api cap


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(..., description="Token from @BotFather")
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    force_sub_channel: str | None = None  # "@my_channel" or "-1001234567890"; empty = disabled
    force_sub_channel_url: str | None = None  # invite link shown to users (for private channels)

    # --- Local Bot API server (optional, raises upload cap to 2 GB) ---
    use_local_bot_api: bool = False
    local_bot_api_url: str = "http://telegram-bot-api:8081"

    # --- Storage ---
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    download_dir: Path = Path("./downloads")
    temp_file_ttl_minutes: int = 60
    cleanup_interval_minutes: int = 30

    # --- yt-dlp / ffmpeg ---
    cookies_file: Path | None = None  # Netscape cookies.txt for Instagram/YouTube
    proxy: str | None = None  # e.g. socks5://127.0.0.1:1080
    yt_player_clients: str | None = None  # YouTube extractor arg, e.g. "android,web"
    download_timeout_seconds: int = 900
    max_duration_minutes: int = 180
    audio_bitrate_kbps: int = 192

    # --- Limits ---
    rate_limit_per_minute: int = 5
    max_concurrent_downloads: int = 3

    # --- Misc ---
    log_level: str = "INFO"
    default_language: str = "uz"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x) for x in v.replace(";", ",").split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        raise ValueError("ADMIN_IDS must be a comma-separated list of integers")

    @field_validator("cookies_file", "force_sub_channel", "force_sub_channel_url", "proxy", "yt_player_clients", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v in ("", None) else v

    @property
    def max_file_size_mb(self) -> int:
        return LOCAL_API_LIMIT_MB if self.use_local_bot_api else CLOUD_API_LIMIT_MB

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
