# Yearly Goals, Forecasting, Tag Reporting & CSV Import — Design

**Date:** 2026-08-17
**Branch:** `feature/goals-forecast-tags-import` (off `origin/main` @ 94b0739, after PR #3)
**Status:** Approved design → spec review

## Summary

Seven coupled features that build on the expenses/goals/reports foundation shipped
in PR #3: monthly **and** yearly income goals, a configurable end-of-period
forecast, the goal + forecast drawn on the chart, a tag breakdown on the chart
with an expanded tag taxonomy, forecast-tiered motivation phrases, a pricier
"elite" fun-basket with a luxury segment, and a `/import` command for bulk CSV
record import. The user chose one PR.

Foundation already in place (PR #3): `IncomeRecord.type` (income/expense),
`ChatGoal(chat_id, amount, currency, updated_by, updated_at)` + `goals.csv`,
`GoalService` (monthly pace), `AnalyticsService.totals_by_type`/`net`/`tag_breakdown`,
diverging matplotlib PNG (`report_chart.py`), `/goal` handler, `/export`.

## Global constraints

- `amount` is always `Decimal > 0`; income/expense sign lives in `type`.
- Matplotlib OO API only (`Figure` + `FigureCanvasAgg`), never `pyplot`.
- Every commit passes the full CI gate `just check` (`ruff format --check` +
  `ruff check` + `pyright` + `pytest`) with **0 warnings** — not just `just test`.
- `Period` stays the single source of truth via `typing.get_args`.
- Bot copy Ukrainian; code identifiers and GitHub text English.
- The PostToolUse ruff hook strips an import added before its first use.

---

## 1. Monthly + yearly goals

### Model (`models/domain.py`)
- Add `GoalPeriod = Literal["month", "year"]`.
- `ChatGoal` gains `period: GoalPeriod = "month"`. Goals are keyed by
  `(chat_id, period)`.

### Repository (`repositories/records_repository.py`)
- `GOAL_COLUMNS` picks up `period` automatically (`list(ChatGoal.model_fields)`).
- `get_goal_sync(chat_id, period)` / `set_goal_sync(chat_id, amount, currency,
  updated_by, period)`; async wrappers gain the `period` argument; upsert key
  becomes `(chat_id, period)`. Protocol updated.
- Legacy `goals.csv` without a `period` column backfills `period="month"` on read
  (side-effect-free), mirroring the `type` backfill for records.

### Command (`handlers/goal_handler.py`)
- `/goal <period> <amount> [CUR]` — `period ∈ {month, year}` (also Ukrainian
  aliases `місяць`→month, `рік`→year). Sets that period's goal.
- `/goal <amount>` (no period) — backward compatible, sets the **month** goal.
- `/goal` (no args) — shows both goals with progress + forecast (see §2).
- Invalid period → usage hint.

## 2. Configurable forecast

### Config (`config/settings.py`, `models/domain.py`)
- `AnalyticsConfig.forecast_method: Literal["linear", "average", "weighted"] = "weighted"`.

### Service (`services/goal_service.py`)
- `GoalProgress` gains `forecast: Decimal` (projected end-of-period total, income,
  goal currency) and `period: GoalPeriod`.
- New pure helper `forecast_total(daily_income, elapsed_days, total_days, method)`:
  - `linear` — `actual * total_days / max(1, elapsed_days)`.
  - `average` — `mean(daily) * total_days` (mean over elapsed days incl. zeros).
  - `weighted` — linearly time-weighted daily mean (day *i* weight *i*), so recent
    days count more, times `total_days`. Deterministic; documented formula.
- `progress(chat_id, *, today, period)` computes actual (period income for the
  goal currency), elapsed/total days for that period (`month` via
  `calendar.monthrange`; `year` via 365/366), and the forecast.
- Status is **forecast-driven**: `reached` if `actual >= goal`; else `on_track`
  if `forecast >= goal`; else `off_track`.

## 3. Goal + forecast on the chart (`services/report_chart.py`)

- `render_report_png` gains an optional `goal: Decimal | None` and
  `forecast: Decimal | None` (single-currency period charts only).
- When `goal` is set: draw a horizontal **goal line** (`axhline`, labeled
  `🎯 Ціль X`) across the income side.
- When `forecast` is set: draw a dashed **forecast marker** line labeled
  `Прогноз ~Y`.
- `AnalyticsService.build_chart_artifacts` looks up the chat's goal for the
  resolved period (month/year presets → that period's goal) and computes the
  forecast, threading both into the render. Non-period/range charts pass `None`.

## 4. Tag breakdown on the chart + expanded tags

### Tag taxonomy (`config.yaml`)
Expand `income.tags` (multi-tag per record already supported by the parser's
`_detect_labels` + `IncomeRecord.tags: list[str]`):
- Payment: `card` (картка/карта/на карту/на картку/card), `cash` (готівка/готівкою/кеш/cash)
- Purpose: `rent` (оренда/квартплата), `utilities` (комуналка/комунальні),
  `dentistry` (стоматологія/стомат/зуби), `health` (медицина/ліки/аптека/лікар),
  `groceries` (продукти), `transport` (транспорт/таксі/пальне),
  `cafe` (кафе/ресторан), `subscriptions` (підписка/підписки), `education` (курс/навчання)

### Rendering (`services/report_chart.py`)
- New `render_tag_breakdown` section drawn **below** the main axes (a second
  subplot / horizontal bars): one bar per tag present, **distinct color per tag**,
  label `{emoji} {tag} {amount}`. Amounts come from `AnalyticsService.tag_breakdown`
  (a record with N tags contributes its amount to each of its N tags — documented,
  so per-tag sums can exceed the grand total).
- A small tag→emoji/label map lives in `report_chart.py` (fallback: the raw tag).
- If no tags present in the frame, the section is omitted (no empty axes → no
  matplotlib warning; guard like the legend guard from PR #3).

## 5. Forecast-tiered phrases (`services/goal_service.py`, `models/domain.py`)

- `GoalConfig` phrase pools stay three, now driven by forecast status:
  - `reached_phrases` — already at/over goal.
  - `ahead_phrases` — `on_track` (forecast ≥ goal): normal encouragement.
  - `behind_phrases` — `off_track` (forecast < goal): **stronger** "push harder"
    wording (config seeded accordingly).
- `render()` shows the goal, actual, %, forecast line, and the tier phrase.
- `after_save_line` unchanged in gating (income only, on by default) but now
  reflects the forecast tier.

## 6. Elite fun-basket (`config.yaml`, `models/domain.py`, `services/analytics_service.py`)

- Raise the price of **all** existing items.
- Add a **luxury tier**: e.g. Rolex, авто, MacBook Pro, квартира, відпустка за
  кордоном, бізнес-клас переліт.
- `FunItem` gains `luxury: bool = False`. In `build_fun_summary`, a luxury item is
  included only when `amount >= 0.5 * price_uah`, and its quantity is displayed as
  **`0.5`** (when `0.5*price ≤ amount < price`) or **`1`** (when `amount ≥ price`)
  — never more, never a tiny fraction. Non-luxury items keep the existing
  integer/`fractional` behavior.

## 7. `/import` CSV bulk import

### Repository (`repositories/records_repository.py`)
- `import_records_sync(records: list[IncomeRecord]) -> tuple[int, int]` — appends
  validated records atomically, deduping by `(chat_id, telegram_message_id,
  source_index)` against the existing store; returns `(added, skipped)`.

### Handler (`handlers/` — new `import_handler.py`)
- `/import` sent as the **caption of a CSV document** (admin-only, like `/export`).
- Downloads the document, reads it as the `records.csv` schema (`categories`/`tags`
  JSON lists; missing `type` → `income`). Each row is validated into an
  `IncomeRecord` with a fresh `id`, `chat_id` overridden to the **importing chat**,
  `updated_by` = the importer. Invalid rows are skipped and counted.
- Reply: `Імпортовано N, пропущено M.`
- Non-CSV / no-document `/import` → usage hint.

## Testing

- **Goals:** period-keyed set/get, `/goal month|year`, backward-compat `/goal N`,
  `/goal` shows both, legacy goals.csv backfill.
- **Forecast:** each of linear/average/weighted with a known daily series;
  status transitions reached/on_track/off_track.
- **Chart:** goal line + forecast marker present; tag breakdown bars per tag with
  distinct colors; no-tag frame omits the section (0 warnings).
- **Phrases:** tier chosen by forecast status.
- **Basket:** luxury 0.5/1 display rule (below 0.5×price omitted; 0.5; 1; capped);
  existing items repriced; config loads.
- **Import:** valid CSV added, invalid rows skipped, dedupe, chat override,
  admin-only.
- **Smoke:** `just parse`, `just analytics <id> month/year`, config load; then
  `/code-review` + `/simplify` + `/just-test`.

## Non-goals

- No per-category goals (only income, per period).
- No forecast confidence intervals — a single projected number.
- No `/import` from a URL or other formats (CSV document only).
- No change to the expense/parser semantics from PR #3 (Ruling A stands).

## Risks

- Multi-tag sums exceed the grand total by construction — documented in the chart
  label so it does not read as a bug.
- `weighted` forecast on very sparse data can swing; deterministic and bounded by
  the same total-days multiplier, and covered by tests.
- CSV import trusts row content; mitigated by per-row pydantic validation,
  chat-id override, and dedupe (no cross-chat leakage, no duplicates).
