"""Browser-free income report chart rendered to PNG with matplotlib.

Plotly's PNG export needs a native Chrome/kaleido build that isn't available on
this ARM server, so the photo report is drawn with matplotlib's Agg backend.
The object-oriented API (``Figure`` + ``FigureCanvasAgg``) is used instead of
``pyplot`` so rendering stays thread-safe inside the analytics worker thread.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from income_stats.models import Period

_WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
_MONTH_LABELS = (
    "Січ", "Лют", "Бер", "Кві", "Тра", "Чер",
    "Лип", "Сер", "Вер", "Жов", "Лис", "Гру",
)  # fmt: skip
PERIOD_TITLES = {
    "week": "Звіт за тиждень",
    "month": "Звіт за місяць",
    "year": "Звіт за рік",
    "last_week": "Звіт за минулий тиждень",
    "last_month": "Звіт за минулий місяць",
    "last_year": "Звіт за минулий рік",
}


def _period_buckets(
    period: Period,
    reference: date,
) -> tuple[list[str], Callable[[date], int | None]]:
    """Return the x-axis labels and a date→bucket-index mapper for a period."""
    if period == "last_week":
        return _period_buckets("week", reference - timedelta(days=7))
    if period == "last_month":
        first_this = reference.replace(day=1)
        return _period_buckets("month", first_this - timedelta(days=1))
    if period == "last_year":
        return _period_buckets("year", date(reference.year - 1, 1, 1))

    if period == "week":
        start = reference - timedelta(days=reference.weekday())

        def week_index(value: date) -> int | None:
            delta = (value - start).days
            return delta if 0 <= delta < 7 else None

        return list(_WEEKDAY_LABELS), week_index

    if period == "month":
        days = calendar.monthrange(reference.year, reference.month)[1]

        def month_index(value: date) -> int | None:
            if (value.year, value.month) != (reference.year, reference.month):
                return None
            return value.day - 1

        return [str(day) for day in range(1, days + 1)], month_index

    if period == "year":

        def year_index(value: date) -> int | None:
            return value.month - 1 if value.year == reference.year else None

        return list(_MONTH_LABELS), year_index

    raise ValueError(f"Unsupported report period: {period}")


def _range_buckets(
    start: date, end: date
) -> tuple[list[str], Callable[[date], int | None]]:
    """Return x-axis labels and a date→bucket-index mapper for an arbitrary range.

    Buckets by day when the span is at most 62 days, otherwise by month.
    """
    span = (end - start).days
    if span <= 62:
        labels = [
            (start + timedelta(days=offset)).strftime("%d.%m")
            for offset in range(span + 1)
        ]

        def day_index(value: date) -> int | None:
            delta = (value - start).days
            return delta if 0 <= delta <= span else None

        return labels, day_index

    months: list[date] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    labels = [month.strftime("%m.%Y") for month in months]
    lookup = {(month.year, month.month): index for index, month in enumerate(months)}

    def month_index(value: date) -> int | None:
        return lookup.get((value.year, value.month))

    return labels, month_index


def _aggregate(
    frame: pd.DataFrame,
    labels: list[str],
    index_of: Callable[[date], int | None],
) -> dict[str, dict[str, list[float]]]:
    series: dict[str, dict[str, list[float]]] = {}
    for income_date, currency, amount, kind in zip(
        frame["income_date"],
        frame["currency"],
        frame["amount"],
        frame["type"],
        strict=True,
    ):
        bucket = index_of(income_date)
        if bucket is None:
            continue
        by_kind = series.setdefault(
            str(currency),
            {"income": [0.0] * len(labels), "expense": [0.0] * len(labels)},
        )
        by_kind[str(kind)][bucket] += float(amount)
    return series


def _format_amount(value: float) -> str:
    text = f"{int(round(value)):,}" if value == int(value) else f"{value:,.2f}"
    return text.replace(",", " ")


def _legend_label(currency: str, *, income: float, expense: float) -> str:
    return f"{currency}: +{_format_amount(income)} / −{_format_amount(expense)}"


_TAG_LABELS = {
    "card": "Картка",
    "cash": "Готівка",
    "rent": "Оренда",
    "utilities": "Комуналка",
    "dentistry": "Стоматологія",
    "health": "Медицина",
    "groceries": "Продукти",
    "transport": "Транспорт",
    "cafe": "Кафе",
    "subscriptions": "Підписки",
    "education": "Освіта",
}


def _tag_label(tag: str) -> str:
    """Return the plain Ukrainian display name for a tag (no emoji glyphs)."""
    return _TAG_LABELS.get(tag, tag)


def render_report_png(
    frame: pd.DataFrame,
    period: Period,
    reference: date,
    path: Path,
    date_range: tuple[date, date] | None = None,
    goal: Decimal | None = None,
    forecast: Decimal | None = None,
    tag_totals: dict[str, float] | None = None,
) -> None:
    """Draw a period (or arbitrary date-range) report bar chart to ``path``."""
    if date_range is not None:
        labels, index_of = _range_buckets(*date_range)
        title = f"Звіт {date_range[0]:%d.%m.%Y}–{date_range[1]:%d.%m.%Y}"
    else:
        labels, index_of = _period_buckets(period, reference)
        title = PERIOD_TITLES[period]
    series = _aggregate(frame, labels, index_of)
    currencies = sorted(series)

    positions = range(len(labels))
    has_tags = bool(tag_totals)
    figure = Figure(
        figsize=(max(8.0, len(labels) * 0.5), 6.5 if has_tags else 5.0), dpi=150
    )
    FigureCanvasAgg(figure)
    if has_tags:
        axes, tag_axes = figure.subplots(2, 1, height_ratios=[3, 1])
    else:
        axes = figure.subplots()
        tag_axes = None

    group_width = 0.8
    bar_width = group_width / max(1, len(currencies))
    for order, currency in enumerate(currencies):
        income = series[currency]["income"]
        expense = series[currency]["expense"]
        offsets = [
            position - group_width / 2 + bar_width * (order + 0.5)
            for position in positions
        ]
        income_bars = axes.bar(
            offsets,
            income,
            width=bar_width,
            label=_legend_label(currency, income=sum(income), expense=sum(expense)),
        )
        color = income_bars[0].get_facecolor()
        axes.bar(
            offsets,
            [-value for value in expense],
            width=bar_width,
            color=color,
            alpha=0.55,
            hatch="//",
        )
        for offset, up, down in zip(offsets, income, expense, strict=True):
            if up > 0:
                axes.annotate(
                    _format_amount(up),
                    (offset, up),
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    rotation=90 if len(labels) > 12 else 0,
                )
            if down > 0:
                axes.annotate(
                    _format_amount(down),
                    (offset, -down),
                    ha="center",
                    va="top",
                    fontsize=8,
                    rotation=90 if len(labels) > 12 else 0,
                )

    axes.axhline(0, color="black", linewidth=0.8)
    if goal is not None:
        axes.axhline(float(goal), color="#d29922", linewidth=1.4, linestyle="-")
        axes.annotate(
            f"Ціль {_format_amount(float(goal))}",
            (len(labels) - 1, float(goal)),
            ha="right",
            va="bottom",
            fontsize=8,
            color="#d29922",
        )
    if forecast is not None:
        axes.axhline(float(forecast), color="#3fb950", linewidth=1.2, linestyle="--")
        axes.annotate(
            f"Прогноз ~{_format_amount(float(forecast))}",
            (0, float(forecast)),
            ha="left",
            va="bottom",
            fontsize=8,
            color="#3fb950",
        )
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylabel("Сума")
    axes.margins(y=0.18)
    axes.grid(axis="y", linestyle=":", alpha=0.4)

    def _subtitle_part(currency: str) -> str:
        income_total = sum(series[currency]["income"])
        expense_total = sum(series[currency]["expense"])
        net_total = income_total - expense_total
        return (
            f"{currency} Дохід {_format_amount(income_total)}"
            f" · Витрати {_format_amount(expense_total)}"
            f" · Чистими {_format_amount(net_total)}"
        )

    subtitle = " · ".join(_subtitle_part(currency) for currency in currencies)
    axes.set_title(f"{title}\n{subtitle or '—'}")
    if currencies:
        axes.legend(title="Валюта")

    if tag_axes is not None:
        assert tag_totals is not None  # tag_axes is only set when tag_totals is set
        tags = sorted(tag_totals, key=lambda tag: tag_totals[tag], reverse=True)
        values = [tag_totals[tag] for tag in tags]
        positions_tags = range(len(tags))
        cmap = colormaps["tab20"]
        colors = [cmap(index % cmap.N) for index in positions_tags]
        tag_axes.barh(list(positions_tags), values, color=colors)
        tag_axes.set_yticks(list(positions_tags))
        tag_axes.set_yticklabels([_tag_label(tag) for tag in tags])
        tag_axes.invert_yaxis()
        tag_axes.set_xlabel("Сума за тегами")
        tag_axes.grid(axis="x", linestyle=":", alpha=0.4)
        for position, value in zip(positions_tags, values, strict=True):
            tag_axes.annotate(
                _format_amount(value),
                (value, position),
                ha="left",
                va="center",
                fontsize=8,
                xytext=(4, 0),
                textcoords="offset points",
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path)
