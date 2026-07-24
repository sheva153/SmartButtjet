from __future__ import annotations

import argparse
import asyncio
import random
import re
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import yaml
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configuration and domain models


class BotConfig(BaseModel):
    timezone: str = "Europe/Kyiv"
    allowed_chat_ids: list[int] = Field(default_factory=list)
    admin_user_ids: list[int] = Field(default_factory=list)


class StorageConfig(BaseModel):
    records_file: Path = Path("data/records.csv")
    notes_file: Path = Path("data/record_notes.csv")
    chat_settings_file: Path = Path("data/chat_settings.csv")
    export_directory: Path = Path("data/exports")


class IncomeConfig(BaseModel):
    default_currency: str = "UAH"
    categories: list[str] = Field(default_factory=lambda: ["other"])
    allow_custom_categories: bool = True

    @field_validator("default_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        normalized = [normalize_category(value) for value in values]
        if not normalized:
            raise ValueError("At least one category is required")
        return list(dict.fromkeys(normalized))


class PermissionsConfig(BaseModel):
    everyone_can_edit: bool = True
    author_can_delete: bool = True
    admin_can_delete: bool = True
    edit_lock_seconds: int = Field(default=120, ge=10, le=3600)


class AnalyticsConfig(BaseModel):
    default_period: Literal["today", "week", "month", "all"] = "month"
    static_preview: bool = True
    interactive_html: bool = True


class FunItem(BaseModel):
    label: str
    emoji: str
    price_uah: Decimal = Field(gt=0)
    fractional: bool = False


class FunSummaryConfig(BaseModel):
    enabled: bool = True
    comparisons_per_message: int = Field(default=3, ge=1, le=5)
    phrases: list[str] = Field(default_factory=list)
    number_phrases: dict[str, str] = Field(default_factory=dict)
    ending_phrases: dict[str, str] = Field(default_factory=dict)
    items: dict[str, FunItem] = Field(default_factory=dict)


class AppConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    income: IncomeConfig = Field(default_factory=IncomeConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    fun_summary: FunSummaryConfig = Field(default_factory=FunSummaryConfig)


class Secrets(BaseSettings):
    telegram_bot_token: str = ""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class ParsedIncome(BaseModel):
    amount: Decimal | None
    currency: str = "UAH"
    category: str = "other"
    description: str = ""
    income_date: date


class IncomeRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    telegram_message_id: int
    source_index: int = 0
    chat_id: int
    user_id: int
    username: str = ""
    original_text: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    category: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=1000)
    income_date: date
    status: Literal["saved"] = "saved"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: int

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return normalize_category(value)


class RecordNote(BaseModel):
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


class ChatSetting(BaseModel):
    chat_id: int
    enabled: bool = True
    updated_by: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


RECORD_COLUMNS = list(IncomeRecord.model_fields)
NOTE_COLUMNS = list(RecordNote.model_fields)
CHAT_SETTING_COLUMNS = list(ChatSetting.model_fields)


def normalize_category(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip().lower())
    normalized = re.sub(r"[^\w-]", "", normalized, flags=re.UNICODE)
    if not normalized:
        raise ValueError("Category must not be empty")
    return normalized[:50]


def load_config(path: Path = Path("config.yaml")) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    return AppConfig.model_validate(raw)


# Deterministic Ukrainian/English income parser


CURRENCY_ALIASES = {
    "₴": "UAH",
    "грн": "UAH",
    "гривня": "UAH",
    "гривні": "UAH",
    "гривень": "UAH",
    "uah": "UAH",
    "$": "USD",
    "usd": "USD",
    "дол": "USD",
    "долар": "USD",
    "долари": "USD",
    "доларів": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "євро": "EUR",
    "euro": "EUR",
    "euros": "EUR",
}
FUZZY_CURRENCY_WORDS = {
    alias: currency
    for alias, currency in CURRENCY_ALIASES.items()
    if alias.isalpha() and len(alias) >= 3
}
CURRENCY_TOKEN = "|".join(
    sorted((re.escape(item) for item in CURRENCY_ALIASES), key=len, reverse=True)
)
AMOUNT_TOKEN = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?"
MONEY_PATTERN = re.compile(
    rf"(?:(?P<prefix>{CURRENCY_TOKEN})\s*)?"
    rf"(?P<amount>{AMOUNT_TOKEN})"
    rf"(?:\s*(?P<suffix>{CURRENCY_TOKEN}))?",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})"
    r"(?:[./-](?P<year>\d{4}))?(?!\d)"
)
RELATIVE_DATE_PATTERN = re.compile(
    r"\b(?P<relative>сьогодні|вчора|today|yesterday)\b",
    re.IGNORECASE,
)
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "salary": ("зарплата", "зарплату", "salary", "paycheck"),
    "freelance": ("фріланс", "freelance", "проєкт", "project"),
    "sales": ("продаж", "продав", "sale", "sold"),
    "consulting": ("консультац", "consult", "advice"),
    "investment": ("дивіденд", "інвест", "dividend", "invest"),
    "gift": ("подар", "gift", "present"),
}


def decimal_from_text(value: str) -> Decimal:
    compact = value.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        amount = Decimal(compact)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value}") from error
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount.quantize(Decimal("0.01"))


def detect_category(text: str, configured: Sequence[str]) -> str:
    lowered = text.casefold()
    allowed = set(configured)
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in allowed and any(keyword in lowered for keyword in keywords):
            return category
    return "other" if "other" in allowed else configured[0]


def normalize_nearby_currency_typos(text: str) -> str:
    replacements: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^\W\d_]{3,12}", text, re.UNICODE):
        word = match.group(0).casefold()
        before = text[max(0, match.start() - 24) : match.start()]
        after = text[match.end() : match.end() + 24]
        next_to_amount = bool(
            re.search(rf"{AMOUNT_TOKEN}\s*$", before)
            or re.match(rf"^\s*{AMOUNT_TOKEN}", after)
        )
        if not next_to_amount or word in FUZZY_CURRENCY_WORDS:
            continue
        candidates = get_close_matches(
            word,
            FUZZY_CURRENCY_WORDS,
            n=1,
            cutoff=0.68,
        )
        if candidates:
            replacements.append(
                (
                    match.start(),
                    match.end(),
                    FUZZY_CURRENCY_WORDS[candidates[0]],
                )
            )
    normalized = text
    for start, end, currency in reversed(replacements):
        normalized = f"{normalized[:start]}{currency}{normalized[end:]}"
    return normalized


def parse_income_message(
    text: str,
    *,
    default_currency: str = "UAH",
    categories: Sequence[str] = ("other",),
    today: date | None = None,
) -> list[ParsedIncome]:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized_currencies = normalize_nearby_currency_typos(normalized)
    current_date = today or datetime.now(UTC).date()
    income_date = current_date
    text_without_date = normalized_currencies
    date_match = DATE_PATTERN.search(normalized_currencies)
    relative_match = RELATIVE_DATE_PATTERN.search(normalized_currencies)
    if date_match:
        year = int(date_match.group("year") or current_date.year)
        try:
            income_date = date(
                year,
                int(date_match.group("month")),
                int(date_match.group("day")),
            )
        except ValueError as error:
            raise ValueError(f"Invalid income date: {date_match.group(0)}") from error
        text_without_date = DATE_PATTERN.sub(" ", text_without_date, count=1)
    elif relative_match:
        relative = relative_match.group("relative").casefold()
        if relative in {"вчора", "yesterday"}:
            income_date = current_date - timedelta(days=1)
        text_without_date = RELATIVE_DATE_PATTERN.sub(" ", text_without_date, count=1)

    matches = list(MONEY_PATTERN.finditer(text_without_date))
    if not matches:
        return [
            ParsedIncome(
                amount=None,
                currency=default_currency,
                category=detect_category(normalized, categories),
                description=re.sub(r"\s+", " ", text_without_date).strip(),
                income_date=income_date,
            )
        ]

    description = MONEY_PATTERN.sub(" ", text_without_date)
    description = re.sub(r"\s+", " ", description).strip(" ,;:-")
    category = detect_category(normalized, categories)
    parsed: list[ParsedIncome] = []
    for match in matches:
        token = (match.group("prefix") or match.group("suffix") or "").casefold()
        currency = CURRENCY_ALIASES.get(token, default_currency.upper())
        parsed.append(
            ParsedIncome(
                amount=decimal_from_text(match.group("amount")),
                currency=currency,
                category=category,
                description=description,
                income_date=income_date,
            )
        )
    return parsed


# Pandas-backed atomic CSV repository


class CsvStorage:
    def __init__(self, config: StorageConfig):
        self.records_path = config.records_file
        self.notes_path = config.notes_file
        self.chat_settings_path = config.chat_settings_file
        self.export_directory = config.export_directory
        self._lock = asyncio.Lock()

    @staticmethod
    def _read(path: Path, columns: list[str]) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=columns)
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception as error:
            raise ValueError(f"Cannot read CSV {path}: {error}") from error
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"CSV {path} misses columns: {sorted(missing)}")
        return cast(pd.DataFrame, frame.loc[:, columns])

    @staticmethod
    def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            frame.to_csv(stream, index=False)
        temporary.replace(path)

    @staticmethod
    def _record_row(record: IncomeRecord) -> dict[str, str]:
        data = record.model_dump(mode="json")
        return {key: str(value) for key, value in data.items()}

    @staticmethod
    def _note_row(note: RecordNote) -> dict[str, str]:
        data = note.model_dump(mode="json")
        return {key: str(value) for key, value in data.items()}

    def read_records_sync(self) -> pd.DataFrame:
        if self.records_path.exists():
            frame = pd.read_csv(self.records_path, dtype=str, keep_default_na=False)
            if "income_date" not in frame.columns and "created_at" in frame.columns:
                frame["income_date"] = pd.to_datetime(
                    frame["created_at"], errors="raise", utc=True
                ).dt.date.astype(str)
                self._atomic_write(frame, self.records_path)
        return self._read(self.records_path, RECORD_COLUMNS)

    def read_notes_sync(self) -> pd.DataFrame:
        return self._read(self.notes_path, NOTE_COLUMNS)

    def read_chat_settings_sync(self) -> pd.DataFrame:
        return self._read(self.chat_settings_path, CHAT_SETTING_COLUMNS)

    def get_chat_setting_sync(self, chat_id: int) -> ChatSetting | None:
        frame = self.read_chat_settings_sync()
        rows = frame[frame["chat_id"] == str(chat_id)]
        if rows.empty:
            return None
        return ChatSetting.model_validate(rows.iloc[-1].to_dict())

    def is_chat_enabled_sync(self, chat_id: int) -> bool:
        setting = self.get_chat_setting_sync(chat_id)
        return setting.enabled if setting else True

    def set_chat_enabled_sync(
        self, chat_id: int, enabled: bool, updated_by: int
    ) -> ChatSetting:
        frame = self.read_chat_settings_sync()
        setting = ChatSetting(
            chat_id=chat_id,
            enabled=enabled,
            updated_by=updated_by,
        )
        row = {
            key: str(value) for key, value in setting.model_dump(mode="json").items()
        }
        indexes = frame.index[frame["chat_id"] == str(chat_id)].tolist()
        if indexes:
            frame.loc[indexes[-1], CHAT_SETTING_COLUMNS] = pd.Series(row)
        else:
            frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        self._atomic_write(frame, self.chat_settings_path)
        return setting

    def get_record_sync(self, record_id: str) -> IncomeRecord | None:
        frame = self.read_records_sync()
        rows = frame[frame["id"] == record_id]
        if rows.empty:
            return None
        return IncomeRecord.model_validate(rows.iloc[-1].to_dict())

    def create_record_sync(self, record: IncomeRecord) -> IncomeRecord:
        frame = self.read_records_sync()
        duplicate = frame[
            (frame["chat_id"] == str(record.chat_id))
            & (frame["telegram_message_id"] == str(record.telegram_message_id))
            & (frame["source_index"] == str(record.source_index))
        ]
        if not duplicate.empty:
            return IncomeRecord.model_validate(duplicate.iloc[-1].to_dict())
        frame = pd.concat(
            [frame, pd.DataFrame([self._record_row(record)])], ignore_index=True
        )
        self._atomic_write(frame, self.records_path)
        return record

    def update_record_sync(
        self, record_id: str, changes: dict[str, object], updated_by: int
    ) -> IncomeRecord:
        frame = self.read_records_sync()
        indexes = frame.index[frame["id"] == record_id].tolist()
        if not indexes:
            raise KeyError(f"Record not found: {record_id}")
        current = IncomeRecord.model_validate(frame.loc[indexes[-1]].to_dict())
        changes.update({"updated_at": datetime.now(UTC), "updated_by": updated_by})
        updated = current.model_copy(update=changes)
        updated = IncomeRecord.model_validate(updated.model_dump())
        frame.loc[indexes[-1], RECORD_COLUMNS] = pd.Series(self._record_row(updated))
        self._atomic_write(frame, self.records_path)
        return updated

    def delete_record_sync(self, record_id: str) -> None:
        records = self.read_records_sync()
        if not (records["id"] == record_id).any():
            raise KeyError(f"Record not found: {record_id}")
        records = cast(pd.DataFrame, records.loc[records["id"] != record_id])
        notes = self.read_notes_sync()
        notes = cast(pd.DataFrame, notes.loc[notes["record_id"] != record_id])
        self._atomic_write(records, self.records_path)
        self._atomic_write(notes, self.notes_path)

    def add_note_sync(self, note: RecordNote) -> RecordNote:
        if self.get_record_sync(note.record_id) is None:
            raise KeyError(f"Record not found: {note.record_id}")
        frame = self.read_notes_sync()
        frame = pd.concat(
            [frame, pd.DataFrame([self._note_row(note)])], ignore_index=True
        )
        self._atomic_write(frame, self.notes_path)
        return note

    async def create_record(self, record: IncomeRecord) -> IncomeRecord:
        async with self._lock:
            return await asyncio.to_thread(self.create_record_sync, record)

    async def get_record(self, record_id: str) -> IncomeRecord | None:
        return await asyncio.to_thread(self.get_record_sync, record_id)

    async def update_record(
        self, record_id: str, changes: dict[str, object], updated_by: int
    ) -> IncomeRecord:
        async with self._lock:
            return await asyncio.to_thread(
                self.update_record_sync, record_id, changes, updated_by
            )

    async def delete_record(self, record_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self.delete_record_sync, record_id)

    async def add_note(self, note: RecordNote) -> RecordNote:
        async with self._lock:
            return await asyncio.to_thread(self.add_note_sync, note)

    async def is_chat_enabled(self, chat_id: int) -> bool:
        return await asyncio.to_thread(self.is_chat_enabled_sync, chat_id)

    async def set_chat_enabled(
        self, chat_id: int, enabled: bool, updated_by: int
    ) -> ChatSetting:
        async with self._lock:
            return await asyncio.to_thread(
                self.set_chat_enabled_sync, chat_id, enabled, updated_by
            )


# Analytics and exports


def analytics_frame(
    storage: CsvStorage, timezone: str, chat_id: int | None = None
) -> pd.DataFrame:
    frame = storage.read_records_sync()
    if frame.empty:
        return frame
    if chat_id is not None:
        frame = cast(pd.DataFrame, frame.loc[frame["chat_id"] == str(chat_id)])
        if frame.empty:
            return frame
    frame = frame.copy()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["income_date"] = pd.to_datetime(frame["income_date"], errors="coerce")
    frame["created_at"] = pd.to_datetime(
        frame["created_at"], errors="coerce", utc=True
    ).dt.tz_convert(timezone)
    return frame.dropna(subset=["amount", "income_date"])


def filter_period(
    frame: pd.DataFrame, period: Literal["today", "week", "month", "all"], timezone: str
) -> pd.DataFrame:
    if frame.empty or period == "all":
        return frame
    now = datetime.now(ZoneInfo(timezone))
    dates = frame["income_date"]
    if period == "today":
        return cast(pd.DataFrame, frame.loc[dates.dt.date == now.date()])
    if period == "week":
        start = (now - timedelta(days=now.weekday())).date()
        return cast(pd.DataFrame, frame.loc[dates.dt.date >= start])
    return cast(
        pd.DataFrame,
        frame.loc[(dates.dt.year == now.year) & (dates.dt.month == now.month)],
    )


def income_summary(frame: pd.DataFrame, title: str) -> str:
    if frame.empty:
        return f"{title}\n\nЗаписів ще немає."
    by_category = cast(
        pd.Series, frame.groupby("category")["amount"].sum()
    ).sort_values(ascending=False)
    categories = "\n".join(
        f"• {name}: {value:,.2f}" for name, value in by_category.items()
    )
    return (
        f"{title}\n\n"
        f"Загалом: {frame['amount'].sum():,.2f}\n"
        f"Записів: {len(frame)}\n"
        f"Середнє: {frame['amount'].mean():,.2f}\n\n"
        f"Категорії:\n{categories}"
    )


def build_chart(frame: pd.DataFrame, output: Path) -> Path:
    if frame.empty:
        raise ValueError("No data for chart")
    daily = (
        frame.assign(day=frame["income_date"].dt.date)
        .groupby(["day", "currency"], as_index=False)["amount"]
        .sum()
    )
    figure = px.bar(
        daily,
        x="day",
        y="amount",
        color="currency",
        title="Доходи за днями",
        labels={"day": "Дата", "amount": "Сума", "currency": "Валюта"},
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(output, include_plotlyjs=True)
    return output


def build_export_zip(
    storage: CsvStorage, timezone: str, chat_id: int | None = None
) -> Path:
    storage.export_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive = storage.export_directory / f"income-export-{timestamp}.zip"
    frame = analytics_frame(storage, timezone, chat_id)
    chart = storage.export_directory / f"income-chart-{timestamp}.html"
    if not frame.empty:
        build_chart(frame, chart)
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        records_export = temporary_root / "records.csv"
        notes_export = temporary_root / "record_notes.csv"
        raw_records = storage.read_records_sync()
        if chat_id is not None:
            raw_records = cast(
                pd.DataFrame,
                raw_records.loc[raw_records["chat_id"] == str(chat_id)],
            )
        raw_records.to_csv(records_export, index=False)
        raw_notes = storage.read_notes_sync()
        record_ids = list(raw_records["id"].tolist())
        raw_notes = cast(
            pd.DataFrame,
            raw_notes.loc[raw_notes["record_id"].isin(record_ids)],
        )
        raw_notes.to_csv(notes_export, index=False)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(records_export, arcname=records_export.name)
            bundle.write(notes_export, arcname=notes_export.name)
            if chart.exists():
                bundle.write(chart, arcname=chart.name)
    if chart.exists():
        chart.unlink()
    return archive


def build_fun_summary(
    record: IncomeRecord,
    config: FunSummaryConfig,
    rng: random.Random | random.SystemRandom | None = None,
) -> str:
    if not config.enabled:
        return ""
    chooser = rng or random.SystemRandom()
    phrase = ""
    if record.amount == record.amount.to_integral_value():
        amount_key = str(int(record.amount))
        phrase = config.number_phrases.get(amount_key, "")
        if not phrase:
            for ending, ending_phrase in sorted(
                config.ending_phrases.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                if amount_key.endswith(ending):
                    phrase = ending_phrase
                    break
    if not phrase:
        phrase = chooser.choice(config.phrases) if config.phrases else "Молодець! 🔥"
    lines = [phrase]
    if record.currency != "UAH" or not config.items:
        return "\n".join(lines)

    available = [
        item for item in config.items.values() if record.amount >= item.price_uah
    ]
    if not available:
        return "\n".join([*lines, "Навіть маленький дохід — це плюс до балансу ✨"])
    selected = chooser.sample(
        available, k=min(config.comparisons_per_message, len(available))
    )
    comparisons: list[str] = []
    for item in selected:
        quantity = record.amount / item.price_uah
        value = f"{quantity:.1f}" if item.fractional else str(int(quantity))
        comparisons.append(f"{item.emoji} {value} {item.label}")
    return "\n".join(
        [
            *lines,
            "",
            "На ці гроші приблизно можна купити:",
            *comparisons,
        ]
    )


# Telegram presentation and handlers


class RecordAction(CallbackData, prefix="record"):
    action: str
    record_id: str
    value: str = ""


class EditState(StatesGroup):
    waiting_value = State()
    waiting_missing_amount = State()


class EditLocks:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[int, datetime]] = {}

    def acquire(self, record_id: str, user_id: int, seconds: int) -> bool:
        now = datetime.now(UTC)
        owner = self._locks.get(record_id)
        if owner and owner[1] > now and owner[0] != user_id:
            return False
        self._locks[record_id] = (user_id, now + timedelta(seconds=seconds))
        return True

    def release(self, record_id: str, user_id: int) -> None:
        owner = self._locks.get(record_id)
        if owner and owner[0] == user_id:
            self._locks.pop(record_id, None)


router = Router()
edit_locks = EditLocks()
APP_CONFIG: AppConfig | None = None
STORAGE: CsvStorage | None = None
RECORDS_PAGE_SIZE = 8


def app_context() -> tuple[AppConfig, CsvStorage]:
    if APP_CONFIG is None or STORAGE is None:
        raise RuntimeError("Application is not initialized")
    return APP_CONFIG, STORAGE


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗂 Записи"), KeyboardButton(text="📊 Аналітика")],
            [KeyboardButton(text="📈 Діаграма"), KeyboardButton(text="ℹ️ Допомога")],
        ],
        resize_keyboard=True,
    )


def record_keyboard(record: IncomeRecord) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, field in (
        ("💵 Сума", "amount"),
        ("💱 Валюта", "currency"),
        ("🏷 Категорія", "category"),
        ("📅 Дата", "income_date"),
        ("📝 Опис", "description"),
    ):
        builder.button(
            text=label,
            callback_data=RecordAction(action="edit", record_id=record.id, value=field),
        )
    builder.button(
        text="📋 Нотатки",
        callback_data=RecordAction(action="notes", record_id=record.id),
    )
    builder.button(
        text="➕ Нотатка",
        callback_data=RecordAction(action="note", record_id=record.id),
    )
    builder.button(
        text="🗑 Видалити",
        callback_data=RecordAction(action="delete", record_id=record.id),
    )
    builder.button(
        text="⬅️ До списку",
        callback_data=RecordAction(action="records", record_id=record.id, value="0"),
    )
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def success_keyboard(record: IncomeRecord) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗂 Відкрити record",
                    callback_data=RecordAction(
                        action="open", record_id=record.id
                    ).pack(),
                )
            ]
        ]
    )


def format_success(record: IncomeRecord, config: AppConfig) -> str:
    fun = build_fun_summary(record, config.fun_summary)
    return (
        f"✅ Записано {record.amount:,.2f} {record.currency}\n"
        f"📅 {record.income_date.strftime('%d.%m.%Y')}\n\n{fun}"
    ).strip()


def format_record(record: IncomeRecord) -> str:
    return (
        f"✅ Дохід #{record.id[:8]}\n\n"
        f"Сума: {record.amount:,.2f} {record.currency}\n"
        f"Дата: {record.income_date.strftime('%d.%m.%Y')}\n"
        f"Категорія: {record.category}\n"
        f"Опис: {record.description or '—'}\n"
        f"Оновив: {record.updated_by}"
    )


def records_list_keyboard(
    records: Sequence[IncomeRecord], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for record in records:
        label = (
            f"{record.income_date.strftime('%d.%m')} · "
            f"{record.amount:,.2f} {record.currency} · {record.category}"
        )
        builder.button(
            text=label[:64],
            callback_data=RecordAction(action="open", record_id=record.id),
        )
    if page > 0:
        builder.button(
            text="⬅️ Назад",
            callback_data=RecordAction(
                action="records", record_id="-", value=str(page - 1)
            ),
        )
    if page + 1 < total_pages:
        builder.button(
            text="Далі ➡️",
            callback_data=RecordAction(
                action="records", record_id="-", value=str(page + 1)
            ),
        )
    builder.adjust(*(1 for _ in records), 2)
    return builder.as_markup()


async def records_page(chat_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    _, storage = app_context()
    frame = await asyncio.to_thread(storage.read_records_sync)
    frame = cast(pd.DataFrame, frame.loc[frame["chat_id"] == str(chat_id)])
    if frame.empty:
        return "Записів ще немає.", InlineKeyboardMarkup(inline_keyboard=[])
    frame = frame.sort_values(
        ["income_date", "created_at"], ascending=False
    ).reset_index(drop=True)
    total_pages = max(1, (len(frame) + RECORDS_PAGE_SIZE - 1) // RECORDS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * RECORDS_PAGE_SIZE
    page_frame = frame.iloc[start : start + RECORDS_PAGE_SIZE]
    records = [
        IncomeRecord.model_validate(row.to_dict()) for _, row in page_frame.iterrows()
    ]
    text = f"🗂 Доходи · сторінка {page + 1}/{total_pages}\n\nОбери record:"
    return text, records_list_keyboard(records, page, total_pages)


def is_allowed_chat(config: AppConfig, chat_id: int) -> bool:
    return not config.bot.allowed_chat_ids or chat_id in config.bot.allowed_chat_ids


async def is_admin(message_or_query: Message | CallbackQuery, user_id: int) -> bool:
    config, _ = app_context()
    if user_id in config.bot.admin_user_ids:
        return True
    if isinstance(message_or_query, CallbackQuery):
        if not message_or_query.message:
            return False
        chat_id = message_or_query.message.chat.id
    else:
        chat_id = message_or_query.chat.id
    try:
        bot = message_or_query.bot
        if bot is None:
            return False
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in {"administrator", "creator"}


async def set_chat_mode(message: Message, enabled: bool) -> None:
    _, storage = app_context()
    if not message.from_user:
        return
    if not await is_admin(message, message.from_user.id):
        await message.answer("Цю команду можуть використовувати лише адміністратори.")
        return
    await storage.set_chat_enabled(
        message.chat.id, enabled=enabled, updated_by=message.from_user.id
    )
    if enabled:
        await message.answer("🟢 Запис доходів увімкнено для цього чату.")
    else:
        await message.answer(
            "🔴 Запис доходів вимкнено. Нові звичайні повідомлення ігноруються."
        )


@router.message(Command("turn_on"))
async def turn_on_handler(message: Message) -> None:
    await set_chat_mode(message, enabled=True)


@router.message(Command("turn_off"))
async def turn_off_handler(message: Message) -> None:
    await set_chat_mode(message, enabled=False)


@router.message(Command("status"))
async def status_handler(message: Message) -> None:
    _, storage = app_context()
    enabled = await storage.is_chat_enabled(message.chat.id)
    if enabled:
        await message.answer("🟢 Запис доходів увімкнено для цього чату.")
    else:
        await message.answer("🔴 Запис доходів вимкнено для цього чату.")


@router.message(Command("start", "help"))
@router.message(F.text == "ℹ️ Допомога")
async def help_handler(message: Message) -> None:
    await message.answer(
        "Надішли повідомлення про дохід звичайним текстом.\n\n"
        "Приклади:\n"
        "• Отримав 1500 грн за консультацію\n"
        "• Earned $250 for design\n"
        "• Продаж 2300\n\n"
        "Валюта без позначення — UAH. Будь-яке поле можна змінити кнопками.\n\n"
        "/status — поточний режим запису\n"
        "/turn_on — увімкнути запис (admin)\n"
        "/turn_off — вимкнути запис (admin)",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.from_user and (record_id := data.get("record_id")):
        edit_locks.release(str(record_id), message.from_user.id)
    await state.clear()
    await message.answer("Дію скасовано.", reply_markup=main_menu())


@router.message(EditState.waiting_value)
async def edit_value_handler(message: Message, state: FSMContext) -> None:
    config, storage = app_context()
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    record_id = str(data["record_id"])
    field = str(data["field"])
    raw = message.text.strip()
    try:
        if field == "note":
            note = RecordNote(
                record_id=record_id,
                user_id=message.from_user.id,
                username=message.from_user.username or "",
                text=raw,
            )
            await storage.add_note(note)
            await state.clear()
            await message.answer("📝 Нотатку додано.")
            return
        if field == "amount":
            value: object = decimal_from_text(raw)
        elif field == "currency":
            value = raw.upper()
            if value not in {"UAH", "USD", "EUR"}:
                raise ValueError("Підтримуються UAH, USD та EUR.")
        elif field == "category":
            value = normalize_category(raw)
            if (
                not config.income.allow_custom_categories
                and value not in config.income.categories
            ):
                raise ValueError("Такої категорії немає.")
        elif field == "income_date":
            parsed_date = DATE_PATTERN.fullmatch(raw)
            if not parsed_date:
                raise ValueError("Використай формат 10.07 або 10.07.2026.")
            current_date = datetime.now(ZoneInfo(config.bot.timezone)).date()
            value = date(
                int(parsed_date.group("year") or current_date.year),
                int(parsed_date.group("month")),
                int(parsed_date.group("day")),
            )
        else:
            value = raw
        record = await storage.update_record(
            record_id, {field: value}, message.from_user.id
        )
    except (ValueError, ValidationError) as error:
        await message.answer(f"Некоректне значення: {error}. Спробуй ще раз.")
        return
    finally:
        edit_locks.release(record_id, message.from_user.id)
    await state.clear()
    await message.answer(format_record(record), reply_markup=record_keyboard(record))


@router.message(EditState.waiting_missing_amount)
async def missing_amount_handler(message: Message, state: FSMContext) -> None:
    config, storage = app_context()
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    try:
        amount = decimal_from_text(message.text)
        record = IncomeRecord(
            telegram_message_id=int(data["telegram_message_id"]),
            chat_id=int(data["chat_id"]),
            user_id=int(data["user_id"]),
            username=str(data["username"]),
            original_text=str(data["original_text"]),
            amount=amount,
            currency=config.income.default_currency,
            category=str(data["category"]),
            description=str(data["description"]),
            income_date=date.fromisoformat(str(data["income_date"])),
            updated_by=message.from_user.id,
        )
        await storage.create_record(record)
    except (ValueError, ValidationError) as error:
        await message.answer(f"Введи додатну суму, наприклад 1500: {error}")
        return
    await state.clear()
    await message.answer(
        format_success(record, config), reply_markup=success_keyboard(record)
    )


@router.callback_query(RecordAction.filter(F.action == "edit"))
async def edit_callback(
    query: CallbackQuery, callback_data: RecordAction, state: FSMContext
) -> None:
    config, storage = app_context()
    if not query.from_user:
        return
    record = await storage.get_record(callback_data.record_id)
    if record is None:
        await query.answer("Запис не знайдено.", show_alert=True)
        return
    if not edit_locks.acquire(
        record.id, query.from_user.id, config.permissions.edit_lock_seconds
    ):
        await query.answer("Цей запис зараз редагує інший учасник.", show_alert=True)
        return
    await state.set_state(EditState.waiting_value)
    await state.set_data({"record_id": record.id, "field": callback_data.value})
    await query.answer()
    if query.message:
        await query.message.answer(
            f"Надішли нове значення для «{callback_data.value}» або /cancel."
        )


@router.callback_query(RecordAction.filter(F.action == "open"))
async def open_record_callback(
    query: CallbackQuery, callback_data: RecordAction
) -> None:
    _, storage = app_context()
    record = await storage.get_record(callback_data.record_id)
    if record is None:
        await query.answer("Запис не знайдено.", show_alert=True)
        return
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(
            format_record(record), reply_markup=record_keyboard(record)
        )


@router.callback_query(RecordAction.filter(F.action == "records"))
async def records_page_callback(
    query: CallbackQuery, callback_data: RecordAction
) -> None:
    if not isinstance(query.message, Message):
        return
    try:
        page = int(callback_data.value or "0")
    except ValueError:
        page = 0
    text, keyboard = await records_page(query.message.chat.id, page)
    await query.answer()
    await query.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(RecordAction.filter(F.action == "notes"))
async def notes_callback(query: CallbackQuery, callback_data: RecordAction) -> None:
    _, storage = app_context()
    record = await storage.get_record(callback_data.record_id)
    if record is None:
        await query.answer("Запис не знайдено.", show_alert=True)
        return
    notes = await asyncio.to_thread(storage.read_notes_sync)
    notes = cast(
        pd.DataFrame,
        notes.loc[notes["record_id"] == callback_data.record_id],
    )
    if notes.empty:
        text = f"📋 Нотатки до #{record.id[:8]}\n\nНотаток ще немає."
    else:
        lines = [
            f"• {row['text']} — @{row['username'] or row['user_id']}"
            for _, row in notes.iterrows()
        ]
        text = f"📋 Нотатки до #{record.id[:8]}\n\n" + "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Додати",
                    callback_data=RecordAction(
                        action="note", record_id=record.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="⬅️ До record",
                    callback_data=RecordAction(
                        action="open", record_id=record.id
                    ).pack(),
                ),
            ]
        ]
    )
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(RecordAction.filter(F.action == "note"))
async def note_callback(
    query: CallbackQuery, callback_data: RecordAction, state: FSMContext
) -> None:
    if not query.from_user:
        return
    await state.set_state(EditState.waiting_value)
    await state.set_data({"record_id": callback_data.record_id, "field": "note"})
    await query.answer()
    if query.message:
        await query.message.answer("Надішли текст нотатки або /cancel.")


@router.callback_query(RecordAction.filter(F.action == "delete"))
async def delete_callback(query: CallbackQuery, callback_data: RecordAction) -> None:
    _, storage = app_context()
    record = await storage.get_record(callback_data.record_id)
    if record is None or not query.from_user:
        await query.answer("Запис не знайдено.", show_alert=True)
        return
    config, _ = app_context()
    allowed = (
        config.permissions.author_can_delete and record.user_id == query.from_user.id
    ) or (
        config.permissions.admin_can_delete
        and await is_admin(query, query.from_user.id)
    )
    if not allowed:
        await query.answer("Недостатньо прав.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Так, видалити",
                    callback_data=RecordAction(
                        action="confirm_delete", record_id=record.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="↩️ Скасувати",
                    callback_data=RecordAction(
                        action="cancel", record_id=record.id
                    ).pack(),
                ),
            ]
        ]
    )
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(RecordAction.filter(F.action == "confirm_delete"))
async def confirm_delete_callback(
    query: CallbackQuery, callback_data: RecordAction
) -> None:
    _, storage = app_context()
    await storage.delete_record(callback_data.record_id)
    await query.answer("Запис видалено.")
    if isinstance(query.message, Message):
        await query.message.edit_text("🗑 Запис видалено.")


@router.callback_query(RecordAction.filter(F.action == "cancel"))
async def cancel_callback(query: CallbackQuery, callback_data: RecordAction) -> None:
    _, storage = app_context()
    record = await storage.get_record(callback_data.record_id)
    await query.answer("Скасовано.")
    if isinstance(query.message, Message) and record:
        await query.message.edit_reply_markup(reply_markup=record_keyboard(record))


async def send_stats(message: Message, period: str = "month") -> None:
    config, storage = app_context()
    frame = await asyncio.to_thread(
        analytics_frame, storage, config.bot.timezone, message.chat.id
    )
    frame = filter_period(frame, period, config.bot.timezone)  # type: ignore[arg-type]
    titles = {
        "today": "За сьогодні",
        "week": "За поточний тиждень",
        "month": "За поточний місяць",
        "all": "За весь час",
    }
    await message.answer(income_summary(frame, titles.get(period, "Статистика")))


@router.message(Command("stats"))
@router.message(F.text == "📊 Аналітика")
async def stats_handler(message: Message) -> None:
    await send_stats(message)


@router.message(Command("chart"))
@router.message(F.text == "📈 Діаграма")
async def chart_handler(message: Message) -> None:
    config, storage = app_context()
    frame = await asyncio.to_thread(
        analytics_frame, storage, config.bot.timezone, message.chat.id
    )
    frame = filter_period(frame, config.analytics.default_period, config.bot.timezone)
    if frame.empty:
        await message.answer("Недостатньо даних для діаграми.")
        return
    output = config.storage.export_directory / f"chart-{uuid4().hex[:8]}.html"
    await asyncio.to_thread(build_chart, frame, output)
    await message.answer_document(FSInputFile(output), caption="Інтерактивна діаграма")
    output.unlink(missing_ok=True)


@router.message(Command("export"))
async def export_handler(message: Message) -> None:
    config, storage = app_context()
    if not message.from_user or not await is_admin(message, message.from_user.id):
        await message.answer("Експорт доступний лише адміністраторам.")
        return
    archive = await asyncio.to_thread(
        build_export_zip, storage, config.bot.timezone, message.chat.id
    )
    await message.answer_document(FSInputFile(archive))


@router.message(Command("records"))
@router.message(F.text == "🗂 Записи")
async def records_handler(message: Message) -> None:
    text, keyboard = await records_page(message.chat.id, 0)
    await message.answer(text, reply_markup=keyboard)


@router.message(F.text)
async def income_message_handler(message: Message, state: FSMContext) -> None:
    config, storage = app_context()
    if (
        not message.text
        or not message.from_user
        or message.from_user.is_bot
        or message.text.startswith("/")
        or not is_allowed_chat(config, message.chat.id)
    ):
        return
    if not await storage.is_chat_enabled(message.chat.id):
        return
    parsed_items = parse_income_message(
        message.text,
        default_currency=config.income.default_currency,
        categories=config.income.categories,
        today=datetime.now(ZoneInfo(config.bot.timezone)).date(),
    )
    if parsed_items[0].amount is None:
        item = parsed_items[0]
        await state.set_state(EditState.waiting_missing_amount)
        await state.set_data(
            {
                "telegram_message_id": message.message_id,
                "chat_id": message.chat.id,
                "user_id": message.from_user.id,
                "username": message.from_user.username or "",
                "original_text": message.text,
                "category": item.category,
                "description": item.description,
                "income_date": item.income_date.isoformat(),
            }
        )
        await message.reply("Не знайшов суму. Надішли суму числом або /cancel.")
        return
    for index, item in enumerate(parsed_items):
        if item.amount is None:
            continue
        record = IncomeRecord(
            telegram_message_id=message.message_id,
            source_index=index,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            username=message.from_user.username or "",
            original_text=message.text,
            amount=item.amount,
            currency=item.currency,
            category=item.category,
            description=item.description,
            income_date=item.income_date,
            updated_by=message.from_user.id,
        )
        record = await storage.create_record(record)
        await message.reply(
            format_success(record, config), reply_markup=success_keyboard(record)
        )


# CLI


async def run_bot(config: AppConfig) -> None:
    global APP_CONFIG, STORAGE
    secrets = Secrets()
    if not secrets.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")
    APP_CONFIG = config
    STORAGE = CsvStorage(config.storage)
    bot = Bot(secrets.telegram_bot_token)
    await bot.set_my_commands(
        [
            BotCommand(command="help", description="Допомога"),
            BotCommand(command="records", description="Останні записи"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="chart", description="Діаграма"),
            BotCommand(command="status", description="Режим запису"),
            BotCommand(command="turn_on", description="Увімкнути запис"),
            BotCommand(command="turn_off", description="Вимкнути запис"),
            BotCommand(command="cancel", description="Скасувати дію"),
        ]
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Income Telegram bot")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bot")
    commands.add_parser("check-config")
    commands.add_parser("check-storage")
    parse_command = commands.add_parser("parse")
    parse_command.add_argument("message")
    analytics_command = commands.add_parser("analytics")
    analytics_command.add_argument(
        "--period", choices=("today", "week", "month", "all"), default="month"
    )
    commands.add_parser("export")
    return parser


def cli() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    storage = CsvStorage(config.storage)
    if args.command == "bot":
        asyncio.run(run_bot(config))
    elif args.command == "check-config":
        ZoneInfo(config.bot.timezone)
        print("Configuration is valid.")
    elif args.command == "check-storage":
        storage.read_records_sync()
        storage.read_notes_sync()
        storage.read_chat_settings_sync()
        print("Storage is valid.")
    elif args.command == "parse":
        results = parse_income_message(
            args.message,
            default_currency=config.income.default_currency,
            categories=config.income.categories,
        )
        for result in results:
            print(result.model_dump_json(indent=2))
    elif args.command == "analytics":
        frame = analytics_frame(storage, config.bot.timezone)
        frame = filter_period(frame, args.period, config.bot.timezone)
        print(income_summary(frame, f"Period: {args.period}"))
    elif args.command == "export":
        print(build_export_zip(storage, config.bot.timezone))


if __name__ == "__main__":
    cli()
