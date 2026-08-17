import warnings
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from income_stats.models import Period
from income_stats.services.report_chart import (
    PERIOD_TITLES,
    _aggregate,
    _legend_label,
    _period_buckets,
    _range_buckets,
    render_report_png,
)


def _week_frame_mixed() -> pd.DataFrame:
    """Monday income 2000 UAH, Tuesday expense 500 UAH for the week of 2026-08-17."""
    return pd.DataFrame(
        {
            "income_date": [date(2026, 8, 17), date(2026, 8, 18)],
            "currency": ["UAH", "UAH"],
            "amount": [Decimal("2000"), Decimal("500")],
            "type": ["income", "expense"],
        }
    )


def test_week_buckets_are_weekdays() -> None:
    labels, index_of = _period_buckets("week", date(2026, 8, 14))  # Friday
    assert labels == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    assert index_of(date(2026, 8, 10)) == 0  # Monday of that week
    assert index_of(date(2026, 8, 14)) == 4  # Friday
    assert index_of(date(2026, 8, 17)) is None  # next week


def test_month_buckets_cover_every_day() -> None:
    labels, index_of = _period_buckets("month", date(2026, 2, 10))  # 28 days
    assert labels[0] == "1"
    assert labels[-1] == "28"
    assert index_of(date(2026, 2, 1)) == 0
    assert index_of(date(2026, 3, 1)) is None


def test_year_buckets_are_twelve_months() -> None:
    labels, index_of = _period_buckets("year", date(2026, 8, 14))
    assert len(labels) == 12
    assert index_of(date(2026, 1, 5)) == 0
    assert index_of(date(2026, 12, 31)) == 11
    assert index_of(date(2025, 12, 31)) is None


def test_last_week_buckets_shift_reference_back_a_week() -> None:
    labels, index_of = _period_buckets("last_week", date(2026, 8, 17))  # Monday
    assert labels == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    assert index_of(date(2026, 8, 10)) == 0  # Monday of last week
    assert index_of(date(2026, 8, 16)) == 6  # Sunday of last week
    assert index_of(date(2026, 8, 17)) is None  # this week


def test_last_month_buckets_resolve_previous_month() -> None:
    labels, index_of = _period_buckets("last_month", date(2026, 8, 17))
    assert len(labels) == 31  # July has 31 days
    assert index_of(date(2026, 7, 1)) == 0
    assert index_of(date(2026, 8, 1)) is None


def test_last_year_buckets_resolve_previous_year() -> None:
    labels, index_of = _period_buckets("last_year", date(2026, 8, 17))
    assert len(labels) == 12
    assert index_of(date(2025, 1, 5)) == 0
    assert index_of(date(2026, 1, 5)) is None


def test_last_year_buckets_survive_leap_day_reference() -> None:
    # 2028 is a leap year but 2027 is not; naive year-1 replace() would raise
    # ValueError: day is out of range for month.
    labels, index_of = _period_buckets("last_year", date(2028, 2, 29))
    assert len(labels) == 12
    assert index_of(date(2027, 1, 5)) == 0
    assert index_of(date(2028, 1, 5)) is None


def test_render_writes_png_for_leap_day_last_year_reference(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "income_date": [date(2027, 3, 1)],
            "currency": ["UAH"],
            "amount": [Decimal("100")],
            "type": ["income"],
        }
    )
    path = tmp_path / "leap.png"
    render_report_png(frame, cast(Period, "last_year"), date(2028, 2, 29), path)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_report_png_no_warning_when_period_has_no_matching_data(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        {
            "income_date": [date(2026, 8, 3)],
            "currency": ["UAH"],
            "amount": [Decimal("100")],
            "type": ["income"],
        }
    )
    path = tmp_path / "empty.png"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        render_report_png(frame, cast(Period, "last_month"), date(2026, 8, 17), path)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_preset_periods_have_titles() -> None:
    assert PERIOD_TITLES["last_week"] == "Звіт за минулий тиждень"
    assert PERIOD_TITLES["last_month"] == "Звіт за минулий місяць"
    assert PERIOD_TITLES["last_year"] == "Звіт за минулий рік"


def test_unsupported_period_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported report period"):
        _period_buckets(cast(Period, "all"), date(2026, 8, 14))


def test_aggregate_sums_per_bucket_and_currency() -> None:
    labels, index_of = _period_buckets("week", date(2026, 8, 14))
    frame = pd.DataFrame(
        {
            "income_date": [date(2026, 8, 10), date(2026, 8, 10), date(2026, 8, 12)],
            "currency": ["UAH", "UAH", "USD"],
            "amount": [Decimal("100"), Decimal("50"), Decimal("20")],
            "type": ["income", "income", "expense"],
        }
    )
    series = _aggregate(frame, labels, index_of)
    assert series["UAH"]["income"][0] == 150.0  # Monday sum
    assert series["USD"]["expense"][2] == 20.0  # Wednesday


def test_aggregate_splits_income_and_expense() -> None:
    labels, index_of = _period_buckets("week", date(2026, 8, 17))
    frame = _week_frame_mixed()
    series = _aggregate(frame, labels, index_of)
    assert series["UAH"]["income"][0] == 2000.0
    assert series["UAH"]["expense"][1] == 500.0


def test_aggregate_initializes_both_kinds_for_every_currency() -> None:
    labels, index_of = _period_buckets("week", date(2026, 8, 17))
    frame = pd.DataFrame(
        {
            "income_date": [date(2026, 8, 17)],
            "currency": ["USD"],
            "amount": [Decimal("10")],
            "type": ["income"],
        }
    )
    series = _aggregate(frame, labels, index_of)
    assert series["USD"]["expense"] == [0.0] * len(labels)


def test_legend_label_includes_totals() -> None:
    assert (
        _legend_label("UAH", income=12000.0, expense=3000.0) == "UAH: +12 000 / −3 000"
    )


@pytest.mark.parametrize(
    "period", ["week", "month", "year", "last_week", "last_month", "last_year"]
)
def test_render_writes_png(period: str, tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "income_date": [date(2026, 8, 3), date(2026, 8, 14)],
            "currency": ["UAH", "USD"],
            "amount": [Decimal("1500"), Decimal("250")],
            "type": ["income", "expense"],
        }
    )
    path = tmp_path / f"{period}.png"
    render_report_png(frame, cast(Period, period), date(2026, 8, 14), path)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_report_png_writes_file(tmp_path: Path) -> None:
    path = tmp_path / "chart.png"
    render_report_png(_week_frame_mixed(), "week", date(2026, 8, 17), path)
    assert path.exists() and path.stat().st_size > 0


def test_range_buckets_by_day_for_short_span() -> None:
    labels, index_of = _range_buckets(date(2026, 3, 1), date(2026, 3, 5))
    assert labels == ["01.03", "02.03", "03.03", "04.03", "05.03"]
    assert index_of(date(2026, 3, 3)) == 2
    assert index_of(date(2026, 2, 28)) is None
    assert index_of(date(2026, 3, 6)) is None


def test_range_buckets_by_month_for_long_span() -> None:
    labels, index_of = _range_buckets(date(2025, 1, 15), date(2025, 6, 3))
    assert labels == ["01.2025", "02.2025", "03.2025", "04.2025", "05.2025", "06.2025"]
    assert index_of(date(2025, 3, 20)) == 2
    assert index_of(date(2024, 12, 31)) is None


def test_render_report_png_uses_range_title(tmp_path: Path) -> None:
    path = tmp_path / "range.png"
    render_report_png(
        _week_frame_mixed(),
        "week",
        date(2026, 8, 17),
        path,
        date_range=(date(2026, 8, 17), date(2026, 8, 18)),
    )
    assert path.read_bytes().startswith(b"\x89PNG")
