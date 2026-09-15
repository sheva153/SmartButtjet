# Yearly Goals, Forecasting, Tag Reporting & CSV Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add monthly+yearly income goals, a configurable end-of-period forecast, goal+forecast lines and a tag breakdown on the chart with an expanded tag taxonomy, forecast-tiered motivation phrases, an elite fun-basket with a luxury segment, and a `/import` CSV command.

**Architecture:** `ChatGoal` gains a `period` (month|year) and is keyed by `(chat_id, period)`. `GoalService` computes a forecast (linear/average/weighted, config-selected) and derives a forecast-driven status. The matplotlib report draws the goal + forecast lines and a per-tag breakdown. `build_fun_summary` gains a luxury tier displayed as 0.5/1. A new `/import` handler bulk-loads a CSV via the repository.

**Tech Stack:** Python 3.13, aiogram 3.30, pydantic v2, pandas, matplotlib (Agg), uv, just, pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-08-17-goals-forecast-tags-import-design.md`

## Global Constraints

- `amount` is always `Decimal > 0`; income/expense sign lives in `type`.
- Matplotlib OO API only (`Figure` + `FigureCanvasAgg`), never `pyplot`.
- Every commit passes `just check` (`ruff format --check` + `ruff check` + `pyright` + `pytest`) with **0 warnings**.
- `Period` stays the single source of truth via `typing.get_args`.
- Bot copy Ukrainian; code identifiers and GitHub text English.
- The PostToolUse ruff hook strips an import added before its first use — add imports with their use.
- Reads stay side-effect free; only migrate/atomic-write paths touch disk.

---

### Task 1: `ChatGoal.period` + period-keyed goal repository

**Files:**
- Modify: `src/income_stats/models/domain.py`
- Modify: `src/income_stats/models/__init__.py`
- Modify: `src/income_stats/repositories/records_repository.py`
- Test: `tests/unit/test_models.py`, `tests/integration/test_csv_repository.py`

**Interfaces:**
- Produces: `GoalPeriod = Literal["month", "year"]`; `ChatGoal.period: GoalPeriod = "month"`; repository `get_goal_sync(chat_id, period="month")`, `set_goal_sync(chat_id, amount, currency, updated_by, period="month")`, async `get_goal(chat_id, period="month")` / `set_goal(..., period="month")`, upsert keyed on `(chat_id, period)`; legacy `goals.csv` without `period` backfills `"month"`.

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_csv_repository.py
def test_month_and_year_goals_are_independent(tmp_path):
    repo = _repo(tmp_path)  # existing helper/fixture in this file
    repo.set_goal_sync(1, Decimal("50000"), "UAH", 7, period="month")
    repo.set_goal_sync(1, Decimal("600000"), "UAH", 7, period="year")
    assert repo.get_goal_sync(1, "month").amount == Decimal("50000")
    assert repo.get_goal_sync(1, "year").amount == Decimal("600000")


def test_legacy_goals_without_period_are_month(tmp_path):
    repo = _repo(tmp_path)
    cols = [c for c in GOAL_COLUMNS if c != "period"]
    frame = pd.DataFrame([_goal_row_without_period()], columns=cols)
    repo.goals_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(repo.goals_path, index=False)
    assert repo.get_goal_sync(1, "month").amount > 0
```

```python
# tests/unit/test_models.py
def test_chat_goal_defaults_to_month_period() -> None:
    goal = ChatGoal(chat_id=1, amount=Decimal("10"), currency="UAH", updated_by=1)
    assert goal.period == "month"
```

Build `_goal_row_without_period()` inline mirroring the file's existing goal-row construction (a dict of string cells for every GOAL_COLUMN except `period`). Import `GOAL_COLUMNS`, `pandas as pd`, `ChatGoal` at the top.

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/integration/test_csv_repository.py -k goal tests/unit/test_models.py -k period -v` → FAIL (unexpected `period` kwarg / attribute).

- [ ] **Step 3: Model**

`domain.py` — add near `Period`:
```python
GoalPeriod = Literal["month", "year"]
```
In `ChatGoal`, after `chat_id`:
```python
    period: GoalPeriod = "month"
```
Export `GoalPeriod` from `models/__init__.py`.

- [ ] **Step 4: Repository period keying + backfill**

In `records_repository.py`:
- `get_goal_sync(self, chat_id, period="month")`: filter `(frame["chat_id"] == str(chat_id)) & (frame["period"] == period)`, return last or None.
- `set_goal_sync(self, chat_id, amount, currency, updated_by, period="month")`: build `ChatGoal(..., period=period)`; upsert index = rows matching BOTH chat_id and period.
- async `get_goal(self, chat_id, period="month")` / `set_goal(self, chat_id, amount, currency, updated_by, period="month")` delegate under the async lock. Update the `RecordsRepository` Protocol signatures.
- Add a `_read` backfill for goals: since `_read` raises on missing columns, add a dedicated read path — in `get_goal_sync`/`set_goal_sync` read via a helper that, when the `period` column is absent, adds `frame["period"] = "month"` before selecting `GOAL_COLUMNS`. Implement `_read_goals()` mirroring `_read` but backfilling `period`.

- [ ] **Step 5: Run to verify pass** — `uv run pytest tests/integration/test_csv_repository.py tests/unit/test_models.py -v` → PASS.

- [ ] **Step 6: `just check` + commit**

```bash
git add src/income_stats tests
git commit -m "feat: period-keyed monthly and yearly goals"
```

---

### Task 2: Configurable forecast + forecast-driven status

**Files:**
- Modify: `src/income_stats/config/settings.py` (`AnalyticsConfig.forecast_method`)
- Modify: `src/income_stats/services/goal_service.py`
- Test: `tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: `AnalyticsConfig`, repository `get_goal(chat_id, period)`, `AnalyticsService.frame` + `totals_by_type`.
- Produces: `forecast_total(daily: list[Decimal], total_days: int, method: str) -> Decimal`; `GoalProgress` gains `forecast: Decimal` and `period: GoalPeriod`; `GoalService.progress(chat_id, *, today, period="month")`; status ∈ {"reached","on_track","off_track"}; `GoalService` ctor takes `forecast_method: str`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_goal_service.py
from income_stats.services.goal_service import forecast_total


def test_forecast_linear_projects_runrate():
    # 1000 over 10 elapsed days, 30-day period -> 3000
    daily = [Decimal("100")] * 10
    assert forecast_total(daily, 30, "linear") == Decimal("3000")


def test_forecast_weighted_favours_recent_days():
    daily = [Decimal("0")] * 9 + [Decimal("100")]  # only last day earned
    linear = forecast_total(daily, 30, "linear")
    weighted = forecast_total(daily, 30, "weighted")
    assert weighted > linear  # recent surge extrapolated stronger


def test_forecast_empty_is_zero():
    assert forecast_total([], 30, "weighted") == Decimal()
```

```python
async def test_progress_status_off_track_when_forecast_below_goal(goal_service_low):
    p = await goal_service_low.progress(1, today=date(2026, 8, 20), period="month")
    assert p.status == "off_track"
    assert p.forecast < p.amount


async def test_progress_year_period(goal_service_year):
    p = await goal_service_year.progress(1, today=date(2026, 8, 20), period="year")
    assert p.period == "year"
```

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/unit/test_goal_service.py -k "forecast or status or year" -v` → FAIL.

- [ ] **Step 3: Config field**

`settings.py` `AnalyticsConfig`:
```python
    forecast_method: Literal["linear", "average", "weighted"] = "weighted"
```
(`Literal` is imported from `typing` — add if missing.)

- [ ] **Step 4: forecast_total helper**

`goal_service.py` (module level):
```python
def forecast_total(daily: list[Decimal], total_days: int, method: str) -> Decimal:
    """Project an end-of-period total from per-elapsed-day amounts.

    projected = actual + rate * remaining_days, where `rate` (per day) depends
    on the method. Deterministic; returns Decimal() for an empty/zero series.
    """
    elapsed = len(daily)
    actual = sum(daily, start=Decimal())
    if elapsed == 0 or actual == 0:
        return actual
    if method == "average":
        window = daily[-7:]
        rate = sum(window, start=Decimal()) / Decimal(len(window))
    elif method == "weighted":
        weights = range(1, elapsed + 1)
        numer = sum(day * Decimal(w) for day, w in zip(daily, weights, strict=True))
        rate = numer / Decimal(sum(weights))
    else:  # linear
        rate = actual / Decimal(elapsed)
    remaining = max(0, total_days - elapsed)
    return actual + rate * Decimal(remaining)
```

- [ ] **Step 5: GoalProgress + progress()**

Add `forecast: Decimal` and `period: GoalPeriod` to `GoalProgress`. `GoalService.__init__` gains `forecast_method: str` (store it). Rewrite `progress`:
```python
async def progress(self, chat_id, *, today, period="month"):
    goal = await self._repository.get_goal(chat_id, period)
    if goal is None:
        return None
    frame = await self._analytics.frame(chat_id, period, today=today)
    totals = self._analytics.totals_by_type(frame)
    actual = totals.get(goal.currency, {}).get("income", Decimal())
    if period == "year":
        total_days = 366 if calendar.isleap(today.year) else 365
        elapsed = today.timetuple().tm_yday
    else:
        total_days = calendar.monthrange(today.year, today.month)[1]
        elapsed = today.day
    daily = _daily_income(frame, goal.currency, elapsed, today, period)
    forecast = forecast_total(daily, total_days, self._forecast_method)
    days_left = max(1, total_days - elapsed)
    per_day = max(Decimal(), goal.amount - actual) / Decimal(days_left)
    if actual >= goal.amount:
        status = "reached"
    elif forecast >= goal.amount:
        status = "on_track"
    else:
        status = "off_track"
    return GoalProgress(
        goal.amount, goal.currency, actual, forecast, per_day, status, period
    )
```
Add `_daily_income(frame, currency, elapsed, today, period) -> list[Decimal]`: bucket the goal-currency income rows by day-of-period into a list of length `elapsed` (index 0 = first day of the period), summing amounts, zero-filled. Map `"ahead"`→`on_track` semantics: `_phrase` keys become `{"reached","on_track","off_track"}` mapped to `reached_phrases`/`ahead_phrases`/`behind_phrases`.

- [ ] **Step 6: Run to verify pass**, then `just check` + commit

```bash
git add src/income_stats tests/unit/test_goal_service.py
git commit -m "feat: configurable end-of-period forecast with forecast-driven status"
```

---

### Task 3: `/goal <period> <amount>` + render both goals with forecast

**Files:**
- Modify: `src/income_stats/handlers/goal_handler.py`
- Modify: `src/income_stats/services/goal_service.py` (`render`)
- Modify: `src/income_stats/bot/application.py` (pass `forecast_method` into GoalService)
- Modify: `src/income_stats/handlers/income_handler.py` (after-save line uses month period explicitly)
- Test: `tests/handlers/test_goal_handler.py`, `tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: `GoalService.progress(..., period)`, repository `set_goal(..., period)`.
- Produces: `/goal <month|year> <amount> [CUR]` sets that period; `/goal <amount>` → month; `/goal` shows both period cards; `GoalService.render(progress)` includes the forecast line.

- [ ] **Step 1: Failing tests**

```python
# tests/handlers/test_goal_handler.py
async def test_goal_year_sets_year_period(...):
    # text "/goal year 600000" → repository.set_goal called with period="year", amount 600000
async def test_goal_bare_amount_is_month(...):
    # text "/goal 50000" → set_goal period="month"
async def test_goal_show_lists_both_periods(...):
    # month+year goals set → "/goal" answer contains both "місяць" and "рік"
```

```python
# tests/unit/test_goal_service.py
def test_render_includes_forecast_line(goal_progress_factory):
    text = goal_service.render(progress)  # progress with forecast
    assert "Прогноз" in text
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Handler**

Rewrite `goal_handler` to parse an optional leading period token:
```python
_PERIOD_ALIASES = {"month": "month", "місяць": "month", "рік": "year", "year": "year"}
...
if command.args:
    parts = command.args.split()
    period = "month"
    idx = 0
    if parts and parts[0].casefold() in _PERIOD_ALIASES:
        period = _PERIOD_ALIASES[parts[0].casefold()]
        idx = 1
    try:
        amount = Decimal(parts[idx].replace(",", "."))
    except (InvalidOperation, IndexError):
        await message.answer("Формат: /goal [month|year] 50000 [UAH]")
        return
    currency = (
        parts[idx + 1].upper()
        if len(parts) > idx + 1
        else app_config.income.default_currency
    )
    if amount <= 0:
        await message.answer("Ціль має бути більшою за нуль.")
        return
    user = message.from_user
    await repository.set_goal(
        message.chat.id, amount, currency, user.id if user else 0, period=period
    )
    label = "місяць" if period == "month" else "рік"
    await message.answer(f"🎯 Ціль встановлено: {amount:,.0f} {currency}/{label}")
    return
# no args → show both
lines = []
for period in ("month", "year"):
    p = await goal_service.progress(message.chat.id, today=today, period=period)
    if p is not None:
        lines.append(goal_service.render(p))
await message.answer(
    "\n\n".join(lines) if lines else "Ціль ще не задана. Встанови: /goal month 50000"
)
```

- [ ] **Step 4: render() forecast line**

In `GoalService.render`, add after the "Виконано" line:
```python
        pct_fc = (progress.forecast / progress.amount * 100) if progress.amount else Decimal()
        label = "місяць" if progress.period == "month" else "рік"
        lines = [
            f"🎯 Ціль ({label}): {progress.amount:,.0f} {progress.currency}",
            f"Виконано: {progress.actual:,.0f} ({pct:.0f}%)",
            f"Прогноз до кінця: ~{progress.forecast:,.0f} ({pct_fc:.0f}%)",
        ]
```
Keep the behind per-day line + the tier phrase.

- [ ] **Step 5: Wire forecast_method + month after-save**

`application.py`: `GoalService(repository, analytics_service, config.goals, config.analytics.forecast_method)`.
`income_handler.py`: `after_save_line(..., period="month")` if that param is added; otherwise leave (month is default). `after_save_line` in GoalService: add `period="month"` default and pass through to `progress`.

- [ ] **Step 6: Run + `just check` + commit**

```bash
git add src/income_stats tests
git commit -m "feat: /goal month|year command and both-goal progress with forecast"
```

---

### Task 4: Goal + forecast lines on the chart

**Files:**
- Modify: `src/income_stats/services/report_chart.py` (`render_report_png`)
- Modify: `src/income_stats/services/analytics_service.py` (`build_chart_artifacts`, `_write_chart_artifacts`)
- Test: `tests/unit/test_report_chart.py`, `tests/unit/test_analytics_service.py`

**Interfaces:**
- Consumes: goal lookup + forecast from Tasks 1-2.
- Produces: `render_report_png(frame, period, reference, path, date_range=None, goal=None, forecast=None)` — draws a labeled goal line and a dashed forecast line when provided.

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_report_chart.py
def test_render_draws_goal_and_forecast(tmp_path):
    path = tmp_path / "c.png"
    render_report_png(
        _month_frame(),
        "month",
        date(2026, 8, 17),
        path,
        goal=Decimal("50000"),
        forecast=Decimal("42000"),
    )
    assert path.exists() and path.stat().st_size > 0
```

- [ ] **Step 2: Run to verify fail** (unexpected `goal` kwarg).

- [ ] **Step 3: render_report_png**

Add params `goal: Decimal | None = None, forecast: Decimal | None = None`. After the bar loop and `axhline(0)`, before legend:
```python
if goal is not None:
    axes.axhline(float(goal), color="#d29922", linewidth=1.4, linestyle="-")
    axes.annotate(
        f"🎯 Ціль {_format_amount(float(goal))}",
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
```

- [ ] **Step 4: Thread from analytics**

`build_chart_artifacts`: for a `resolved_period in ("month","year")` and no `date_range`, look up `goal = await self._repository.get_goal(chat_id, resolved_period)` and compute a forecast via the same helper GoalService uses (extract `forecast_total` usage into a small internal call, or call a GoalService method). Pass `goal.amount`/forecast (goal currency single-currency only; if the frame has multiple currencies, pass `None` to avoid an ambiguous line). Thread through `_write_chart_artifacts` → `render_report_png`. Requires `AnalyticsService` to hold the repository (it already does via `self._repository`) and `forecast_method` (add to ctor / read from `self._config.forecast_method`).

- [ ] **Step 5: Run + `just check` + commit**

```bash
git add src/income_stats tests
git commit -m "feat: draw goal and forecast lines on the report chart"
```

---

### Task 5: Tag breakdown on the chart

**Files:**
- Modify: `src/income_stats/services/report_chart.py`
- Modify: `src/income_stats/services/analytics_service.py` (`_write_chart_artifacts` passes tag totals)
- Test: `tests/unit/test_report_chart.py`

**Interfaces:**
- Consumes: `AnalyticsService.tag_breakdown(frame)`.
- Produces: `render_report_png(..., tag_totals: dict[str, float] | None = None)` — when non-empty, a second axes below the bars shows one horizontal bar per tag, distinct color, label `{emoji} {tag} {amount}`; omitted (no extra axes) when empty.

- [ ] **Step 1: Failing test**

```python
def test_render_includes_tag_breakdown(tmp_path):
    from income_stats.services.report_chart import _tag_label

    assert _tag_label("rent").startswith("🏠")
    path = tmp_path / "c.png"
    render_report_png(
        _month_frame(),
        "month",
        date(2026, 8, 17),
        path,
        tag_totals={"rent": 8000.0, "card": 12000.0},
    )
    assert path.exists() and path.stat().st_size > 0
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement**

Add a tag→(emoji,label) map + `_tag_label(tag)`:
```python
_TAG_META = {
    "card": ("💳", "Картка"),
    "cash": ("💵", "Готівка"),
    "rent": ("🏠", "Оренда"),
    "utilities": ("💡", "Комуналка"),
    "dentistry": ("🦷", "Стоматологія"),
    "health": ("🩺", "Медицина"),
    "groceries": ("🛒", "Продукти"),
    "transport": ("🚕", "Транспорт"),
    "cafe": ("☕", "Кафе"),
    "subscriptions": ("📱", "Підписки"),
    "education": ("🎓", "Освіта"),
}


def _tag_label(tag: str) -> str:
    emoji, name = _TAG_META.get(tag, ("🏷", tag))
    return f"{emoji} {name}"
```
When `tag_totals` is non-empty, create the figure with two rows
(`figure.subplots(2, 1, height_ratios=[3, 1])`) and draw the tag bars on the
second axes: `axes2.barh(range(n), values, color=[cmap(i) ...])`, y-ticklabels
`_tag_label(tag)`, value labels `_format_amount`. When empty, keep the single
axes (no second subplot → no empty-axes warning). Keep everything OO-API.

- [ ] **Step 4: Thread tag totals**

`_write_chart_artifacts`: compute `tag_totals` from `AnalyticsService.tag_breakdown(frame)` — flatten to `{tag: float(sum across currencies)}` (single-currency deployments are the norm; sum is acceptable and documented), pass to `render_report_png`. Empty frame/no tags → `{}`.

- [ ] **Step 5: Run + `just check` + commit**

```bash
git add src/income_stats tests
git commit -m "feat: per-tag breakdown section on the report chart"
```

---

### Task 6: Expanded tag taxonomy (config)

**Files:**
- Modify: `config.yaml` (`income.tags`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_config.py
def test_tags_include_purpose_tags():
    config = load_config(Path("config.yaml"))
    for tag in ("card", "cash", "rent", "dentistry", "health", "transport"):
        assert tag in config.income.tags
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Expand `income.tags`**

```yaml
  tags:
    card: [картка, карта, на карту, на картку, card]
    cash: [готівка, готівкою, кеш, cash]
    rent: [оренда, квартплата, орендна плата]
    utilities: [комуналка, комунальні, комуналку]
    dentistry: [стоматологія, стомат, зуби, зубний]
    health: [медицина, ліки, аптека, лікар]
    groceries: [продукти, їжа, магазин]
    transport: [транспорт, таксі, пальне, бензин, проїзд]
    cafe: [кафе, ресторан, кава]
    subscriptions: [підписка, підписки, subscription]
    education: [курс, курси, навчання, освіта]
```

- [ ] **Step 4: Run + `just check` + commit**

```bash
git add config.yaml tests/unit/test_config.py
git commit -m "feat: expanded multi-tag taxonomy (payment + purpose tags)"
```

---

### Task 7: Elite fun-basket (luxury tier + higher prices)

**Files:**
- Modify: `src/income_stats/models/domain.py` (`FunItem.luxury`)
- Modify: `src/income_stats/services/analytics_service.py` (`build_fun_summary`)
- Modify: `config.yaml` (`fun_summary.items`)
- Test: `tests/unit/test_analytics_service.py`, `tests/unit/test_config.py`

**Interfaces:**
- Produces: `FunItem.luxury: bool = False`; luxury items show only when `amount >= 0.5*price`, quantity displayed as `0.5` (`0.5*price ≤ amount < price`) or `1` (`amount ≥ price`).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_analytics_service.py
def test_luxury_item_shows_half_then_whole():
    rolex = FunItem(label="Rolex", emoji="⌚", price_uah=Decimal("400000"), luxury=True)
    cfg = FunSummaryConfig(items={"rolex": rolex}, phrases=["x"])
    half = build_fun_summary(_income(Decimal("250000")), cfg)  # 0.5*price ≤ amt < price
    whole = build_fun_summary(_income(Decimal("500000")), cfg)  # amt ≥ price
    assert "0.5 Rolex" in half
    assert "1 Rolex" in whole


def test_luxury_hidden_below_half_price():
    rolex = FunItem(label="Rolex", emoji="⌚", price_uah=Decimal("400000"), luxury=True)
    cfg = FunSummaryConfig(items={"rolex": rolex}, phrases=["x"])
    assert "Rolex" not in build_fun_summary(_income(Decimal("100000")), cfg)
```

```python
# tests/unit/test_config.py
def test_basket_has_luxury_tier():
    config = load_config(Path("config.yaml"))
    assert any(item.luxury for item in config.fun_summary.items.values())
    assert max(i.price_uah for i in config.fun_summary.items.values()) >= 100000
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Model + logic**

`FunItem`: add `luxury: bool = False`.
In `build_fun_summary`, change the availability + quantity logic:
```python
available = [
    item
    for item in config.items.values()
    if record.amount >= (item.price_uah / 2 if item.luxury else item.price_uah)
]
...
for item in selected:
    quantity = record.amount / item.price_uah
    if item.luxury:
        value = "1" if quantity >= 1 else "0.5"
    elif item.fractional:
        value = f"{quantity:.1f}"
    else:
        value = str(int(quantity))
    comparisons.append(f"{item.emoji} {value} {item.label}")
```

- [ ] **Step 4: config.yaml — raise all prices + luxury items**

Raise every existing `price_uah` (roughly +40-60%). Add luxury items, e.g.:
```yaml
    macbook: {label: "MacBook Pro", emoji: "💻", price_uah: 120000, luxury: true}
    iphone_pro: {label: "iPhone Pro", emoji: "📱", price_uah: 70000, luxury: true}
    rolex: {label: "Rolex", emoji: "⌚", price_uah: 400000, luxury: true}
    car: {label: "авто", emoji: "🚗", price_uah: 900000, luxury: true}
    trip: {label: "відпустку за кордоном", emoji: "🏝️", price_uah: 80000, luxury: true}
    business_flight: {label: "переліт бізнес-класом", emoji: "✈️", price_uah: 60000, luxury: true}
```

- [ ] **Step 5: Run + `just check` + commit**

```bash
git add src/income_stats config.yaml tests
git commit -m "feat: elite fun-basket with luxury 0.5/1 tier and higher prices"
```

---

### Task 8: Forecast config value + stronger off-track phrases

**Files:**
- Modify: `config.yaml` (`analytics.forecast_method`, `goals.*_phrases`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_config.py
def test_forecast_method_and_phrase_tiers_configured():
    config = load_config(Path("config.yaml"))
    assert config.analytics.forecast_method == "weighted"
    assert len(config.goals.behind_phrases) >= 2
    assert len(config.goals.ahead_phrases) >= 2
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: config.yaml**

Under `analytics:` add `forecast_method: weighted`. Under `goals:` seed tiered phrases — `ahead_phrases` normal encouragement (forecast ≥ goal), `behind_phrases` **stronger** push (forecast < goal), `reached_phrases` celebration:
```yaml
  ahead_phrases:
    - "Чудовий темп! Прогноз каже — ціль буде твоя 🚀"
    - "Ідеш за графіком, тримай ритм ✨"
  behind_phrases:
    - "Прогноз нижчий за ціль — час натиснути сильніше 🔥"
    - "Ще не дотягуєш до цілі. Піднатужся — кожен запис наближає 💪"
  reached_phrases:
    - "Ціль досягнута! Ти неймовірна 🎉"
    - "Планку взято — вітаю! 🏆"
```

- [ ] **Step 4: Run + `just check` + commit**

```bash
git add config.yaml tests/unit/test_config.py
git commit -m "feat: weighted forecast default and forecast-tiered goal phrases"
```

---

### Task 9: `/import` CSV bulk import

**Files:**
- Modify: `src/income_stats/repositories/records_repository.py` (`import_records_sync` + async + Protocol)
- Create: `src/income_stats/handlers/import_handler.py`
- Modify: `src/income_stats/handlers/__init__.py`, `src/income_stats/bot/application.py` (register router)
- Test: `tests/integration/test_csv_repository.py`, `tests/handlers/test_import_handler.py`

**Interfaces:**
- Produces: `import_records_sync(records: list[IncomeRecord]) -> tuple[int, int]` (added, skipped) with dedupe by `(chat_id, telegram_message_id, source_index)`; `/import` handler (admin-only) reads a CSV document caption.

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_csv_repository.py
def test_import_records_adds_and_dedupes(tmp_path):
    repo = _repo(tmp_path)
    r = make_record(chat_id=1, telegram_message_id=10, source_index=0)
    assert repo.import_records_sync([r]) == (1, 0)
    assert repo.import_records_sync([r]) == (0, 1)  # dedupe
```

```python
# tests/handlers/test_import_handler.py
async def test_import_admin_only(...):
    # non-admin /import → "лише для адміністраторів", repository.import_records not called
async def test_import_parses_csv_document(...):
    # admin sends a CSV doc with caption /import → repository.import_records called,
    # reply contains "Імпортовано"
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Repository**

```python
def import_records_sync(self, records: list[IncomeRecord]) -> tuple[int, int]:
    with self._sync_lock:
        frame = self._read_records_unlocked()
        existing = {
            (row["chat_id"], row["telegram_message_id"], row["source_index"])
            for _, row in frame.iterrows()
        }
        added = 0
        skipped = 0
        new_rows = []
        for record in records:
            key = (
                str(record.chat_id),
                str(record.telegram_message_id),
                str(record.source_index),
            )
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            new_rows.append(_to_row(record))
            added += 1
        if new_rows:
            frame = pd.concat(
                [frame, pd.DataFrame(new_rows, columns=RECORD_COLUMNS)],
                ignore_index=True,
            )
            self._atomic_write(frame, self.records_path)
        return added, skipped
```
Add `async def import_records(self, records)` under the async lock; extend the Protocol.

- [ ] **Step 4: Handler**

`import_handler.py`: `import_router`, `@import_router.message(Command("import"))` AND a document handler. Simplest: handle a `Message` whose `document` is present and caption starts with `/import` — register `@import_router.message(F.caption.startswith("/import") & F.document)` plus a plain `Command("import")` that tells the user to attach a CSV. Admin-gate via `is_telegram_admin` (as `export_handler` does). Download the document with `bot.download`, decode UTF-8, `csv.DictReader`, build `IncomeRecord` per row (categories/tags via `json.loads`; missing `type`→"income"; fresh `id`; `chat_id`=message.chat.id; `updated_by`=user.id), collect valid, skip invalid (count). Call `repository.import_records(valid)`, reply `f"Імпортовано {added}, пропущено {skipped + invalid}."` Read `export_handler` first to match the admin-check + DI style.

- [ ] **Step 5: Register + wire**

Add `import_router` to `handlers/__init__.py` and the dispatcher `include_routers` in `application.py`.

- [ ] **Step 6: Run + `just check` + commit**

```bash
git add src/income_stats tests
git commit -m "feat: /import command for bulk CSV record import"
```

---

## Self-Review

**Spec coverage:** §1 goals→T1+T3; §2 forecast→T2 (+T8 config); §3 chart goal/forecast→T4; §4 tags chart→T5, taxonomy→T6; §5 forecast phrases→T2 status + T8 phrases; §6 elite basket→T7; §7 import→T9. ✓

**Placeholder scan:** every step has concrete code; test helpers described with concrete expectations. No TBD/TODO.

**Type consistency:** `GoalPeriod` (T1) used in T2/T3/T4; `get_goal(chat_id, period)`/`set_goal(...,period)` consistent T1↔T3↔T4; `GoalProgress(amount,currency,actual,forecast,per_day,status,period)` consistent T2↔T3; `forecast_total(daily, total_days, method)` consistent T2↔T4; `render_report_png(...,goal,forecast,tag_totals)` consistent T4↔T5; `FunItem.luxury` T7; `import_records_sync -> (added,skipped)` T9.

**Watch during execution:** T4 needs `AnalyticsService` to know `forecast_method` — add it to the ctor (from `config.analytics.forecast_method`) or read `self._config.forecast_method`; keep GoalService and AnalyticsService using the SAME `forecast_total` helper (import it from `goal_service`) so the chart forecast and the `/goal` forecast never diverge.
