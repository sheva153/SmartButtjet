from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from income_stats.models import Period
from income_stats.services.report_chart import (
    _aggregate,
    _legend_label,
    _period_buckets,
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


@pytest.mark.parametrize("period", ["week", "month", "year"])
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
