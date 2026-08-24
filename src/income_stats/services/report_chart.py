"""Browser-free income report chart rendered to PNG with matplotlib.

Plotly's PNG export needs a native Chrome/kaleido build that isn't available on
this ARM server, so the photo report is drawn with matplotlib's Agg backend.
The object-oriented API (``Figure`` + ``FigureCanvasAgg``) is used instead of
``pyplot`` so rendering stays thread-safe inside the analytics worker thread.

Design: colour = tag (a short flat-block gradient per record so a 2-3 tag mix
stays readable), hatch = currency, height = UAH-equivalent (income up /
expense down, stacked per bucket). Ported from the validated reference demo
at ``docs/superpowers/reference/2026-08-17-tag-mix-chart-demo.py``.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.legend_handler import HandlerBase
from matplotlib.patches import Patch, Rectangle

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

CURRENCY_HATCH = {"UAH": "", "USD": "///", "EUR": "..."}
# Keys mirror `income.tags` in config.yaml exactly — these are the tags the
# parser can attach to a record, so every one needs a Ukrainian label and a
# distinct colour (an unlisted tag falls back to NO_TAG grey + its raw key).
# tests/unit/test_report_chart.py::test_tag_maps_cover_config_tags locks this
# in step with the shipped config so the two cannot drift apart again.
TAG_LABELS = {
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
    "education": "Навчання",
}
TAG_COLOR = {
    "card": "#1f6feb",
    "cash": "#2ea043",
    "rent": "#f85149",
    "utilities": "#d29922",
    "dentistry": "#e3b341",
    "health": "#f778ba",
    "groceries": "#db6d28",
    "transport": "#39c5cf",
    "cafe": "#a371f7",
    "subscriptions": "#bc8cff",
    "education": "#58a6ff",
}
NO_TAG = ("_none", "Без тегу", "#8b949e")
_ORDER = {tag: index for index, tag in enumerate([*TAG_LABELS, NO_TAG[0]])}


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


def _month_labels(year: int, month: int) -> list[str]:
    """Return "{day}\\n{weekday}" x-axis labels for every day of a month."""
    days = calendar.monthrange(year, month)[1]
    return [
        f"{day}\n{_WEEKDAY_LABELS[date(year, month, day).weekday()]}"
        for day in range(1, days + 1)
    ]


def _resolved_month(period: Period, reference: date) -> tuple[int, int]:
    """Return the (year, month) a "month"/"last_month" period's x-axis covers."""
    if period == "last_month":
        first_this = reference.replace(day=1)
        prior = first_this - timedelta(days=1)
        return prior.year, prior.month
    return reference.year, reference.month


def to_uah(amount: Decimal, currency: str, fx: dict[str, float]) -> Decimal:
    return amount * Decimal(str(fx.get(currency, 1)))


def _format_amount(value: float) -> str:
    text = f"{int(round(value)):,}" if value == int(value) else f"{value:,.2f}"
    return text.replace(",", " ")


def _label(tag: str) -> str:
    """Return the plain Ukrainian display name for a tag (no emoji glyphs)."""
    return TAG_LABELS.get(tag, NO_TAG[1] if tag == NO_TAG[0] else tag)


def _colours(tags: list[str]) -> list[str]:
    return [TAG_COLOR.get(tag, NO_TAG[2]) for tag in tags]


def _mix_cmap(tags: list[str]) -> LinearSegmentedColormap:
    """Build a gradient with flat colour blocks (short blends at the seams)."""
    colours = _colours(tags)
    count = len(colours)
    if count == 1:
        return LinearSegmentedColormap.from_list("mix", [colours[0], colours[0]])
    segment = 1.0 / count
    blend = segment * 0.14
    stops: list[tuple[float, str]] = []
    for index, colour in enumerate(colours):
        start = index * segment + (blend if index else 0.0)
        end = (index + 1) * segment - (blend if index < count - 1 else 0.0)
        stops.append((round(start, 4), colour))
        stops.append((round(end, 4), colour))
    return LinearSegmentedColormap.from_list("mix", stops)


class MixHandler(HandlerBase):
    """Legend key that shows a tag-mix as side-by-side colour blocks."""

    def __init__(self, colours: list[str]) -> None:
        super().__init__()
        self._colours = colours

    def create_artists(self, legend, orig, xd, yd, width, height, fontsize, trans):  # noqa: ANN001, ANN201
        count = len(self._colours)
        return [
            Rectangle(
                (xd + width * index / count, yd),
                width / count,
                height,
                facecolor=colour,
                edgecolor="none",
                transform=trans,
            )
            for index, colour in enumerate(self._colours)
        ]


def render_report_png(
    frame: pd.DataFrame,
    period: Period,
    reference: date,
    path: Path,
    *,
    date_range: tuple[date, date] | None = None,
    fx: dict[str, float],
    goal: Decimal | None = None,
    forecast: Decimal | None = None,
) -> None:
    """Draw the tag-mix report chart (colour=tag, hatch=currency) to ``path``."""
    if date_range is not None:
        labels, index_of = _range_buckets(*date_range)
        title = f"Звіт {date_range[0]:%d.%m.%Y}–{date_range[1]:%d.%m.%Y}"
    else:
        labels, index_of = _period_buckets(period, reference)
        title = PERIOD_TITLES[period]
        if period in ("month", "last_month"):
            year, month = _resolved_month(period, reference)
            labels = _month_labels(year, month)

    tick_fontsize = 9 if len(labels) <= 12 else 7
    figure = Figure(figsize=(max(8.0, len(labels) * 0.5), 6.6), dpi=150)
    FigureCanvasAgg(figure)
    axes = figure.subplots()

    bar_width = 0.64
    gradient = np.linspace(0, 1, 256).reshape(1, -1)
    bases: dict[tuple[int, str], float] = {}
    combo_total: dict[tuple[str, ...], float] = {}
    currency_total: dict[str, float] = {}
    income_total = expense_total = 0.0
    lo = hi = 0.0

    for income_date, currency, amount, kind, tags in zip(
        frame["income_date"],
        frame["currency"],
        frame["amount"],
        frame["type"],
        frame["tags"],
        strict=True,
    ):
        slot = index_of(income_date)
        if slot is None:
            continue
        uah = float(to_uah(amount, currency, fx))
        height = uah if kind == "income" else -uah
        record_tags = list(tags) or [NO_TAG[0]]
        base = bases.get((slot, kind), 0.0)
        y0, y1 = base, base + height
        x0, x1 = slot - bar_width / 2, slot + bar_width / 2
        y_low, y_high = min(y0, y1), max(y0, y1)
        axes.imshow(
            gradient,
            extent=(x0, x1, y_low, y_high),
            aspect="auto",
            origin="lower",
            cmap=_mix_cmap(record_tags),
            vmin=0,
            vmax=1,
            zorder=2,
        )
        axes.add_patch(
            Rectangle(
                (x0, y_low),
                x1 - x0,
                y_high - y_low,
                fill=False,
                hatch=CURRENCY_HATCH.get(currency, "") or None,
                edgecolor="#0d1117",
                linewidth=0.7,
                zorder=3,
            )
        )
        bases[(slot, kind)] = y1
        lo, hi = min(lo, y_low), max(hi, y_high)
        combo = tuple(sorted(record_tags, key=lambda tag: _ORDER.get(tag, 99)))
        combo_total[combo] = combo_total.get(combo, 0.0) + uah
        currency_total[currency] = currency_total.get(currency, 0.0) + uah
        if kind == "income":
            income_total += uah
        else:
            expense_total += uah

    goal_value = float(goal) if goal is not None else None
    forecast_value = float(forecast) if forecast is not None else None
    for line_value in (goal_value, forecast_value):
        if line_value is not None:
            lo, hi = min(lo, line_value), max(hi, line_value)

    axes.axhline(0, color="#c9d1d9", linewidth=1.0, zorder=4)
    axes.set_xlim(-0.7, len(labels) - 0.3)
    pad = (hi - lo) * 0.08 or 1.0
    axes.set_ylim(lo - pad, hi + pad)
    axes.set_xticks(range(len(labels)))
    axes.set_xticklabels(labels, fontsize=tick_fontsize)
    axes.set_ylabel("Сума, ₴-еквівалент")
    axes.set_title(title, fontsize=11)
    axes.grid(axis="y", linestyle=":", alpha=0.3, zorder=0)

    if goal_value is not None:
        axes.axhline(goal_value, color="#d29922", linewidth=1.4, zorder=4)
        axes.annotate(
            f"Ціль {_format_amount(goal_value)}",
            (len(labels) - 1, goal_value),
            ha="right",
            va="bottom",
            fontsize=8,
            color="#d29922",
        )
    if forecast_value is not None:
        axes.axhline(
            forecast_value, color="#3fb950", linewidth=1.2, linestyle="--", zorder=4
        )
        axes.annotate(
            f"Прогноз ~{_format_amount(forecast_value)}",
            (0, forecast_value),
            ha="left",
            va="bottom",
            fontsize=8,
            color="#3fb950",
        )

    net_total = income_total - expense_total
    legends = []

    together = axes.legend(
        handles=[
            Patch(
                facecolor="#3fb950", label=f"Дохід — {_format_amount(income_total)} ₴"
            ),
            Patch(
                facecolor="#f85149",
                label=f"Витрати — {_format_amount(expense_total)} ₴",
            ),
            Patch(
                facecolor="#58a6ff", label=f"Чистими — {_format_amount(net_total)} ₴"
            ),
        ],
        title="Разом (₴-екв)",
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=8,
        title_fontsize=9,
        borderaxespad=0.0,
    )
    axes.add_artist(together)
    legends.append(together)

    combos = sorted(combo_total, key=lambda combo: combo_total[combo], reverse=True)
    proxies: list[Patch] = []
    handler_map: dict[Patch, MixHandler] = {}
    for combo in combos:
        proxy = Patch(
            label=(
                f"{' + '.join(_label(tag) for tag in combo)}"
                f" — {_format_amount(combo_total[combo])} ₴"
            )
        )
        proxies.append(proxy)
        handler_map[proxy] = MixHandler(_colours(list(combo)))
    mixes = axes.legend(
        handles=proxies,
        handler_map=handler_map,
        title="Мікси тегів (сума ₴-екв)",
        loc="upper left",
        bbox_to_anchor=(1.01, 0.86),
        fontsize=8,
        title_fontsize=9,
        borderaxespad=0.0,
        handlelength=2.2,
    )
    axes.add_artist(mixes)
    legends.append(mixes)

    currency_y = 0.86 - (len(combos) + 1.8) * 0.052
    currencies_legend = axes.legend(
        handles=[
            Patch(
                facecolor="#30363d",
                hatch=CURRENCY_HATCH.get(currency, "") or None,
                label=f"{currency} — {_format_amount(total)} ₴",
            )
            for currency, total in currency_total.items()
        ],
        title="Валюта (₴-екв)",
        loc="upper left",
        bbox_to_anchor=(1.01, currency_y),
        fontsize=8,
        title_fontsize=9,
        borderaxespad=0.0,
    )
    axes.add_artist(currencies_legend)
    legends.append(currencies_legend)

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, bbox_inches="tight", bbox_extra_artists=legends)
