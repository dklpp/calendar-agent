"""Configuration: secrets from the environment, preferences from TOML."""

from __future__ import annotations

import datetime as dt
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class WorkingWindow(BaseModel):
    start: dt.time = dt.time(9, 0)
    end: dt.time = dt.time(18, 0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse(cls, v):
        if isinstance(v, str):
            h, m = v.split(":")
            return dt.time(int(h), int(m))
        return v


class WorkingHours(BaseModel):
    """Acceptable meeting windows, with a per-zone override.

    A meeting pinned to Vilnius time has a different sensible window than one
    pinned to Toronto time, so slot search consults the window belonging to the
    zone the meeting is actually in.
    """

    default: WorkingWindow = WorkingWindow()
    by_zone: dict[str, WorkingWindow] = Field(default_factory=dict)
    buffer_minutes: int = 15
    weekdays_only: bool = True

    def window_for(self, tz_name: str) -> WorkingWindow:
        return self.by_zone.get(tz_name, self.default)


class Preferences(BaseModel):
    primary_tz: str = "America/Toronto"
    secondary_tz: str = "Europe/Vilnius"
    default_duration_minutes: int = 60
    reminder_minutes: list[int] = Field(default_factory=lambda: [60])
    working_hours: WorkingHours = WorkingHours()
    auto_confirm_unambiguous: bool = False
    confirm_ttl_minutes: int = 30
    calendar_id: str = "primary"
    whisper_model: str = "small"
    whisper_language: str = "auto"


class Settings(BaseSettings):
    """Secrets and machine-specific paths. Never committed."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""

    google_credentials_path: Path = BACKEND_ROOT / "credentials.json"
    google_token_path: Path = BACKEND_ROOT / "token.json"

    whisper_cli: Path = Path("whisper-cli")
    whisper_model_path: Path = BACKEND_ROOT / "models" / "ggml-small.bin"

    database_path: Path = BACKEND_ROOT / "calendar_agent.db"
    config_path: Path = BACKEND_ROOT / "config.toml"

    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def allowed_chat_ids(self) -> set[int]:
        """Telegram chat whitelist.

        An empty set means *nobody* is allowed. That is deliberate: a bot that
        defaults to open lets anyone who finds the username read and rewrite
        the calendar.
        """
        raw = self.telegram_allowed_chat_ids.strip()
        if not raw:
            return set()
        return {int(part) for part in raw.replace(",", " ").split()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_preferences() -> Preferences:
    path = get_settings().config_path
    if path.exists():
        with path.open("rb") as handle:
            return Preferences.model_validate(tomllib.load(handle))
    return Preferences()
