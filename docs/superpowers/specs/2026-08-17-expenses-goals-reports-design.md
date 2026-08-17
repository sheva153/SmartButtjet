# Expenses, Monthly Goals & Richer Reports — Design

**Date:** 2026-08-17
**Branch:** `feature/expenses-goals-reports` (off `origin/main` @ 511f7b6)
**Status:** Approved design → spec review

## Summary

One PR delivering five coupled changes to the SmartButtjet income bot. Expenses
(E) are the foundation — the data model becomes income/expense aware — and the
legend (A), custom periods (C), monthly goal (D), and basket prices (B) build on
top. The user explicitly chose a single PR despite the review-size risk.

The app is currently income-only: `IncomeRecord.amount` is always `> 0`, and the
parser, repository, analytics, and chart have no concept of a record `type`.

## Scope decisions (from brainstorming)

| # | Feature | Decision |
|---|---------|----------|
| E | Expenses | Mark via leading minus **or** keyword; store `type=expense`, `amount>0` |
| A | Legend totals | Diverging chart; legend shows per-currency income/expense totals |
| C | Custom periods | Presets (`last_week/last_month/last_year`) **plus** arbitrary `(start,end)` range |
| D | Monthly goal | Monthly **income** target; pace feedback; after-save line ON by default when a goal is set |
| B | Basket prices | Add mid/high-tier items, bump mid items, drop most cheapest (keep 2-3 for tiny amounts) |

---

## E — Expenses (foundation)

### Data model (`models/domain.py`)
- Add `type: Literal["income", "expense"] = "income"` to both `ParsedIncome` and
  `IncomeRecord`. `amount` stays `> 0`; the sign lives in `type`, not the number.
- Add a public alias `RecordType = Literal["income", "expense"]`.
- Keep the class name `IncomeRecord` (renaming is disproportionate churn); a
  module docstring note records that it now holds income *and* expense rows.

### CSV migration (`repositories/records_repository.py`)
- Records CSV gains a `type` column. `_normalize_records` adds it when absent and
  backfills `income` for legacy rows, flagging `needs_rewrite`. Reuses the
  existing pure-normalize + `migrate_records_sync` machinery from PR #1.
- Backward compatible: old files load; first write persists the new column.

### Parser (`parsers/income_parser.py`, `config.py`)
- A message segment is an **expense** when either:
  - **(a)** the amount carries a leading minus: `-500 таксі`, or
  - **(b)** the text contains an expense marker from config
    `income.expense_markers` (default: `витрата, витратив, витратила, мінус`).
- The minus binds to the **amount only**, never to a date token (`15.10` stays a
  date). Reuses the PR #1 `sole_money_span` / invalid-date disambiguation; the
  sign detection runs on the resolved money span, so date parsing is untouched.
- Default markers are deliberately conservative to avoid false positives like
  "оплатили мені" (income). Extendable via config.

### Analytics (`services/analytics_service.py`)
- New helpers:
  - `totals_by_type(frame) -> dict[str, dict[RecordType, Decimal]]` — per currency,
    income and expense sums.
  - `net(frame) -> dict[str, Decimal]` — per currency income − expense.
- `summary()` renders, per currency: `Дохід X · Витрати Y · Чистими Z`.
- Existing `totals()` / `total()` keep returning gross sums (used elsewhere) but
  callers that must not mix types are audited.

---

## A — Diverging chart + legend totals (`services/report_chart.py`)

- Bars per bucket & currency: **income up**, **expense down** (expense plotted as
  a negative value). Income/expense distinguished by color; a zero baseline line
  is drawn.
- `_aggregate` returns signed per-bucket sums split by `(currency, type)`.
- Legend label per currency shows its totals, e.g. `UAH: +12 000 / −3 000`
  (income / expense). This is the "show the total sum next to the currency" ask.
- Title subtitle: `Дохід 12 000 · Витрати 3 000 · Чистими 9 000 UAH` (per currency,
  ` · ` joined across currencies).
- Value labels on bars keep the space-thousands formatting; negative bars label
  below the bar.

---

## C — Periods: presets + arbitrary range

### Presets (`models/domain.py`, handlers, CLI)
- Extend `Period` with `last_week`, `last_month`, `last_year`.
- `frame()` computes their windows relative to `reference`:
  - `last_week` — the 7-day Mon–Sun block before the current week.
  - `last_month` — the whole previous calendar month.
  - `last_year` — the whole previous calendar year.
- Chart period keyboard gains buttons: **Минулий тиждень / Минулий місяць /
  Минулий рік**. CLI `--period` picks these up automatically via `get_args(Period)`.
- `CHART_PERIODS` extended so the presets render a PNG; bucket labels reuse the
  existing week/day/month bucketers with a shifted reference.

### Arbitrary range
- `frame()` gains optional `date_range: tuple[date, date] | None`; when set it
  overrides period filtering (inclusive start/end).
- `build_chart_artifacts` accepts an optional range and threads it through.
- Entry point: `/chart 01.03 15.03` (or `DD.MM.YYYY`). A small range parser lives
  in the analytics handler / a parser helper; invalid input → friendly error.
- Report bucketing for an arbitrary range: by **day** when span ≤ 62 days, else by
  **month** (single auto rule; no per-user config).

---

## D — Monthly income goal + motivation

### Storage (`models/domain.py`, `repositories/`, `config.py`)
- New model `ChatGoal(chat_id, amount: Decimal>0, currency, updated_by, updated_at)`.
- New CSV `data/goals.csv` (path in `storage` config). Repository gains
  `read_goal_sync` / `list_goals` / `set_goal` (atomic write like other CSVs).

### Commands (`handlers/`)
- `/goal 50000` or `/goal 50000 UAH` — set the chat's monthly income goal.
- `/goal` with no args — show a progress card.

### Pace logic (`services/`)
- `actual` = income total for the current month (matching goal currency, `type=income`).
- `expected` = `goal × (days_elapsed / days_in_month)`.
- If `actual ≥ expected` → "встигаєш, продовжуй" + an *ahead* phrase.
- Else → "пришвидшись" + required `(goal − actual) / days_left` per day + a *behind* phrase.
- If `actual ≥ goal` → *reached* phrase.
- Phrase pools in config `goal_phrases: {ahead: [...], behind: [...], reached: [...]}`.

### Where it shows
- In `/goal` output (the progress card).
- Appended as a short line to the post-save message after an **income** record,
  **on by default whenever a goal is set** (silent when no goal). Expenses do not
  trigger the goal line. Goal progress is income-based, so E and D stay decoupled.

---

## B — Richer basket (`config.yaml`, maybe `models/domain.py`)

- Drop most of the cheapest trinkets (олівці 12, Рачки 12, кулька 15, цукерки 15,
  банани 18…); keep 2-3 low items only for very small incomes.
- Bump mid items (сукні, шкарпетки, etc.) to realistic 2026 prices.
- Add mid/high tier with the same humor: консоль, монітор, велосипед, вечеря в
  ресторані, вихідні-подорож, місяць оренди, пилосос, онлайн-курс, тощо.
- Config-only where possible. If down-ranking cheap items needs code, add an
  optional `weight: int = 1` to `FunItem` + weighted sampling; otherwise skip it
  (YAGNI) and rely on curation.

---

## Testing

- **Parser:** expense via leading minus and via each keyword; dates (`15.10`,
  invalid-month reinterpretation) still parse as income; mixed segments.
- **Analytics:** `totals_by_type`, `net`, summary rendering with mixed types.
- **Chart:** diverging render (income up / expense down), legend totals string,
  arbitrary-range bucketing (day vs month rule), preset windows.
- **Periods:** `last_week/last_month/last_year` window boundaries; CLI choices;
  range parser (valid + invalid input).
- **Goal:** set/read, pace math (ahead/behind/reached, month-edge days), post-save
  line appears only with a goal set and only for income.
- **Migration:** legacy records CSV without `type` loads and backfills `income`.
- **Smoke:** `just analytics <id> last_month`, goal set/show; then `/code-review`
  + `/simplify` + `/just-test` as on prior PRs.

## Non-goals

- No superadmin / secret-session feature (tracked separately).
- No expense categories/budgets beyond the shared category machinery.
- No multi-currency goal (one goal, one currency per chat).
- No renaming of `IncomeRecord`.

## Risks

- Single large PR touching the data model — mitigated by keeping subsystems
  behind clear helpers and a thorough test matrix.
- Parser expense/date/minus ambiguity — mitigated by binding the sign to the
  resolved money span and reusing PR #1 disambiguation, with dedicated tests.
- CSV migration on live data — mitigated by best-effort backfill + existing
  atomic-write machinery.
