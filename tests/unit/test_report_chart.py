import warnings
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from income_stats.config import load_config
from income_stats.models import IncomeRecord, Period, RecordType
from income_stats.services.report_chart import (
    PERIOD_TITLES,
    TAG_COLOR,
    TAG_LABELS,
    _period_buckets,
    _range_buckets,
    render_report_png,
)

_FX = {"USD": 41.0, "EUR": 45.0}


def _record(
    *,
    income_date: date,
    amount: str,
    currency: str = "UAH",
    tags: list[str] | None = None,
    kind: RecordType = "income",
) -> dict[str, object]:
    record = IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=1,
        updated_by=1,
        original_text="test",
        amount=Decimal(amount),
        currency=currency,
        tags=tags or [],
        type=kind,
        income_date=income_date,
    )
    return record.model_dump(mode="python")


def _mixed_frame() -> pd.DataFrame:
    """Week of 2026-08-17 (Monday): mixed currencies, single/multi tags, both kinds."""
    rows = [
        _record(
            income_date=date(2026, 8, 17),
            amount="20000",
            currency="UAH",
            tags=["salary", "card"],
            kind="income",
        ),
        _record(
            income_date=date(2026, 8, 17),
            amount="8000",
            currency="UAH",
            tags=["rent", "card"],
            kind="expense",
        ),
        _record(
            income_date=date(2026, 8, 18),
            amount="300",
            currency="USD",
            tags=["freelance", "card"],
            kind="income",
        ),
        _record(
            income_date=date(2026, 8, 19),
            amount="1600",
            currency="UAH",
            tags=["groceries", "cafe", "card"],
            kind="expense",
        ),
        _record(
            income_date=date(2026, 8, 20),
            amount="1000",
            currency="UAH",
            tags=[],
            kind="income",
        ),
    ]
    return pd.DataFrame(rows, columns=list(IncomeRecord.model_fields))


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(IncomeRecord.model_fields))


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


def test_preset_periods_have_titles() -> None:
    assert PERIOD_TITLES["last_week"] == "Звіт за минулий тиждень"
    assert PERIOD_TITLES["last_month"] == "Звіт за минулий місяць"
    assert PERIOD_TITLES["last_year"] == "Звіт за минулий рік"


def test_unsupported_period_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported report period"):
        _period_buckets(cast(Period, "all"), date(2026, 8, 14))


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


def test_to_uah_converts_by_rate() -> None:
    from income_stats.services.report_chart import to_uah

    assert to_uah(Decimal("100"), "USD", {"USD": 41.0}) == Decimal("4100")
    assert to_uah(Decimal("100"), "UAH", {"USD": 41.0}) == Decimal("100")
    assert to_uah(Decimal("100"), "GBP", {"USD": 41.0}) == Decimal("100")


def test_month_labels_include_weekday() -> None:
    from income_stats.services.report_chart import _month_labels

    labels = _month_labels(2026, 8)
    assert labels[0] == "1\nСб"  # 2026-08-01 is Saturday
    assert len(labels) == 31
    assert labels[-1] == "31\nПн"  # 2026-08-31 is Monday


def test_mix_cmap_multi_tag_has_flat_blocks() -> None:
    from income_stats.services.report_chart import _mix_cmap

    cm = _mix_cmap(("card", "groceries", "cafe"))
    # three distinct block colours sampled away from the seams
    assert cm(0.15) != cm(0.5) != cm(0.85)


def test_mix_cmap_single_tag_is_flat() -> None:
    from income_stats.services.report_chart import _mix_cmap

    cm = _mix_cmap(("card",))
    assert cm(0.0) == cm(0.5) == cm(1.0)


def test_label_returns_ukrainian_display_name() -> None:
    from income_stats.services.report_chart import _label

    assert _label("rent") == "Оренда"
    assert _label("card") == "Картка"


def test_label_falls_back_to_raw_tag() -> None:
    from income_stats.services.report_chart import _label

    assert _label("unknown-tag") == "unknown-tag"


def test_render_tag_mix_writes_png(tmp_path: Path) -> None:
    path = tmp_path / "c.png"
    render_report_png(_mixed_frame(), "week", date(2026, 8, 17), path, fx=_FX)
    assert path.exists() and path.stat().st_size > 0


@pytest.mark.parametrize(
    "period", ["week", "month", "year", "last_week", "last_month", "last_year"]
)
def test_render_writes_png_for_all_periods(period: str, tmp_path: Path) -> None:
    path = tmp_path / f"{period}.png"
    render_report_png(
        _mixed_frame(), cast(Period, period), date(2026, 8, 17), path, fx=_FX
    )
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_report_png_no_warning_when_period_has_no_matching_data(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        [_record(income_date=date(2026, 8, 3), amount="100")],
        columns=list(IncomeRecord.model_fields),
    )
    path = tmp_path / "empty.png"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        render_report_png(
            frame, cast(Period, "last_month"), date(2026, 8, 17), path, fx=_FX
        )
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_report_png_uses_range_title(tmp_path: Path) -> None:
    path = tmp_path / "range.png"
    render_report_png(
        _mixed_frame(),
        "week",
        date(2026, 8, 17),
        path,
        date_range=(date(2026, 8, 17), date(2026, 8, 18)),
        fx=_FX,
    )
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_draws_goal_and_forecast(tmp_path: Path) -> None:
    path = tmp_path / "c.png"
    render_report_png(
        _mixed_frame(),
        "month",
        date(2026, 8, 17),
        path,
        fx=_FX,
        goal=Decimal("50000"),
        forecast=Decimal("42000"),
    )
    assert path.exists() and path.stat().st_size > 0


def test_render_report_png_no_warning_with_goal_and_forecast(tmp_path: Path) -> None:
    path = tmp_path / "c.png"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        render_report_png(
            _mixed_frame(),
            "month",
            date(2026, 8, 17),
            path,
            fx=_FX,
            goal=Decimal("50000"),
            forecast=Decimal("42000"),
        )
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_handles_empty_frame_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        render_report_png(_empty_frame(), "month", date(2026, 8, 17), path, fx=_FX)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_render_groups_legend_by_tag_mix_and_sums_uah_equivalent(
    tmp_path: Path,
) -> None:
    """Two records sharing a tag-mix in different slots collapse into one combo sum."""
    from income_stats.services.report_chart import _colours, to_uah

    frame = pd.DataFrame(
        [
            _record(
                income_date=date(2026, 8, 17),
                amount="1200",
                currency="UAH",
                tags=["groceries", "cash"],
                kind="expense",
            ),
            _record(
                income_date=date(2026, 8, 20),
                amount="700",
                currency="UAH",
                tags=["groceries", "cash"],
                kind="expense",
            ),
        ],
        columns=list(IncomeRecord.model_fields),
    )
    path = tmp_path / "combo.png"
    render_report_png(frame, "week", date(2026, 8, 17), path, fx=_FX)
    assert path.exists() and path.stat().st_size > 0
    total = to_uah(Decimal("1200"), "UAH", _FX) + to_uah(Decimal("700"), "UAH", _FX)
    assert total == Decimal("1900")
    assert _colours(["groceries", "cash"]) != []


def test_render_untagged_record_uses_no_tag_sentinel(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            _record(
                income_date=date(2026, 8, 17), amount="1000", currency="UAH", tags=[]
            )
        ],
        columns=list(IncomeRecord.model_fields),
    )
    path = tmp_path / "untagged.png"
    render_report_png(frame, "week", date(2026, 8, 17), path, fx=_FX)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_no_tag_colour_depends_on_kind() -> None:
    from income_stats.services.report_chart import (
        NO_TAG_EXPENSE,
        NO_TAG_INCOME,
        _colours,
    )

    assert _colours([NO_TAG_INCOME]) == ["#1f6feb"]
    assert _colours([NO_TAG_EXPENSE]) == ["#f85149"]


def test_no_tag_income_and_expense_labels_are_ukrainian() -> None:
    from income_stats.services.report_chart import NO_TAG_EXPENSE, NO_TAG_INCOME, _label

    assert _label(NO_TAG_INCOME) == "Без тегу"
    assert _label(NO_TAG_EXPENSE) == "Без тегу"


def test_tag_colours_never_use_the_reserved_no_tag_hues() -> None:
    # Blue and red are reserved for "no tag" income/expense defaults; a real
    # tag colliding with either would be visually confused with that default.
    reserved = {"#1f6feb", "#f85149"}
    assert not reserved & set(TAG_COLOR.values())


def test_untagged_income_and_expense_render_with_distinct_colours(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        [
            _record(
                income_date=date(2026, 8, 17),
                amount="1000",
                currency="UAH",
                tags=[],
                kind="income",
            ),
            _record(
                income_date=date(2026, 8, 17),
                amount="500",
                currency="UAH",
                tags=[],
                kind="expense",
            ),
        ],
        columns=list(IncomeRecord.model_fields),
    )
    path = tmp_path / "untagged-both.png"
    render_report_png(frame, "week", date(2026, 8, 17), path, fx=_FX)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_value_label_centers_inside_a_tall_bar() -> None:
    from income_stats.services.report_chart import _value_label

    position, alignment = _value_label(0.0, 1000.0, span=2000.0)

    assert alignment == "center"
    assert position == 500.0


def test_value_label_moves_outside_a_short_income_bar() -> None:
    from income_stats.services.report_chart import _value_label

    position, alignment = _value_label(0.0, 10.0, span=2000.0)

    assert alignment == "bottom"
    assert position > 10.0


def test_value_label_moves_outside_a_short_expense_bar() -> None:
    from income_stats.services.report_chart import _value_label

    position, alignment = _value_label(0.0, -10.0, span=2000.0)

    assert alignment == "top"
    assert position < -10.0


def test_value_label_handles_zero_span_without_crashing() -> None:
    from income_stats.services.report_chart import _value_label

    position, alignment = _value_label(0.0, 100.0, span=0.0)

    assert alignment == "center"
    assert position == 50.0


def test_combo_totals_by_kind_splits_income_and_expense() -> None:
    from income_stats.services.report_chart import _combo_totals_by_kind

    combo_total = {
        ("income", ("card",)): 500.0,
        ("expense", ("rent",)): 300.0,
        ("income", ("cash",)): 1200.0,
    }

    income = _combo_totals_by_kind(combo_total, "income")
    expense = _combo_totals_by_kind(combo_total, "expense")

    assert income == [(("cash",), 1200.0), (("card",), 500.0)]
    assert expense == [(("rent",), 300.0)]


def test_combo_totals_by_kind_returns_empty_for_missing_kind() -> None:
    from income_stats.services.report_chart import _combo_totals_by_kind

    assert _combo_totals_by_kind({("income", ("card",)): 500.0}, "expense") == []


def test_render_still_works_with_only_one_kind_present(tmp_path: Path) -> None:
    """No expense records at all: the expense mix legend must not error out."""
    frame = pd.DataFrame(
        [
            _record(
                income_date=date(2026, 8, 17),
                amount="500",
                currency="UAH",
                tags=["card"],
                kind="income",
            ),
        ],
        columns=list(IncomeRecord.model_fields),
    )
    path = tmp_path / "income-only.png"
    render_report_png(frame, "week", date(2026, 8, 17), path, fx=_FX)
    assert path.read_bytes().startswith(b"\x89PNG")


def test_tag_maps_cover_config_tags() -> None:
    # The chart colours/labels tags by their `income.tags` key. If the shipped
    # config gains a tag the maps don't know, it renders grey with a raw
    # English key (a Ukrainian-label regression); if the maps carry a key the
    # config never emits as a tag, it is dead. Lock the two together.
    config_tags = set(load_config().income.tags)
    assert set(TAG_LABELS) == config_tags
    assert set(TAG_COLOR) == config_tags
    # Every label is Ukrainian (not the raw English key) and every colour hex.
    for tag in config_tags:
        assert TAG_LABELS[tag] != tag
        assert TAG_COLOR[tag].startswith("#")
