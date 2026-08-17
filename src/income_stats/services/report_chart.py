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
from pathlib import Path

import pandas as pd
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
}


def _period_buckets(
    period: Period,
    reference: date,
) -> tuple[list[str], Callable[[date], int | None]]:
    """Return the x-axis labels and a date→bucket-index mapper for a period."""
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


def _aggregate(
    frame: pd.DataFrame,
    labels: list[str],
    index_of: Callable[[date], int | None],
) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {}
    for income_date, currency, amount in zip(
        frame["income_date"], frame["currency"], frame["amount"], strict=True
    ):
        bucket = index_of(income_date)
        if bucket is None:
            continue
        totals = series.setdefault(str(currency), [0.0] * len(labels))
        totals[bucket] += float(amount)
    return series


def _format_amount(value: float) -> str:
    text = f"{int(round(value)):,}" if value == int(value) else f"{value:,.2f}"
    return text.replace(",", " ")


def render_report_png(
    frame: pd.DataFrame,
    period: Period,
    reference: date,
    path: Path,
) -> None:
    """Draw a period report bar chart with value and time labels to ``path``."""
    labels, index_of = _period_buckets(period, reference)
    series = _aggregate(frame, labels, index_of)
    currencies = sorted(series)

    positions = range(len(labels))
    figure = Figure(figsize=(max(8.0, len(labels) * 0.5), 5.0), dpi=150)
    FigureCanvasAgg(figure)
    axes = figure.subplots()

    group_width = 0.8
    bar_width = group_width / max(1, len(currencies))
    for order, currency in enumerate(currencies):
        values = series[currency]
        offsets = [
            position - group_width / 2 + bar_width * (order + 0.5)
            for position in positions
        ]
        bars = axes.bar(offsets, values, width=bar_width, label=currency)
        for rectangle, value in zip(bars, values, strict=True):
            if value <= 0:
                continue
            axes.annotate(
                _format_amount(value),
                (rectangle.get_x() + rectangle.get_width() / 2, value),
                ha="center",
                va="bottom",
                fontsize=8,
                rotation=90 if len(labels) > 12 else 0,
            )

    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylabel("Сума")
    axes.margins(y=0.18)
    axes.grid(axis="y", linestyle=":", alpha=0.4)
    totals = " · ".join(
        f"{_format_amount(sum(series[currency]))} {currency}" for currency in currencies
    )
    axes.set_title(f"{PERIOD_TITLES[period]}\nРазом: {totals or '—'}")
    if len(currencies) > 1:
        axes.legend(title="Валюта")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
