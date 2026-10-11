"""Centralised configuration. All settings come from environment / .env."""
from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"
VOICE_DIR = DATA_DIR / "voice"


def _parse_hhmm(value: str) -> time:
    h, m = value.strip().split(":")
    return time(int(h), int(m))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_user_id: int = 0

    # Identity
    companion_name: str = "Nova"
    user_name: str = ""
    timezone: str = "UTC"

    # AI
    ai_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: int = 120

    # Database
    database_url: str = "sqlite:///./data/agent.db"

    # Context
    recent_messages_limit: int = 20
    memory_context_limit: int = 12

    # Proactive
    proactive_enabled: bool = True
    proactive_check_interval_minutes: int = 20
    min_time_between_proactive_messages_hours: float = 3
    inactivity_threshold_hours: float = 6
    max_proactive_messages_per_day: int = 4
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "08:00"
    escalation_enabled: bool = False
    escalation_wait_hours: float = 4
    escalation_voice_enabled: bool = False

    # Voice
    voice_input_enabled: bool = True
    voice_responses: bool = False
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    piper_binary: str = "piper"
    piper_model_path: str = ""

    # Search
    search_provider: str = "duckduckgo"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_token: str = ""

    log_level: str = "INFO"

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _check_hhmm(cls, v: str) -> str:
        _parse_hhmm(v)
        return v

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    @property
    def quiet_start(self) -> time:
        return _parse_hhmm(self.quiet_hours_start)

    @property
    def quiet_end(self) -> time:
        return _parse_hhmm(self.quiet_hours_end)

    def secrets(self) -> list[str]:
        """Values that must never appear in logs."""
        return [s for s in (self.telegram_bot_token, self.api_token) if s]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
