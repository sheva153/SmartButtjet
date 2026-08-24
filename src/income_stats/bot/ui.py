"""Telegram callbacks, states, keyboards, and presentation helpers."""

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from income_stats.models import CHART_PERIODS, IncomeRecord

MENU_RECORDS = "🗂 Записи"
MENU_ANALYTICS = "📊 Аналітика"
MENU_CHART = "📈 Діаграма"
MENU_HELP = "ℹ️ Допомога"
MENU_LABELS = frozenset({MENU_RECORDS, MENU_ANALYTICS, MENU_CHART, MENU_HELP})


class RecordAction(CallbackData, prefix="record"):
    action: str
    record_id: str
    value: str = ""


class ChartPeriod(CallbackData, prefix="chart"):
    period: str


CHART_PERIOD_LABELS = {
    "week": "📅 Тиждень",
    "month": "🗓 Місяць",
    "year": "📆 Рік",
    "last_week": "📅 Мин. тиждень",
    "last_month": "🗓 Мин. місяць",
    "last_year": "📆 Мин. рік",
}


def chart_period_keyboard():
    builder = InlineKeyboardBuilder()
    for period in CHART_PERIODS:
        builder.button(
            text=CHART_PERIOD_LABELS[period],
            callback_data=ChartPeriod(period=period),
        )
    builder.adjust(3, 3)
    return builder.as_markup()


class EditState(StatesGroup):
    waiting_value = State()


def short_id(record_id: str) -> str:
    return record_id[:8]


def format_labels(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "—"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_RECORDS), KeyboardButton(text=MENU_ANALYTICS)],
            [KeyboardButton(text=MENU_CHART), KeyboardButton(text=MENU_HELP)],
        ],
        resize_keyboard=True,
    )


def record_keyboard(record: IncomeRecord):
    builder = InlineKeyboardBuilder()
    for label, field in (
        ("💵 Сума", "amount"),
        ("💱 Валюта", "currency"),
        ("🏷 Категорії", "categories"),
        ("🔖 Теги", "tags"),
        ("📅 Дата", "income_date"),
        ("📝 Опис", "description"),
    ):
        builder.button(
            text=label,
            callback_data=RecordAction(action="edit", record_id=record.id, value=field),
        )
    for label, action in (
        ("📋 Нотатки", "notes"),
        ("➕ Нотатка", "note"),
        ("🗑 Видалити", "delete"),
        ("⬅️ До списку", "records"),
    ):
        builder.button(
            text=label, callback_data=RecordAction(action=action, record_id=record.id)
        )
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def success_keyboard(record: IncomeRecord):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗂 Відкрити запис",
        callback_data=RecordAction(action="open", record_id=record.id),
    )
    return builder.as_markup()


def _signed_amount(record: IncomeRecord) -> str:
    sign = "−" if record.type == "expense" else ""
    return f"{sign}{record.amount:,.2f}"


def records_keyboard(records: Sequence[IncomeRecord], page: int, total_pages: int):
    builder = InlineKeyboardBuilder()
    for record in records:
        builder.button(
            text=(
                f"{record.income_date:%d.%m} · {_signed_amount(record)} "
                f"{record.currency} · {format_labels(record.categories)}"
                + (f" · 🔖{format_labels(record.tags)}" if record.tags else "")
            )[:64],
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


def format_record(record: IncomeRecord) -> str:
    label = "Витрата" if record.type == "expense" else "Дохід"
    return (
        f"✅ {label} #{short_id(record.id)}\n\n"
        f"Сума: {_signed_amount(record)} {record.currency}\n"
        f"Дата: {record.income_date:%d.%m.%Y}\n"
        f"Категорії: {format_labels(record.categories)}\n"
        f"Теги: {format_labels(record.tags)}\n"
        f"Опис: {record.description or '—'}"
    )


def format_success(record: IncomeRecord, fun: str = "") -> str:
    return (
        f"✅ Записано {_signed_amount(record)} {record.currency}\n"
        f"📅 {record.income_date:%d.%m.%Y}\n\n{fun}"
    ).strip()
