"""Typed application configuration loaded from YAML and environment variables."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from income_stats.models.domain import FunSummaryConfig, Period, normalize_label


class BotConfig(BaseModel):
    timezone: str = "Europe/Kyiv"
    allowed_chat_ids: list[int] = Field(default_factory=list)
    admin_user_ids: list[int] = Field(default_factory=list)


class StorageConfig(BaseModel):
    records_file: Path = Path("data/records.csv")
    notes_file: Path = Path("data/record_notes.csv")
    chat_settings_file: Path = Path("data/chat_settings.csv")
    export_directory: Path = Path("data/exports")


class TaxonomyConfig(BaseModel):
    """Configured canonical category/tag labels and their message aliases."""

    categories: dict[str, list[str]] = Field(
        default_factory=lambda: {"other": []}
    )
    tags: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("categories", "tags")
    @classmethod
    def normalize_taxonomy(
        cls, values: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        return {
            normalize_label(name): list(
                dict.fromkeys(alias.strip().casefold() for alias in aliases)
            )
            for name, aliases in values.items()
        }


class IncomeConfig(TaxonomyConfig):
    default_currency: str = "UAH"
    allow_custom_categories: bool = True

    @field_validator("default_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()


class PermissionsConfig(BaseModel):
    everyone_can_edit: bool = True
    author_can_delete: bool = True
    admin_can_delete: bool = True
    edit_lock_seconds: int = Field(default=120, ge=10, le=3600)


class AnalyticsConfig(BaseModel):
    default_period: Period = "month"
    static_preview: bool = True
    interactive_html: bool = True


class AppConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    income: IncomeConfig = Field(default_factory=IncomeConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    fun_summary: FunSummaryConfig = Field(default_factory=FunSummaryConfig)


class Secrets(BaseSettings):
    telegram_bot_token: str = ""
    log_level: str = "INFO"
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"}:
            raise ValueError(f"Unsupported LOG_LEVEL: {value}")
        return level


def load_config(path: Path = Path("config.yaml")) -> AppConfig:
    """Load and validate the application's YAML configuration."""
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    return AppConfig.model_validate(raw)

