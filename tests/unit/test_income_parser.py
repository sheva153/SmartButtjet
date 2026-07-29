from datetime import date
from decimal import Decimal

import pytest

from income_stats.config.settings import IncomeConfig
from income_stats.parsers.income_parser import (
    find_protected_spans,
    parse_income_message,
)


@pytest.fixture
def income_config() -> IncomeConfig:
    return IncomeConfig(
        default_currency="UAH",
        categories={
            "salary": ["зарплата", "зп"],
            "debt": ["борг", "повернули борг"],
            "sales": ["продаж"],
            "other": [],
        },
        tags={
            "card": ["картка", "на картку"],
            "cash": ["готівкою"],
        },
    )


@pytest.mark.parametrize(
    "text",
    [
        "зустріч о 00:00",
        "зустріч о 15:30",
        "зустріч о 23:59",
        "телефон +380 67 123 45 67",
        "телефон +380-67-123-45-67",
        "телефон 067-123-45-67",
        "телефон 67 123 45 67",
        "тел. 67-123-45-67",
        "tel 67 123 45 67",
        "call me at +1 (202) 555-0123",
        "вул. Шевченка, будинок 12, квартира 35",
        "будинок №12",
        "apartment #35",
        "вул. Шевченка, 12",
        "street Baker 221B",
        "street Baker, house 221B, apartment 5",
        "подія 10.07.2026",
    ],
)
def test_non_money_patterns_are_ignored(text: str, income_config: IncomeConfig) -> None:
    assert parse_income_message(text, income_config) == []


def test_protected_spans_keep_original_positions() -> None:
    text = "отримав 500, телефон +380 67 123 45 67"

    spans = find_protected_spans(text)

    assert [text[start:end] for start, end in spans] == ["+380 67 123 45 67"]


def test_money_next_to_phone_is_kept(income_config: IncomeConfig) -> None:
    messages = [
        "отримав 500, телефон +380 67 123 45 67",
        "отримав 500, телефон +380-67-123-45-67",
        "отримав 500, телефон +44-20-7946-0958",
    ]

    for message in messages:
        parsed = parse_income_message(message, income_config)
        assert [item.amount for item in parsed] == [Decimal("500.00")]


def test_money_next_to_address_and_time_is_kept(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "о 9:30 отримав 1500 грн, будинок 12",
        income_config,
    )

    assert [item.amount for item in parsed] == [Decimal("1500.00")]


def test_address_fraction_is_not_used_as_an_income_date(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "будинок 12/3 отримав 500",
        income_config,
        today=date(2026, 7, 29),
    )

    assert [item.amount for item in parsed] == [Decimal("500.00")]
    assert parsed[0].income_date == date(2026, 7, 29)


@pytest.mark.parametrize(
    "text",
    [
        "на вул. Шевченка отримав 500",
        "продав квартиру отримав 500 грн",
        "будинок продав за 500",
        "apartment sold for 500 USD",
        "квиток 500",
    ],
)
def test_address_words_do_not_swallow_later_money(
    text: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert [item.amount for item in parsed] == [Decimal("500.00")]


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("500", Decimal("500.00"), "UAH"),
        ("Отримав 1500 грн за консультацію", Decimal("1500.00"), "UAH"),
        ("Earned $250 for design", Decimal("250.00"), "USD"),
        ("Payment 300 EUR", Decimal("300.00"), "EUR"),
        ("Продаж 2 300", Decimal("2300.00"), "UAH"),
        ("Заробив 99,50 гривень", Decimal("99.50"), "UAH"),
    ],
)
def test_permissive_amount_and_currency_parsing(
    text: str,
    amount: Decimal,
    currency: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert parsed[0].amount == amount
    assert parsed[0].currency == currency


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Отримав 100 жвро", "EUR"),
        ("Payment 100 euroo", "EUR"),
        ("Отримав 100 доллар", "USD"),
        ("Отримав 100 гривен", "UAH"),
    ],
)
def test_nearby_currency_typos_remain_supported(
    text: str,
    currency: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert parsed[0].amount == Decimal("100.00")
    assert parsed[0].currency == currency


@pytest.mark.parametrize("text", ["100 usda", "100 eurocentric"])
def test_currency_aliases_do_not_match_word_prefixes(
    text: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert parsed[0].amount == Decimal("100.00")
    assert parsed[0].currency == "UAH"


def test_standalone_hour_remains_an_amount_candidate(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message("зустріч о 15", income_config)

    assert [item.amount for item in parsed] == [Decimal("15.00")]


@pytest.mark.parametrize(
    "structured",
    [
        "123.456",
        "192.168.1.1",
        "2026-07-29",
        "25:00",
        "123456789012345678901234567890",
        "1 234 567 890 123 456 789",
    ],
)
def test_structured_or_unreasonable_tokens_are_not_partially_parsed(
    structured: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        f"службове значення {structured}, отримав 500",
        income_config,
        today=date(2026, 7, 29),
    )

    assert [item.amount for item in parsed] == [Decimal("500.00")]


def test_invalid_candidate_does_not_abort_later_valid_money(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message("службове значення 0, отримав 500", income_config)

    assert [item.amount for item in parsed] == [Decimal("500.00")]


def test_multiple_amounts_create_multiple_items(income_config: IncomeConfig) -> None:
    parsed = parse_income_message(
        "Продаж 1000 грн і ще 500 грн готівкою", income_config
    )

    assert [item.amount for item in parsed] == [
        Decimal("1000.00"),
        Decimal("500.00"),
    ]
    assert all(item.categories == ["sales"] for item in parsed)
    assert all(item.tags == ["cash"] for item in parsed)


def test_multiple_spaces_are_valid_thousands_grouping(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message("зп 20  000", income_config)

    assert [item.amount for item in parsed] == [Decimal("20000.00")]


def test_multiple_categories_and_tags_are_detected(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message("зп 20 000 на картку, повернули борг", income_config)

    assert parsed[0].categories == ["salary", "debt"]
    assert parsed[0].tags == ["card"]


def test_unknown_taxonomy_falls_back_to_other(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message("500 за роботу", income_config)

    assert parsed[0].categories == ["other"]
    assert parsed[0].tags == []


def test_taxonomy_aliases_are_word_bounded(income_config: IncomeConfig) -> None:
    parsed = parse_income_message("незп 500 псевдоборг", income_config)

    assert parsed[0].categories == ["other"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Отримав 800 грн 10.07", date(2026, 7, 10)),
        ("Earned $50 10/07/2025", date(2025, 7, 10)),
        ("Отримав 200 вчора", date(2026, 7, 23)),
        ("Earned 300 today", date(2026, 7, 24)),
    ],
)
def test_income_date_is_extracted_without_becoming_an_amount(
    text: str,
    expected: date,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        text,
        income_config,
        today=date(2026, 7, 24),
    )

    assert len(parsed) == 1
    assert parsed[0].income_date == expected


def test_invalid_absolute_date_does_not_abort_valid_money(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "Отримав 500 грн 32.13",
        income_config,
        today=date(2026, 7, 24),
    )

    assert [item.amount for item in parsed] == [Decimal("500.00")]
    assert parsed[0].income_date == date(2026, 7, 24)


def test_description_is_truncated_to_record_limit(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(f"{'x' * 1100} 500", income_config)

    assert len(parsed[0].description) == 1000
    assert parsed[0].description == "x" * 1000
