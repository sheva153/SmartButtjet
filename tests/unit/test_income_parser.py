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
        "моб. 67 123 45 67",
        "мобільний 67-123-45-67",
        "phone 67 123 45 67",
        "mobile 67-123-45-67",
        "telephone 202 555 0123",
        "mobile (202) 555-0123",
        "call me at +1 (202) 555-0123",
        "вул. Шевченка, будинок 12, квартира 35",
        "будинок №12",
        "apartment #35",
        "вул. Шевченка, 12",
        "вул. Івана Франка 12",
        "вулиця Героїв Небесної Сотні 12",
        "street Baker 221B",
        "street Martin Luther King 12",
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


def test_only_valid_clock_time_is_a_protected_span() -> None:
    valid = "зустріч о 15:30"
    invalid = ["значення 25:00", "значення 15:30:20"]

    assert [valid[start:end] for start, end in find_protected_spans(valid)] == ["15:30"]
    assert all(find_protected_spans(text) == [] for text in invalid)


def test_protected_spans_use_configured_income_aliases(
    income_config: IncomeConfig,
) -> None:
    text = "на вул. Шевченка зп 500"

    spans = find_protected_spans(text, income_config)

    assert all(text[start:end] != "вул. Шевченка зп 500" for start, end in spans)
    assert parse_income_message(text, income_config, today=date(2026, 7, 29))[
        0
    ].amount == Decimal("500.00")


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
        "на вул. Шевченка зп 500",
        "на вул. Шевченка зарплата 500",
        "на вул. Шевченка повернули борг 500",
        "на вул. Шевченка подарували 500",
        "на вул. Шевченка переказали 500",
        "вул. отримав 500",
        "street earned 500",
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
    "text",
    [
        "street Baker salary 500",
        "street Baker card payment 500",
    ],
)
def test_configured_english_aliases_stop_street_protection(text: str) -> None:
    config = IncomeConfig(
        default_currency="GBP",
        categories={
            "salary": ["salary"],
            "other": [],
        },
        tags={"card": ["card payment"]},
    )

    parsed = parse_income_message(text, config)

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
        ("500\u00a0грн", Decimal("500.00"), "UAH"),
        ("500\nUSD", Decimal("500.00"), "USD"),
        ("₴\n500", Decimal("500.00"), "UAH"),
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
        ("Отримав 100 евро", "EUR"),
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


@pytest.mark.parametrize(
    "word",
    ["долина", "долати", "європа", "europe", "рівень"],
)
def test_unlisted_nearby_words_keep_default_currency(
    word: str,
    income_config: IncomeConfig,
) -> None:
    non_uah_config = income_config.model_copy(update={"default_currency": "GBP"})
    parsed = parse_income_message(f"100 {word}", non_uah_config)

    assert parsed[0].currency == "GBP"


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


def test_text_without_money_does_not_create_manual_draft(
    income_config: IncomeConfig,
) -> None:
    assert parse_income_message("Консультація для нового клієнта", income_config) == []


@pytest.mark.parametrize(
    "structured",
    [
        "123.456",
        "192.168.1.1",
        "2026-07-29",
        "25:00",
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "123456789012345678901234567890.12",
            Decimal("123456789012345678901234567890.12"),
        ),
        (
            "1 234 567 890 123 456 789 012 345 678",
            Decimal("1234567890123456789012345678.00"),
        ),
    ],
)
def test_large_decimal_precision_is_supported(
    text: str,
    expected: Decimal,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert [item.amount for item in parsed] == [expected]


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


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("99.99 грн", "UAH"),
        ("$99.99", "USD"),
        ("50.25 USD", "USD"),
        ("32.13 грн", "UAH"),
    ],
)
def test_explicit_currency_dot_decimal_wins_over_date_syntax(
    text: str,
    currency: str,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config)

    assert [item.amount for item in parsed] == [
        Decimal(text.replace("$", "").split()[0]).quantize(Decimal("0.01"))
    ]
    assert parsed[0].currency == currency


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("99.99 жвро", Decimal("99.99"), "EUR"),
        ("жвро 99.99", Decimal("99.99"), "EUR"),
        ("99.99\nжвро", Decimal("99.99"), "EUR"),
        ("50.25 доллар", Decimal("50.25"), "USD"),
        ("10.07 euroo", Decimal("10.07"), "EUR"),
    ],
)
def test_known_typo_dot_decimal_wins_and_is_removed_from_description(
    text: str,
    amount: Decimal,
    currency: str,
    income_config: IncomeConfig,
) -> None:
    non_uah_config = income_config.model_copy(update={"default_currency": "GBP"})

    parsed = parse_income_message(text, non_uah_config)

    assert [item.amount for item in parsed] == [amount]
    assert parsed[0].currency == currency
    assert parsed[0].description == ""


def test_bare_valid_date_remains_protected(income_config: IncomeConfig) -> None:
    assert parse_income_message("подія 10.07", income_config) == []


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("Отримав 12.05", Decimal("12.05")),
        ("Отримав 20.99", Decimal("20.99")),
        ("Заробив 1.50", Decimal("1.50")),
    ],
)
def test_lone_dot_amount_with_income_context_records_as_income(
    text: str,
    amount: Decimal,
    income_config: IncomeConfig,
) -> None:
    # A sole dotted amount matches the DD.MM date pattern; with income context
    # and no other number it is the amount, not a date — matching its comma form.
    parsed = parse_income_message(text, income_config, today=date(2026, 7, 24))

    assert [item.amount for item in parsed] == [amount]
    assert parsed[0].income_date == date(2026, 7, 24)


def test_dot_amount_alongside_real_amount_stays_a_date(
    income_config: IncomeConfig,
) -> None:
    # When a real income amount is present, a DD.MM token is a backdate, not a
    # second income.
    parsed = parse_income_message(
        "Отримав 800 грн 10.07", income_config, today=date(2026, 7, 24)
    )

    assert [item.amount for item in parsed] == [Decimal("800.00")]
    assert parsed[0].income_date == date(2026, 7, 10)


@pytest.mark.parametrize("text", ["23 квітня", "23 числа"])
def test_bare_ukrainian_text_date_is_not_income(
    text: str,
    income_config: IncomeConfig,
) -> None:
    assert parse_income_message(text, income_config, today=date(2026, 7, 24)) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Отримав 500 грн 23 квітня", date(2026, 4, 23)),
        ("Отримав 500 грн 23 числа", date(2026, 7, 23)),
    ],
)
def test_ukrainian_text_date_is_extracted_without_becoming_amount(
    text: str,
    expected: date,
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(text, income_config, today=date(2026, 7, 24))

    assert [item.amount for item in parsed] == [Decimal("500.00")]
    assert parsed[0].income_date == expected


def test_amount_23_with_currency_remains_income(income_config: IncomeConfig) -> None:
    parsed = parse_income_message("Отримав 23 грн", income_config)

    assert [item.amount for item in parsed] == [Decimal("23.00")]


def test_invalid_bare_date_preserves_validation_error(
    income_config: IncomeConfig,
) -> None:
    with pytest.raises(ValueError, match="Invalid income date: 32.13"):
        parse_income_message(
            "Отримав 500 грн 32.13",
            income_config,
            today=date(2026, 7, 24),
        )


def test_description_is_truncated_to_record_limit(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(f"{'x' * 1100} 500", income_config)

    assert len(parsed[0].description) == 1000
    assert parsed[0].description == "x" * 1000


def test_description_removes_money_and_temporal_tokens(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "о 9:30 Отримав 1500 грн за консультацію 10.07",
        income_config,
        today=date(2026, 7, 24),
    )

    assert [item.amount for item in parsed] == [Decimal("1500.00")]
    assert parsed[0].income_date == date(2026, 7, 10)
    assert parsed[0].description == "о Отримав за консультацію"
