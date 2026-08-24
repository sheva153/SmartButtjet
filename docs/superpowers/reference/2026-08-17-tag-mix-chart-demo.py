#!/usr/bin/env python3
"""Demo v6: legend groups records by their TAG-MIX (combination) and sums them,
with a multi-colour swatch matching the bar's block-mix. tag=colour (clear blocks,
short blends; readable for 3 tags), currency=hatch, height=UAH-equiv, income up /
expense down. Also: Разом (income/expense/net) and Валюта totals. Week + month
(month x = "day + weekday"). No emoji in matplotlib text.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.legend_handler import HandlerBase
from matplotlib.patches import Patch, Rectangle

TMP = Path("/Users/mikeshevchuk/.claude/jobs/bce437f3/tmp")
SERVE = TMP / "demo-serve"

FX_TO_UAH = {"UAH": 1.0, "USD": 41.0, "EUR": 45.0}
CURRENCY_HATCH = {"UAH": "", "USD": "///", "EUR": "..."}
TAG_LABELS = {
    "salary": "Зарплата",
    "freelance": "Фриланс",
    "sales": "Продажі",
    "consulting": "Консалтинг",
    "gift": "Подарунок",
    "card": "Картка",
    "cash": "Готівка",
    "rent": "Оренда",
    "groceries": "Продукти",
    "cafe": "Кафе",
    "dentistry": "Стоматологія",
    "transport": "Транспорт",
}
TAG_COLOR = {
    "salary": "#3fb950",
    "freelance": "#58a6ff",
    "sales": "#bc8cff",
    "consulting": "#d29922",
    "gift": "#f778ba",
    "card": "#1f6feb",
    "cash": "#2ea043",
    "rent": "#f85149",
    "groceries": "#db6d28",
    "cafe": "#a371f7",
    "dentistry": "#e3b341",
    "transport": "#39c5cf",
}
NO_TAG = ("_none", "Без тегу", "#8b949e")
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
_ORDER = {t: i for i, t in enumerate([*TAG_LABELS, NO_TAG[0]])}


@dataclass
class Rec:
    slot: int
    kind: str
    amount: float
    currency: str
    tags: list[str] = field(default_factory=list)


def _fmt(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def _label(t: str) -> str:
    return TAG_LABELS.get(t, NO_TAG[1] if t == NO_TAG[0] else t)


def _colours(tags: list[str]) -> list[str]:
    return [TAG_COLOR.get(t, NO_TAG[2]) for t in tags]


def _cmap_for(tags: list[str]) -> LinearSegmentedColormap:
    colours = _colours(tags)
    n = len(colours)
    if n == 1:
        return LinearSegmentedColormap.from_list("mix", [colours[0], colours[0]])
    seg, blend, stops = 1.0 / n, (1.0 / n) * 0.14, []
    for i, c in enumerate(colours):
        stops.append((round(i * seg + (blend if i else 0.0), 4), c))
        stops.append((round((i + 1) * seg - (blend if i < n - 1 else 0.0), 4), c))
    return LinearSegmentedColormap.from_list("mix", stops)


class MixHandler(HandlerBase):
    """Legend key that shows a tag-mix as side-by-side colour blocks."""

    def __init__(self, colours: list[str]) -> None:
        super().__init__()
        self._colours = colours

    def create_artists(self, legend, orig, xd, yd, width, height, fontsize, trans):
        n = len(self._colours)
        return [
            Rectangle(
                (xd + width * i / n, yd),
                width / n,
                height,
                facecolor=c,
                edgecolor="none",
                transform=trans,
            )
            for i, c in enumerate(self._colours)
        ]


def render(
    records: list[Rec],
    xlabels: list[str],
    title: str,
    out: Path,
    figsize: tuple[float, float],
    tick_fontsize: int = 9,
) -> None:
    figure = Figure(figsize=figsize, dpi=150)
    FigureCanvasAgg(figure)
    axes = figure.subplots()

    bar_w = 0.64
    grad = np.linspace(0, 1, 256).reshape(1, -1)
    bases: dict[tuple[int, str], float] = {}
    combo_total: dict[tuple[str, ...], float] = {}
    curr_total: dict[str, float] = {}
    income_total = expense_total = 0.0
    lo = hi = 0.0

    for r in records:
        uah = r.amount * FX_TO_UAH[r.currency]
        h = uah if r.kind == "income" else -uah
        tags = r.tags or [NO_TAG[0]]
        base = bases.get((r.slot, r.kind), 0.0)
        y0, y1 = base, base + h
        x0, x1 = r.slot - bar_w / 2, r.slot + bar_w / 2
        ylo, yhi = min(y0, y1), max(y0, y1)
        axes.imshow(
            grad,
            extent=(x0, x1, ylo, yhi),
            aspect="auto",
            origin="lower",
            cmap=_cmap_for(tags),
            vmin=0,
            vmax=1,
            zorder=2,
        )
        axes.add_patch(
            Rectangle(
                (x0, ylo),
                x1 - x0,
                yhi - ylo,
                fill=False,
                hatch=CURRENCY_HATCH[r.currency] or None,
                edgecolor="#0d1117",
                linewidth=0.7,
                zorder=3,
            )
        )
        bases[(r.slot, r.kind)] = y1
        lo, hi = min(lo, ylo), max(hi, yhi)
        combo = tuple(sorted(tags, key=lambda t: _ORDER.get(t, 99)))
        combo_total[combo] = combo_total.get(combo, 0.0) + uah
        curr_total[r.currency] = curr_total.get(r.currency, 0.0) + uah
        income_total += uah if r.kind == "income" else 0.0
        expense_total += uah if r.kind == "expense" else 0.0

    axes.axhline(0, color="#c9d1d9", linewidth=1.0, zorder=4)
    axes.set_xlim(-0.7, len(xlabels) - 0.3)
    pad = (hi - lo) * 0.08 or 1.0
    axes.set_ylim(lo - pad, hi + pad)
    axes.set_xticks(range(len(xlabels)))
    axes.set_xticklabels(xlabels, fontsize=tick_fontsize)
    axes.set_ylabel("Сума, ₴-еквівалент")
    axes.set_title(title, fontsize=11)
    axes.grid(axis="y", linestyle=":", alpha=0.3, zorder=0)

    net = income_total - expense_total
    legends = []

    l1 = axes.legend(
        handles=[
            Patch(facecolor="#3fb950", label=f"Дохід — {_fmt(income_total)} ₴"),
            Patch(facecolor="#f85149", label=f"Витрати — {_fmt(expense_total)} ₴"),
            Patch(facecolor="#58a6ff", label=f"Чистими — {_fmt(net)} ₴"),
        ],
        title="Разом (₴-екв)",
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=8,
        title_fontsize=9,
        borderaxespad=0.0,
    )
    axes.add_artist(l1)
    legends.append(l1)

    # mix legend: one row per unique tag-combination, biggest sum first
    combos = sorted(combo_total, key=lambda c: combo_total[c], reverse=True)
    proxies, handler_map = [], {}
    for combo in combos:
        p = Patch(
            label=f"{' + '.join(_label(t) for t in combo)} — {_fmt(combo_total[combo])} ₴"
        )
        proxies.append(p)
        handler_map[p] = MixHandler(_colours(list(combo)))
    l2 = axes.legend(
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
    axes.add_artist(l2)
    legends.append(l2)

    y_curr = 0.86 - (len(combos) + 1.8) * 0.052
    l3 = axes.legend(
        handles=[
            Patch(
                facecolor="#30363d",
                hatch=CURRENCY_HATCH[c] or None,
                label=f"{c} — {_fmt(curr_total[c])} ₴",
            )
            for c in curr_total
        ],
        title="Валюта (₴-екв)",
        loc="upper left",
        bbox_to_anchor=(1.01, y_curr),
        fontsize=8,
        title_fontsize=9,
        borderaxespad=0.0,
    )
    axes.add_artist(l3)
    legends.append(l3)

    figure.savefig(out, bbox_inches="tight", bbox_extra_artists=legends)


WEEK = [
    Rec(0, "income", 20000, "UAH", ["salary", "card"]),
    Rec(0, "expense", 8000, "UAH", ["rent", "card"]),
    Rec(1, "income", 300, "USD", ["freelance", "card"]),
    Rec(2, "expense", 1200, "UAH", ["groceries", "cash"]),
    Rec(2, "expense", 500, "UAH", ["cafe", "card"]),
    Rec(3, "income", 200, "EUR", ["consulting", "card"]),
    Rec(3, "expense", 1600, "UAH", ["groceries", "cafe", "card"]),  # 3 tags
    Rec(4, "income", 4000, "UAH", ["sales", "cash"]),
    Rec(4, "expense", 900, "UAH", ["dentistry"]),
    Rec(5, "expense", 1500, "UAH", ["transport", "cash"]),
    Rec(5, "income", 1000, "UAH", []),
    Rec(6, "income", 150, "USD", ["gift", "card"]),
    Rec(6, "expense", 700, "UAH", ["groceries", "cash"]),  # same mix as Ср -> grouped
]
MONTH = [
    Rec(0, "income", 18000, "UAH", ["salary", "card"]),
    Rec(2, "expense", 8000, "UAH", ["rent", "card"]),
    Rec(4, "expense", 1300, "UAH", ["groceries", "cash"]),
    Rec(6, "income", 250, "USD", ["freelance", "card"]),
    Rec(9, "expense", 600, "UAH", ["cafe", "card"]),
    Rec(9, "expense", 1800, "UAH", ["groceries", "transport", "card"]),  # 3 tags
    Rec(13, "income", 5000, "UAH", ["sales", "cash"]),
    Rec(16, "expense", 2500, "UAH", ["dentistry", "card"]),
    Rec(18, "income", 300, "EUR", ["consulting", "card"]),
    Rec(21, "expense", 1100, "UAH", ["groceries", "cash"]),  # same mix -> grouped
    Rec(24, "income", 15000, "UAH", ["salary", "card"]),  # same mix -> grouped
    Rec(24, "expense", 900, "UAH", ["transport"]),
    Rec(27, "expense", 700, "UAH", ["cafe", "cash"]),
    Rec(30, "income", 120, "USD", ["gift", "card"]),
]


def build_html(week_png: Path, month_png: Path) -> None:
    wb = base64.b64encode(week_png.read_bytes()).decode()
    mb = base64.b64encode(month_png.read_bytes()).decode()

    def table(records: list[Rec], labels: list[str]) -> str:
        return "\n".join(
            f"<tr><td>{labels[r.slot].replace(chr(10), ' ')}</td>"
            f"<td class='{r.kind}'>{'дохід' if r.kind == 'income' else 'витрата'}</td>"
            f"<td class='num'>{_fmt(r.amount)} {r.currency}</td>"
            f"<td class='num'>{_fmt(r.amount * FX_TO_UAH[r.currency])} ₴</td>"
            f"<td>{', '.join(_label(t) for t in r.tags) or '—'}</td></tr>"
            for r in records
        )

    month_labels = [
        f"{d}\n{WEEKDAYS[date(2026, 8, d).weekday()]}" for d in range(1, 32)
    ]
    html = f"""<!DOCTYPE html><html lang=uk><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<link rel=icon href="data:,">
<title>Демо — групування за міксом тегів</title>
<style>
body{{margin:0;background:#0d1117;color:#c9d1d9;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px 18px 60px}}
h1{{font-size:22px}} h2{{font-size:17px;border-bottom:1px solid #30363d;padding-bottom:.3em;margin-top:1.4em}}
img{{max-width:100%;border:1px solid #30363d;border-radius:10px;background:#fff}}
.scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}}
td,th{{padding:6px 9px;border-bottom:1px solid #30363d;text-align:left}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase}}
.num{{font-family:ui-monospace,Menlo,monospace;text-align:right}}
.income{{color:#3fb950}} .expense{{color:#f85149}}
.note{{color:#8b949e;font-size:13px}} code{{color:#58a6ff}} ul{{font-size:14px}}
</style></head><body><div class=wrap>
<h1>Демо v6 — легенда групує за МІКСОМ тегів і підсумовує</h1>
<p class=note>Прототип. Не в боті — приклад для підтвердження перед портуванням у <code>report_chart.py</code>.</p>
<h2>Головне</h2>
<ul>
<li><b>Групування за міксом.</b> Усі записи з однаковим набором тегів (напр. «Картка + Продукти») по всіх днях зводяться в <b>один рядок легенди</b> із сумою ₴-екв і багатоколірним свотчем цього міксу.</li>
<li><b>Колір = тег, чіткі блоки</b> (2–3 теги читабельні); <b>візерунок = валюта</b> (₴ суцільний, $ <code>///</code>, € <code>...</code>).</li>
<li><b>Висота = ₴-еквівалент</b> (USD×41, EUR×45); дохід вгору, витрата вниз, stacked.</li>
<li><b>Ще в легенді:</b> «Разом» (Дохід/Витрати/Чистими) і «Валюта» (сума ₴-екв).</li>
<li><b>Місяць:</b> вісь X = «число + день тижня».</li>
</ul>
<h2>Тиждень</h2>
<div class=scroll><img src="data:image/png;base64,{wb}" alt="week"></div>
<p class=note>У прикладі «Продукти + Готівка» є і в Ср, і в Нд — у легенді вони підсумовані в один рядок.</p>
<div class=scroll><table><tr><th>День</th><th>Тип</th><th>Сума</th><th>₴-екв</th><th>Теги</th></tr>
{table(WEEK, WEEKDAYS)}
</table></div>
<h2>Місяць (серпень 2026) — з днями тижня</h2>
<div class=scroll><img src="data:image/png;base64,{mb}" alt="month"></div>
<div class=scroll><table><tr><th>Число</th><th>Тип</th><th>Сума</th><th>₴-екв</th><th>Теги</th></tr>
{table(MONTH, month_labels)}
</table></div>
<p class=note>Підтвердь: групування за міксом — те, що треба? курс USD 41 / EUR 45 (з конфігу)? Далі вбудую в бота (+ команда /tags: показати/додати теги й аліаси).</p>
</div></body></html>"""
    (TMP / "tagchart_demo.html").write_text(html, encoding="utf-8")
    SERVE.mkdir(parents=True, exist_ok=True)
    (SERVE / "tagchart.html").write_text(html, encoding="utf-8")
    (SERVE / "index.html").write_text(html, encoding="utf-8")


week_png = TMP / "tagchart_week.png"
month_png = TMP / "tagchart_month.png"
render(
    WEEK,
    WEEKDAYS,
    "Звіт за тиждень — групування легенди за міксом тегів",
    week_png,
    (13, 6.6),
)
render(
    MONTH,
    [f"{d}\n{WEEKDAYS[date(2026, 8, d).weekday()]}" for d in range(1, 32)],
    "Звіт за місяць (серпень 2026) — число + день тижня",
    month_png,
    (17, 6.6),
    8,
)
build_html(week_png, month_png)
print("done: v6 week+month PNGs + HTML; served copy updated")
