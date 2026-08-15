from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from income_stats.models import Period
from income_stats.services.report_chart import (
    _aggregate,
    _period_buckets,
    render_report_png,
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
        }
    )
    series = _aggregate(frame, labels, index_of)
    assert series["UAH"][0] == 150.0  # Monday sum
    assert series["USD"][2] == 20.0  # Wednesday


@pytest.mark.parametrize("period", ["week", "month", "year"])
def test_render_writes_png(period: str, tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "income_date": [date(2026, 8, 3), date(2026, 8, 14)],
            "currency": ["UAH", "USD"],
            "amount": [Decimal("1500"), Decimal("250")],
        }
    )
    path = tmp_path / f"{period}.png"
    render_report_png(frame, cast(Period, period), date(2026, 8, 14), path)
    assert path.read_bytes().startswith(b"\x89PNG")
