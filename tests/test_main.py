import random
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from main import (
    AppConfig,
    CsvStorage,
    FunItem,
    FunSummaryConfig,
    IncomeRecord,
    RecordNote,
    StorageConfig,
    analytics_frame,
    build_export_zip,
    build_fun_summary,
    decimal_from_text,
    filter_period,
    income_summary,
    load_config,
    parse_income_message,
    records_list_keyboard,
)


@pytest.fixture
def storage(tmp_path: Path) -> CsvStorage:
    return CsvStorage(
        StorageConfig(
            records_file=tmp_path / "records.csv",
            notes_file=tmp_path / "notes.csv",
            chat_settings_file=tmp_path / "chat_settings.csv",
            export_directory=tmp_path / "exports",
        )
    )


def make_record(**changes: object) -> IncomeRecord:
    data: dict[str, object] = {
        "telegram_message_id": 10,
        "chat_id": -100,
        "user_id": 42,
        "username": "felix",
        "original_text": "Отримав 1500 грн за консультацію",
        "amount": Decimal("1500"),
        "currency": "UAH",
        "category": "consulting",
        "description": "Отримав за консультацію",
        "income_date": date(2026, 7, 10),
        "created_at": datetime(2026, 7, 24, 10, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 24, 10, tzinfo=UTC),
        "updated_by": 42,
    }
    data.update(changes)
    return IncomeRecord.model_validate(data)


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("Отримав 1500 грн за консультацію", Decimal("1500.00"), "UAH"),
        ("Earned $250 for design", Decimal("250.00"), "USD"),
        ("Payment 300 EUR", Decimal("300.00"), "EUR"),
        ("Продаж 2 300", Decimal("2300.00"), "UAH"),
        ("Заробив 99,50 гривень", Decimal("99.50"), "UAH"),
    ],
)
def test_parser_amount_and_currency(text: str, amount: Decimal, currency: str) -> None:
    parsed = parse_income_message(
        text,
        default_currency="UAH",
        categories=["consulting", "sales", "freelance", "other"],
    )
    assert parsed[0].amount == amount
    assert parsed[0].currency == currency


def test_parser_creates_item_for_each_amount() -> None:
    parsed = parse_income_message("Дизайн 1000 грн і консультація 500 грн")
    assert [item.amount for item in parsed] == [
        Decimal("1000.00"),
        Decimal("500.00"),
    ]


def test_parser_missing_amount_returns_manual_draft() -> None:
    parsed = parse_income_message("Консультація для нового клієнта")
    assert parsed[0].amount is None
    assert parsed[0].currency == "UAH"


def test_parser_suggests_category() -> None:
    parsed = parse_income_message("Продаж 800 грн", categories=["sales", "other"])
    assert parsed[0].category == "sales"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Отримав 800 грн 10.07", date(2026, 7, 10)),
        ("Earned $50 10/07/2025", date(2025, 7, 10)),
        ("Отримав 200 вчора", date(2026, 7, 23)),
        ("Earned 300 today", date(2026, 7, 24)),
    ],
)
def test_parser_income_date(text: str, expected: date) -> None:
    parsed = parse_income_message(text, today=date(2026, 7, 24))
    assert parsed[0].income_date == expected


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Отримав 100 жвро", "EUR"),
        ("Отримав 100 евро", "EUR"),
        ("Payment 100 euroo", "EUR"),
        ("Отримав 100 доллар", "USD"),
        ("Отримав 100 гривен", "UAH"),
    ],
)
def test_parser_tolerates_currency_typos(text: str, currency: str) -> None:
    parsed = parse_income_message(text)
    assert parsed[0].amount == Decimal("100.00")
    assert parsed[0].currency == currency


def test_date_is_not_parsed_as_amount() -> None:
    parsed = parse_income_message("Отримав 1500 грн 10.07", today=date(2026, 7, 24))
    assert [item.amount for item in parsed] == [Decimal("1500.00")]


def test_decimal_rejects_non_positive_amount() -> None:
    with pytest.raises(ValueError, match="positive"):
        decimal_from_text("0")


def test_load_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "bot:\n  timezone: Europe/Kyiv\nincome:\n  categories: [sales, other]\n",
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert isinstance(config, AppConfig)
    assert config.income.categories == ["sales", "other"]


def test_storage_create_and_prevent_duplicate(storage: CsvStorage) -> None:
    record = make_record()
    first = storage.create_record_sync(record)
    second = storage.create_record_sync(record.model_copy(update={"id": "another"}))
    assert first.id == second.id
    assert len(storage.read_records_sync()) == 1


def test_storage_update_record(storage: CsvStorage) -> None:
    record = storage.create_record_sync(make_record())
    updated = storage.update_record_sync(
        record.id, {"category": "sales", "amount": Decimal("1700")}, 99
    )
    assert updated.category == "sales"
    assert updated.amount == Decimal("1700")
    assert updated.updated_by == 99


def test_storage_add_note_and_delete_record(storage: CsvStorage) -> None:
    record = storage.create_record_sync(make_record())
    storage.add_note_sync(
        RecordNote(record_id=record.id, user_id=7, text="Оплачено готівкою")
    )
    assert len(storage.read_notes_sync()) == 1
    storage.delete_record_sync(record.id)
    assert storage.read_records_sync().empty
    assert storage.read_notes_sync().empty


def test_storage_reports_damaged_schema(storage: CsvStorage) -> None:
    storage.records_path.parent.mkdir(parents=True, exist_ok=True)
    storage.records_path.write_text("wrong,column\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="misses columns"):
        storage.read_records_sync()


def test_chat_recording_is_enabled_by_default(storage: CsvStorage) -> None:
    assert storage.is_chat_enabled_sync(-100) is True


def test_chat_recording_mode_is_persisted_per_chat(storage: CsvStorage) -> None:
    storage.set_chat_enabled_sync(-100, enabled=False, updated_by=42)
    storage.set_chat_enabled_sync(-200, enabled=True, updated_by=99)
    assert storage.is_chat_enabled_sync(-100) is False
    assert storage.is_chat_enabled_sync(-200) is True
    setting = storage.get_chat_setting_sync(-100)
    assert setting is not None
    assert setting.updated_by == 42


def test_analytics_summary(storage: CsvStorage) -> None:
    storage.create_record_sync(make_record())
    storage.create_record_sync(
        make_record(
            id="second",
            telegram_message_id=11,
            amount=Decimal("500"),
            category="sales",
        )
    )
    frame = analytics_frame(storage, "Europe/Kyiv")
    summary = income_summary(frame, "Test")
    assert "2,000.00" in summary
    assert "Записів: 2" in summary


def test_filter_period_today() -> None:
    frame = pd.DataFrame(
        {
            "income_date": pd.to_datetime([datetime.now(UTC).date(), date(2020, 1, 1)]),
            "amount": [100, 200],
        }
    )
    result = filter_period(frame, "today", "Europe/Kyiv")
    assert len(result) == 1


def test_export_zip(storage: CsvStorage) -> None:
    storage.create_record_sync(make_record())
    archive = build_export_zip(storage, "Europe/Kyiv")
    assert archive.exists()
    assert archive.suffix == ".zip"


def test_fun_summary_uses_configured_phrase_and_three_items() -> None:
    config = FunSummaryConfig(
        comparisons_per_message=3,
        phrases=["Гаманець аплодує!"],
        items={
            "burger": FunItem(label="бургерів", emoji="🍔", price_uah=Decimal("150")),
            "coffee": FunItem(label="чашок кави", emoji="☕", price_uah=Decimal("50")),
            "cucumber": FunItem(label="огірків", emoji="🥒", price_uah=Decimal("25")),
            "iphone": FunItem(label="айфонів", emoji="📱", price_uah=Decimal("45000")),
        },
    )
    summary = build_fun_summary(make_record(), config, random.Random(7))
    assert "Гаманець аплодує!" in summary
    assert "На ці гроші приблизно можна купити:" in summary
    assert sum(emoji in summary for emoji in ("🍔", "☕", "🥒")) == 3
    assert "📱" not in summary


def test_fun_summary_skips_comparisons_for_foreign_currency() -> None:
    config = FunSummaryConfig(
        phrases=["Красиво!"],
        items={
            "burger": FunItem(label="бургерів", emoji="🍔", price_uah=Decimal("150")),
        },
    )
    summary = build_fun_summary(make_record(currency="USD"), config, random.Random(1))
    assert summary == "Красиво!"


def test_fun_summary_prefers_exact_number_phrase() -> None:
    config = FunSummaryConfig(
        phrases=["Випадкова фраза"],
        number_phrases={"100": "Перша соточка пішла! 💯"},
        ending_phrases={"00": "Кругла сума"},
    )
    summary = build_fun_summary(
        make_record(amount=Decimal("100")), config, random.Random(1)
    )
    assert summary == "Перша соточка пішла! 💯"


def test_fun_summary_uses_number_ending() -> None:
    config = FunSummaryConfig(
        phrases=["Випадкова фраза"],
        ending_phrases={"77": "Подвійна сімка!"},
    )
    summary = build_fun_summary(
        make_record(amount=Decimal("1277")), config, random.Random(1)
    )
    assert summary == "Подвійна сімка!"


def test_records_list_has_only_open_buttons() -> None:
    keyboard = records_list_keyboard([make_record()], page=0, total_pages=1)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert len(buttons) == 1
    assert buttons[0].text.startswith("10.07")
    assert "record:open:" in (buttons[0].callback_data or "")


def test_analytics_are_isolated_by_chat(storage: CsvStorage) -> None:
    storage.create_record_sync(make_record())
    storage.create_record_sync(
        make_record(
            id="other-chat",
            telegram_message_id=11,
            chat_id=-200,
            amount=Decimal("9999"),
        )
    )
    frame = analytics_frame(storage, "Europe/Kyiv", chat_id=-100)
    assert frame["amount"].sum() == 1500
    assert set(frame["chat_id"]) == {"-100"}


def test_existing_csv_is_migrated_with_income_date(storage: CsvStorage) -> None:
    record = make_record()
    row = storage._record_row(record)
    row.pop("income_date")
    pd.DataFrame([row]).to_csv(storage.records_path, index=False)
    migrated = storage.read_records_sync()
    assert migrated.iloc[0]["income_date"] == "2026-07-24"
