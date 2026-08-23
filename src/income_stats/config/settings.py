"""Typed application configuration loaded from YAML and environment variables."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from income_stats.models.domain import (
    FunSummaryConfig,
    GoalConfig,
    Period,
    normalize_label,
)


class BotConfig(BaseModel):
    timezone: str = "Europe/Kyiv"
    allowed_chat_ids: list[int] = Field(default_factory=list)
    admin_user_ids: list[int] = Field(default_factory=list)


class StorageConfig(BaseModel):
    records_file: Path = Path("data/records.csv")
    notes_file: Path = Path("data/record_notes.csv")
    chat_settings_file: Path = Path("data/chat_settings.csv")
    goals_file: Path = Path("data/goals.csv")
    tags_file: Path = Path("data/tags.csv")
    export_directory: Path = Path("data/exports")


class TaxonomyConfig(BaseModel):
    """Configured canonical category/tag labels and their message aliases."""

    categories: dict[str, list[str]] = Field(default_factory=lambda: {"other": []})
    tags: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("categories", "tags", mode="before")
    @classmethod
    def normalize_taxonomy(
        cls, values: object, info: ValidationInfo
    ) -> dict[str, list[str]]:
        if not isinstance(values, dict):
            raise ValueError(f"{info.field_name} must be an alias map")
        if info.field_name == "categories" and not values:
            raise ValueError("At least one category is required")

        normalized: dict[str, list[str]] = {}
        for name, aliases in values.items():
            if not isinstance(name, str):
                raise ValueError("Taxonomy labels must be strings")
            canonical = normalize_label(name)
            if canonical in normalized:
                raise ValueError(f"Taxonomy labels collide after normalization: {name}")
            if not isinstance(aliases, list):
                raise ValueError(f"Aliases for {name} must be a list")

            normalized_aliases: list[str] = []
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    raise ValueError(f"Aliases for {name} must not be blank")
                normalized_aliases.append(alias.strip().casefold())
            normalized[canonical] = list(dict.fromkeys(normalized_aliases))

        if info.field_name == "categories" and "other" not in normalized:
            raise ValueError("Categories must define an 'other' fallback")
        return normalized


class IncomeConfig(TaxonomyConfig):
    default_currency: str = "UAH"
    allow_custom_categories: bool = True
    expense_markers: list[str] = Field(
        default_factory=lambda: ["витрата", "витратив", "витратила", "мінус"]
    )

    @field_validator("default_currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Currency must be a three-letter code")
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("Currency must be a three-letter code")
        return currency

    @field_validator("expense_markers", mode="before")
    @classmethod
    def normalize_expense_markers(cls, values: object) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("expense_markers must be a list")
        markers = [
            str(value).strip().casefold() for value in values if str(value).strip()
        ]
        return list(dict.fromkeys(markers))


class PermissionsConfig(BaseModel):
    everyone_can_edit: bool = True
    author_can_delete: bool = True
    admin_can_delete: bool = True
    edit_lock_seconds: int = Field(default=120, ge=10, le=3600)


class AnalyticsConfig(BaseModel):
    default_period: Period = "month"
    static_preview: bool = True
    interactive_html: bool = True
    forecast_method: Literal["linear", "average", "weighted"] = "weighted"
    fx_to_uah: dict[str, float] = Field(
        default_factory=lambda: {"USD": 41.0, "EUR": 45.0}
    )


class AppConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    income: IncomeConfig = Field(default_factory=IncomeConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    fun_summary: FunSummaryConfig = Field(default_factory=FunSummaryConfig)
    goals: GoalConfig = Field(default_factory=GoalConfig)


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
