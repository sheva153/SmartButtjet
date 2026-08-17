"""Tests for the arbitrary date-range parser used by /chart."""

from datetime import date

from income_stats.parsers.date_range import parse_date_range


def test_parses_two_dates() -> None:
    assert parse_date_range("01.03 15.03", today=date(2026, 8, 17)) == (
        date(2026, 3, 1),
        date(2026, 3, 15),
    )


def test_parses_full_year_dates() -> None:
    assert parse_date_range("01.03.2025 15.03.2025", today=date(2026, 8, 17)) == (
        date(2025, 3, 1),
        date(2025, 3, 15),
    )


def test_returns_none_for_garbage() -> None:
    assert parse_date_range("hello", today=date(2026, 8, 17)) is None


def test_orders_swapped_dates() -> None:
    assert parse_date_range("15.03 01.03", today=date(2026, 8, 17)) == (
        date(2026, 3, 1),
        date(2026, 3, 15),
    )


def test_returns_none_for_single_date() -> None:
    assert parse_date_range("01.03", today=date(2026, 8, 17)) is None


def test_returns_none_for_invalid_date() -> None:
    assert parse_date_range("31.02 01.03", today=date(2026, 8, 17)) is None
