"""Shared domain models used by services and repositories."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

Period = Literal[
    "today",
    "week",
    "month",
    "year",
    "all",
    "last_week",
    "last_month",
    "last_year",
]
GoalPeriod = Literal["month", "year"]
CHART_PERIODS: tuple[Period, ...] = (
    "week",
    "month",
    "year",
    "last_week",
    "last_month",
    "last_year",
)

RecordType = Literal["income", "expense"]


def normalize_label(value: str) -> str:
    """Normalize a human-entered category or tag to its storage label."""
    normalized = "_".join(value.strip().casefold().split())
    if not normalized:
        raise ValueError("Label must not be empty")
    return normalized[:50]


def _normalize_labels(values: list[str], *, fallback: str | None) -> list[str]:
    labels = list(dict.fromkeys(normalize_label(value) for value in values))
    if not labels and fallback is not None:
        return [fallback]
    return labels


def _normalize_currency(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Currency must be a three-letter code")
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Currency must be a three-letter code")
    return currency


class ParsedIncome(BaseModel):
    """An income candidate extracted from one incoming message."""

    amount: Decimal | None = Field(gt=0)
    currency: str = Field(default="UAH", min_length=3, max_length=3)
    categories: list[str] = Field(default_factory=lambda: ["other"])
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    income_date: date
    type: RecordType = "income"

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return _normalize_currency(value)

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback="other")

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback=None)


class IncomeRecord(BaseModel):
    """A persisted income record."""

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=32)
    telegram_message_id: int
    source_index: int = 0
    chat_id: int
    user_id: int
    username: str = ""
    original_text: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    type: RecordType = "income"
    categories: list[str] = Field(default_factory=lambda: ["other"])
    tags: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1000)
    income_date: date = Field(default_factory=date.today)
    status: Literal["saved"] = "saved"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: int

    @field_validator("id")
    @classmethod
    def validate_callback_safe_id(cls, value: str) -> str:
        if not value.isascii() or not all(
            character.isalnum() or character in "_-" for character in value
        ):
            raise ValueError(
                "Record ID may contain only ASCII letters, digits, '_' and '-'"
            )
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return _normalize_currency(value)

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback="other")

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback=None)


class RecordNote(BaseModel):
    """A note attached to an income record."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    record_id: str
    user_id: int
    username: str = ""
    text: str = Field(min_length=1, max_length=1000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Note must not be empty")
        return value


class TagAlias(BaseModel):
    """A runtime-defined tag and the message aliases that detect it."""

    tag: str
    aliases: list[str] = Field(default_factory=list)
    updated_by: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Tag must be a string")
        return normalize_label(value)

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, values: object) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("Aliases must be a list")
        normalized: list[str] = []
        for alias in values:
            if not isinstance(alias, str) or not alias.strip():
                raise ValueError("Aliases must not be blank")
            normalized.append(alias.strip().casefold())
        return list(dict.fromkeys(normalized))


class ChatSetting(BaseModel):
    """Per-chat income recording state."""

    chat_id: int
    enabled: bool = True
    updated_by: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatGoal(BaseModel):
    """Per-chat income goal, keyed by period (month or year)."""

    chat_id: int
    period: GoalPeriod = "month"
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    updated_by: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return _normalize_currency(value)


class GoalConfig(BaseModel):
    """Configuration for monthly income goal pacing."""

    enabled: bool = True
    after_save_line: bool = True
    ahead_phrases: list[str] = Field(
        default_factory=lambda: ["Так тримати! Ти випереджаєш темп 🚀"]
    )
    behind_phrases: list[str] = Field(
        default_factory=lambda: ["Час пришвидшитись — ще все встигаєш 💪"]
    )
    reached_phrases: list[str] = Field(
        default_factory=lambda: ["Ціль досягнута! Ти неймовірна 🎉"]
    )


class FunItem(BaseModel):
    """A purchasable item used in playful income summaries."""

    label: str
    emoji: str
    price_uah: Decimal = Field(gt=0)
    fractional: bool = False
    luxury: bool = False


class FunSummaryConfig(BaseModel):
    """Configuration for playful post-save comparisons."""

    enabled: bool = True
    comparisons_per_message: int = Field(default=3, ge=1, le=5)
    phrases: list[str] = Field(default_factory=list)
    number_phrases: dict[str, str] = Field(default_factory=dict)
    ending_phrases: dict[str, str] = Field(default_factory=dict)
    items: dict[str, FunItem] = Field(default_factory=dict)
