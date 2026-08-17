# Expenses, Monthly Goals & Richer Reports — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add expense tracking, a diverging income/expense chart with per-currency totals in the legend, preset + arbitrary reporting periods, a monthly income goal with pace feedback, and a richer fun-basket — all in one PR.

**Architecture:** Records gain a `type` ("income"|"expense") discriminator; `amount` stays `> 0`. The parser flags expenses by a leading minus or a configurable keyword. Analytics splits sums by type; the matplotlib report draws income up / expense down and puts per-currency totals in the legend. A new `ChatGoal` model + `goals.csv` drives a `GoalService` that reports monthly income pace. All new behaviour hangs off small, focused helpers.

**Tech Stack:** Python 3.13, aiogram 3.30, pydantic v2, pandas, matplotlib (Agg), uv, just, pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-08-17-expenses-goals-reports-design.md`

## Global Constraints

- `amount` is always `Decimal > 0`; the income/expense sign lives in `type`, never in the number.
- `IncomeRecord` is NOT renamed (it now holds income and expense rows).
- Reads stay side-effect free; only `migrate_records_sync` / atomic-write paths touch disk.
- Matplotlib uses the OO API (`Figure` + `FigureCanvasAgg`), never `pyplot` — rendering runs in a worker thread.
- All user-facing bot copy is Ukrainian; all code identifiers and any GitHub text are English.
- `Period` choices are the single source of truth via `typing.get_args(Period)` — CLI and keyboards derive from it.
- The PostToolUse ruff hook removes an import added before its first use — add imports together with the code that uses them.
- Run `just test` (full suite) green before each commit that changes behaviour; `just lint` and `just typecheck` before the final commit.

---

### Task 1: Record `type` discriminator on domain models

**Files:**
- Modify: `src/income_stats/models/domain.py`
- Modify: `src/income_stats/models/__init__.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `RecordType = Literal["income", "expense"]`; `ParsedIncome.type: RecordType = "income"`; `IncomeRecord.type: RecordType = "income"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from income_stats.models import IncomeRecord, ParsedIncome, RecordType


def test_parsed_income_defaults_to_income_type():
    parsed = ParsedIncome(amount=Decimal("10"), income_date=date(2026, 8, 17))
    assert parsed.type == "income"


def test_record_accepts_expense_type():
    record = IncomeRecord(
        telegram_message_id=1, chat_id=1, user_id=1, original_text="-10",
        amount=Decimal("10"), currency="UAH", income_date=date(2026, 8, 17),
        updated_by=1, type="expense",
    )
    assert record.type == "expense"


def test_record_rejects_unknown_type():
    with pytest.raises(ValidationError):
        IncomeRecord(
            telegram_message_id=1, chat_id=1, user_id=1, original_text="x",
            amount=Decimal("10"), currency="UAH", income_date=date(2026, 8, 17),
            updated_by=1, type="refund",
        )
```

(Import `Decimal`, `date`, `pytest`, and pydantic's `ValidationError` at the top if not already present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py -k "type" -v`
Expected: FAIL — `RecordType` not importable / `type` field unknown.

- [ ] **Step 3: Add the type field**

In `domain.py`, after `Period` / `CHART_PERIODS`:

```python
RecordType = Literal["income", "expense"]
```

Add to `ParsedIncome` (after `income_date`) and `IncomeRecord` (after `currency`):

```python
    type: RecordType = "income"
```

In `models/__init__.py` add `RecordType` to the import block and `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/income_stats/models/domain.py src/income_stats/models/__init__.py tests/unit/test_models.py
git commit -m "feat: add income/expense type discriminator to records"
```

---

### Task 2: Parser flags expenses (leading minus + keywords)

**Files:**
- Modify: `src/income_stats/config/settings.py` (add `IncomeConfig.expense_markers`)
- Modify: `src/income_stats/parsers/income_parser.py`
- Test: `tests/unit/test_income_parser.py`

**Interfaces:**
- Consumes: `ParsedIncome.type` (Task 1); `IncomeConfig`.
- Produces: `parse_income_message` sets `type="expense"` when a resolved amount carries a leading minus OR the message contains an expense marker; else `"income"`.

**Design note:** A message is treated as an expense as a whole (marker anywhere) OR per-amount when that amount has a leading minus. Minus binds to the amount span only; `MONEY_PATTERN` does not consume a leading `-`, so we detect it by looking at the character immediately before the amount span. Dates keep parsing unchanged (the sign check runs on money spans, never date spans).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_income_parser.py
def test_leading_minus_marks_expense(income_config):
    parsed = parse_income_message("-500 таксі", income_config, today=date(2026, 8, 17))
    assert parsed[0].type == "expense"
    assert parsed[0].amount == Decimal("500.00")


def test_keyword_marks_expense(income_config):
    parsed = parse_income_message("витратив 500 на таксі", income_config, today=date(2026, 8, 17))
    assert parsed[0].type == "expense"


def test_plain_amount_is_income(income_config):
    parsed = parse_income_message("500 зарплата", income_config, today=date(2026, 8, 17))
    assert parsed[0].type == "income"


def test_minus_does_not_break_dates(income_config):
    # 15.10 is a valid date, not an expense of 15.10
    parsed = parse_income_message("зарплата 200 15.10", income_config, today=date(2026, 8, 17))
    assert parsed[0].type == "income"
    assert parsed[0].income_date == date(2026, 10, 15)
```

Reuse the existing `income_config` fixture in this file (or build an `IncomeConfig(...)` inline matching the module's existing tests).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_income_parser.py -k "expense or minus" -v`
Expected: FAIL — `type` always defaults to income.

- [ ] **Step 3: Add config field**

In `settings.py`, `IncomeConfig`:

```python
    expense_markers: list[str] = Field(
        default_factory=lambda: ["витрата", "витратив", "витратила", "мінус"]
    )

    @field_validator("expense_markers", mode="before")
    @classmethod
    def normalize_expense_markers(cls, values: object) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("expense_markers must be a list")
        markers = [str(value).strip().casefold() for value in values if str(value).strip()]
        return list(dict.fromkeys(markers))
```

(Add `Field` / `field_validator` are already imported.)

- [ ] **Step 4: Implement expense detection**

In `income_parser.py`, add a helper and use it in `parse_income_message`:

```python
def _has_expense_marker(text: str, config: IncomeConfig) -> bool:
    lowered = text.casefold()
    return any(
        re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", lowered)
        for marker in config.expense_markers
    )


def _amount_is_negative(text: str, span: tuple[int, int]) -> bool:
    start, _ = span
    prefix = text[:start].rstrip()
    return prefix.endswith("-") or prefix.endswith("−")
```

In `parse_income_message`, after computing `candidates`, compute:

```python
    message_is_expense = _has_expense_marker(text, config)
```

Then in the returned comprehension, set the type per amount:

```python
        ParsedIncome(
            amount=amount,
            currency=_currency_for_match(text, match, config.default_currency),
            categories=categories,
            tags=tags,
            description=description,
            income_date=income_date,
            type=(
                "expense"
                if message_is_expense or _amount_is_negative(text, match.span())
                else "income"
            ),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_income_parser.py -v`
Expected: PASS (all existing parser tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/income_stats/config/settings.py src/income_stats/parsers/income_parser.py tests/unit/test_income_parser.py
git commit -m "feat: parse expenses via leading minus and keywords"
```

---

### Task 3: Repository migration + `type` column

**Files:**
- Modify: `src/income_stats/repositories/records_repository.py`
- Test: `tests/integration/test_csv_repository.py`

**Interfaces:**
- Consumes: `IncomeRecord.type` (Task 1) — `RECORD_COLUMNS` now includes `type`.
- Produces: legacy records CSV without a `type` column loads with `type="income"` backfilled; `create_record_sync` / `update_record_sync` persist `type`.

**Design note:** `RECORD_COLUMNS = list(IncomeRecord.model_fields)` already includes `type` once Task 1 lands, so writing is automatic. Only reading legacy files needs a backfill in `_normalize_records`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_csv_repository.py
def test_reads_legacy_records_without_type_column(tmp_path):
    repo = _repo(tmp_path)  # existing helper in this file
    # write a legacy CSV missing the `type` column
    legacy = repo.records_path
    legacy.parent.mkdir(parents=True, exist_ok=True)
    columns = [c for c in RECORD_COLUMNS if c != "type"]
    frame = pd.DataFrame([{**_legacy_row()}], columns=columns)  # _legacy_row: helper/inline dict
    frame.to_csv(legacy, index=False)

    records = repo.list_records_sync(chat_id=1)
    assert records[0].type == "income"
```

Build `_legacy_row()` inline as a dict of string cells for every column except `type` (mirror an existing row-builder in the test file). Import `RECORD_COLUMNS` and `pandas as pd` at the top.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_csv_repository.py -k legacy_records_without_type -v`
Expected: FAIL — `CSV ... misses columns: ['type']`.

- [ ] **Step 3: Backfill in `_normalize_records`**

In `_normalize_records`, before the `missing = set(RECORD_COLUMNS) - ...` check:

```python
        if "type" not in frame.columns:
            frame["type"] = "income"
            migrated = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_csv_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/income_stats/repositories/records_repository.py tests/integration/test_csv_repository.py
git commit -m "feat: migrate legacy records CSV to include type column"
```

---

### Task 4: IncomeService propagates `type`

**Files:**
- Modify: `src/income_stats/services/income_service.py`
- Test: `tests/unit/test_income_service.py`

**Interfaces:**
- Consumes: `ParsedIncome.type` (Task 2), `IncomeRecord.type` (Task 1).
- Produces: captured `IncomeRecord.type` equals the parsed item's type.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_income_service.py
async def test_capture_preserves_expense_type(income_service):
    records = await income_service.capture(
        text="-500 таксі", telegram_message_id=10, chat_id=1,
        user_id=1, username="u", today=date(2026, 8, 17),
    )
    assert records[0].type == "expense"
```

Reuse the existing `income_service` fixture in this file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_income_service.py -k expense_type -v`
Expected: FAIL — record type is income.

- [ ] **Step 3: Pass the type through**

In `capture`, add `type=item.type,` to the `IncomeRecord(...)` constructor.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_income_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/income_stats/services/income_service.py tests/unit/test_income_service.py
git commit -m "feat: persist expense type on capture"
```

---

### Task 5: Analytics splits sums by type

**Files:**
- Modify: `src/income_stats/services/analytics_service.py`
- Test: `tests/unit/test_analytics_service.py`

**Interfaces:**
- Consumes: `frame` rows now carry a `type` column.
- Produces:
  - `AnalyticsService.totals_by_type(frame) -> dict[str, dict[str, Decimal]]` — `{currency: {"income": D, "expense": D}}`.
  - `AnalyticsService.net(frame) -> dict[str, Decimal]` — `{currency: income - expense}`.
  - `summary()` renders `Дохід X · Витрати Y · Чистими Z` per currency.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_analytics_service.py
def _frame(rows):
    return pd.DataFrame(rows, columns=list(IncomeRecord.model_fields))

def test_totals_by_type_splits_currencies():
    frame = _mixed_frame()  # two UAH incomes + one UAH expense (helper/inline)
    totals = AnalyticsService.totals_by_type(frame)
    assert totals["UAH"]["income"] == Decimal("12000")
    assert totals["UAH"]["expense"] == Decimal("3000")

def test_net_subtracts_expense():
    frame = _mixed_frame()
    assert AnalyticsService.net(frame)["UAH"] == Decimal("9000")

async def test_summary_shows_income_expense_net(analytics_service_with_mixed):
    text = await analytics_service_with_mixed.summary(chat_id=1, period="all")
    assert "Дохід" in text and "Витрати" in text and "Чистими" in text
```

Build helper frames inline mirroring existing tests (each row a full `IncomeRecord.model_dump(mode="python")`, varying `type`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_analytics_service.py -k "by_type or net or income_expense_net" -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement helpers + summary**

Add to `AnalyticsService`:

```python
    @staticmethod
    def totals_by_type(frame: pd.DataFrame) -> dict[str, dict[str, Decimal]]:
        result: dict[str, dict[str, Decimal]] = {}
        if frame.empty:
            return result
        for (currency, kind), rows in frame.groupby(["currency", "type"], sort=True):
            bucket = result.setdefault(str(currency), {"income": Decimal(), "expense": Decimal()})
            bucket[str(kind)] = sum(rows["amount"], start=Decimal())
        return result

    @classmethod
    def net(cls, frame: pd.DataFrame) -> dict[str, Decimal]:
        return {
            currency: kinds["income"] - kinds["expense"]
            for currency, kinds in cls.totals_by_type(frame).items()
        }
```

Rewrite `summary()`'s body (keep the empty-frame guard) to:

```python
        totals = self.totals_by_type(frame)
        lines = [f"Записів: {len(frame)}"]
        for currency, kinds in totals.items():
            net = kinds["income"] - kinds["expense"]
            lines.append(
                f"{currency}: Дохід {kinds['income']:,.2f} · "
                f"Витрати {kinds['expense']:,.2f} · Чистими {net:,.2f}"
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_analytics_service.py -v`
Expected: PASS. Fix any existing summary test that asserted the old wording.

- [ ] **Step 5: Commit**

```bash
git add src/income_stats/services/analytics_service.py tests/unit/test_analytics_service.py
git commit -m "feat: split analytics totals into income, expense, net"
```

---

### Task 6: Diverging chart with per-currency totals in the legend

**Files:**
- Modify: `src/income_stats/services/report_chart.py`
- Modify: `src/income_stats/services/analytics_service.py` (HTML title unchanged; ensure PNG path passes through)
- Test: `tests/unit/test_report_chart.py`

**Interfaces:**
- Consumes: `frame` with `type`; `render_report_png(frame, period, reference, path)` unchanged signature (range added in Task 8).
- Produces: `_aggregate(frame, labels, index_of) -> dict[str, dict[str, list[float]]]` (`{currency: {"income": [...], "expense": [...]}}`); PNG draws income up, expense down; legend label per currency includes totals.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_report_chart.py
def test_aggregate_splits_income_and_expense():
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    from income_stats.services.report_chart import _aggregate, _period_buckets
    labels, index_of = _period_buckets("week", date(2026, 8, 17))
    frame = _week_frame_mixed()  # income Mon 2000, expense Tue 500 (helper)
    series = _aggregate(frame, labels, index_of)
    assert series["UAH"]["income"][0] == 2000.0
    assert series["UAH"]["expense"][1] == 500.0

def test_render_report_png_writes_file(tmp_path):
    path = tmp_path / "chart.png"
    render_report_png(_week_frame_mixed(), "week", date(2026, 8, 17), path)
    assert path.exists() and path.stat().st_size > 0

def test_legend_label_includes_totals():
    from income_stats.services.report_chart import _legend_label
    assert _legend_label("UAH", income=12000.0, expense=3000.0) == "UAH: +12 000 / −3 000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_report_chart.py -k "aggregate_splits or legend_label or writes_file" -v`
Expected: FAIL — `_aggregate` returns flat dict; `_legend_label` missing.

- [ ] **Step 3: Rewrite `_aggregate` and add `_legend_label`**

```python
def _aggregate(
    frame: pd.DataFrame,
    labels: list[str],
    index_of: Callable[[date], int | None],
) -> dict[str, dict[str, list[float]]]:
    series: dict[str, dict[str, list[float]]] = {}
    for income_date, currency, amount, kind in zip(
        frame["income_date"], frame["currency"], frame["amount"], frame["type"],
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


def _legend_label(currency: str, *, income: float, expense: float) -> str:
    return f"{currency}: +{_format_amount(income)} / −{_format_amount(expense)}"
```

- [ ] **Step 4: Rewrite `render_report_png` drawing**

Replace the drawing loop so each currency plots income (positive) and expense (negative, same color, hatched) and the legend label carries totals:

```python
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
        income = series[currency]["income"]
        expense = series[currency]["expense"]
        offsets = [
            position - group_width / 2 + bar_width * (order + 0.5)
            for position in positions
        ]
        income_bars = axes.bar(
            offsets, income, width=bar_width,
            label=_legend_label(currency, income=sum(income), expense=sum(expense)),
        )
        color = income_bars[0].get_facecolor()
        axes.bar(
            offsets, [-value for value in expense], width=bar_width,
            color=color, alpha=0.55, hatch="//",
        )
        for offset, up, down in zip(offsets, income, expense, strict=True):
            if up > 0:
                axes.annotate(_format_amount(up), (offset, up), ha="center",
                              va="bottom", fontsize=8,
                              rotation=90 if len(labels) > 12 else 0)
            if down > 0:
                axes.annotate(_format_amount(down), (offset, -down), ha="center",
                              va="top", fontsize=8,
                              rotation=90 if len(labels) > 12 else 0)

    axes.axhline(0, color="black", linewidth=0.8)
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylabel("Сума")
    axes.margins(y=0.18)
    axes.grid(axis="y", linestyle=":", alpha=0.4)
    subtitle = " · ".join(
        f"{currency} Дохід {_format_amount(sum(series[currency]['income']))}"
        f" · Витрати {_format_amount(sum(series[currency]['expense']))}"
        f" · Чистими {_format_amount(sum(series[currency]['income']) - sum(series[currency]['expense']))}"
        for currency in currencies
    )
    axes.set_title(f"{PERIOD_TITLES[period]}\n{subtitle or '—'}")
    axes.legend(title="Валюта")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_report_chart.py -v`
Expected: PASS. Update the HTML groupby in `analytics_service._write_chart_artifacts` only if a test asserts old behaviour (title still `PERIOD_TITLES.get(...)`); the HTML `daily` groupby must also keep the `type` column — add `color="type"` is out of scope, leave HTML per-day as-is (income+expense summed) but note it in the commit body.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats/services/report_chart.py tests/unit/test_report_chart.py
git commit -m "feat: diverging income/expense chart with per-currency legend totals"
```

---

### Task 7: Preset periods (last_week / last_month / last_year)

**Files:**
- Modify: `src/income_stats/models/domain.py` (extend `Period`, `CHART_PERIODS`)
- Modify: `src/income_stats/services/analytics_service.py` (`frame` windows)
- Modify: `src/income_stats/services/report_chart.py` (`_period_buckets` resolves presets)
- Test: `tests/unit/test_analytics_service.py`, `tests/unit/test_report_chart.py`, `tests/smoke/test_cli.py`

**Interfaces:**
- Consumes: `frame(chat_id, period, today=...)`.
- Produces: `Period` includes `last_week`, `last_month`, `last_year`; `frame` filters their windows; charts render them.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_analytics_service.py
async def test_last_month_window(analytics_service_seeded):
    # seed one record in July and one in August; today=Aug 17
    frame = await analytics_service_seeded.frame(1, "last_month", today=date(2026, 8, 17))
    assert set(frame["income_date"].map(lambda d: d.month)) == {7}

async def test_last_week_window(analytics_service_seeded):
    frame = await analytics_service_seeded.frame(1, "last_week", today=date(2026, 8, 17))
    # Aug 17 2026 is a Monday; last week = Aug 10..Aug 16
    assert frame["income_date"].min() >= date(2026, 8, 10)
    assert frame["income_date"].max() <= date(2026, 8, 16)
```

```python
# tests/smoke/test_cli.py
def test_analytics_accepts_last_month_period():
    assert main(["analytics", "--chat-id", "1", "--period", "last_month"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_analytics_service.py -k "last_" tests/smoke/test_cli.py -k last_month -v`
Expected: FAIL — unsupported period / invalid choice.

- [ ] **Step 3: Extend `Period` and windows**

`domain.py`:

```python
Period = Literal[
    "today", "week", "month", "year", "all",
    "last_week", "last_month", "last_year",
]
CHART_PERIODS: tuple[Period, ...] = (
    "week", "month", "year", "last_week", "last_month", "last_year",
)
```

In `analytics_service.frame`, add branches (use `calendar`; `import calendar` already? add if missing):

```python
        if period == "last_week":
            this_start = current_date - timedelta(days=current_date.weekday())
            start = this_start - timedelta(days=7)
            end = this_start - timedelta(days=1)
            return cast(pd.DataFrame, frame.loc[(dates >= start) & (dates <= end)].copy())
        if period == "last_month":
            first_this = current_date.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            return cast(
                pd.DataFrame,
                frame.loc[dates.map(lambda v: (v.year, v.month) == (last_prev.year, last_prev.month))].copy(),
            )
        if period == "last_year":
            year = current_date.year - 1
            return cast(pd.DataFrame, frame.loc[dates.map(lambda v: v.year == year)].copy())
```

- [ ] **Step 4: Resolve presets in `_period_buckets`**

At the top of `report_chart._period_buckets`, normalize presets to a base period + shifted reference:

```python
    if period == "last_week":
        return _period_buckets("week", reference - timedelta(days=7))
    if period == "last_month":
        first_this = reference.replace(day=1)
        return _period_buckets("month", first_this - timedelta(days=1))
    if period == "last_year":
        return _period_buckets("year", reference.replace(year=reference.year - 1))
```

Add matching `PERIOD_TITLES` entries:

```python
    "last_week": "Звіт за минулий тиждень",
    "last_month": "Звіт за минулий місяць",
    "last_year": "Звіт за минулий рік",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_analytics_service.py tests/unit/test_report_chart.py tests/smoke/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats/models/domain.py src/income_stats/services/analytics_service.py src/income_stats/services/report_chart.py tests/
git commit -m "feat: add last-week/month/year preset periods"
```

---

### Task 8: Arbitrary date range for reports

**Files:**
- Modify: `src/income_stats/services/analytics_service.py` (`frame`, `summary`, `build_chart_artifacts` accept `date_range`)
- Modify: `src/income_stats/services/report_chart.py` (`render_report_png` + `_range_buckets`)
- Modify: `main.py` (analytics `--from` / `--to`)
- Modify: `src/income_stats/handlers/analytics_handler.py` (`/chart DD.MM DD.MM`)
- Create: `src/income_stats/parsers/date_range.py` (`parse_date_range`)
- Test: `tests/unit/test_date_range.py`, `tests/unit/test_report_chart.py`, `tests/handlers/test_analytics_handler.py`

**Interfaces:**
- Produces: `parse_date_range(text, *, today) -> tuple[date, date] | None`; `frame(..., date_range=(start, end))` overrides period; `render_report_png(frame, period, reference, path, date_range=None)`; chart command `/chart 01.03 15.03`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_date_range.py
from income_stats.parsers.date_range import parse_date_range

def test_parses_two_dates():
    assert parse_date_range("01.03 15.03", today=date(2026, 8, 17)) == (date(2026, 3, 1), date(2026, 3, 15))

def test_parses_full_year_dates():
    assert parse_date_range("01.03.2025 15.03.2025", today=date(2026, 8, 17)) == (date(2025, 3, 1), date(2025, 3, 15))

def test_returns_none_for_garbage():
    assert parse_date_range("hello", today=date(2026, 8, 17)) is None

def test_orders_swapped_dates():
    assert parse_date_range("15.03 01.03", today=date(2026, 8, 17)) == (date(2026, 3, 1), date(2026, 3, 15))
```

```python
# tests/unit/test_report_chart.py
def test_range_buckets_by_day_for_short_span():
    from income_stats.services.report_chart import _range_buckets
    labels, index_of = _range_buckets(date(2026, 3, 1), date(2026, 3, 5))
    assert labels == ["01.03", "02.03", "03.03", "04.03", "05.03"]
    assert index_of(date(2026, 3, 3)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_date_range.py tests/unit/test_report_chart.py -k "range" -v`
Expected: FAIL — modules/functions missing.

- [ ] **Step 3: Implement the range parser**

`src/income_stats/parsers/date_range.py`:

```python
"""Parse a two-date reporting range from user text."""

from __future__ import annotations

import re
from datetime import date

_DATE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?!\d)")


def parse_date_range(text: str, *, today: date) -> tuple[date, date] | None:
    matches = _DATE.findall(text)
    if len(matches) < 2:
        return None
    parsed: list[date] = []
    for day, month, year in matches[:2]:
        try:
            parsed.append(date(int(year) if year else today.year, int(month), int(day)))
        except ValueError:
            return None
    start, end = sorted(parsed)
    return start, end
```

Export it from `src/income_stats/parsers/__init__.py`.

- [ ] **Step 4: Add `_range_buckets` and range support to `render_report_png`**

`report_chart.py`:

```python
def _range_buckets(start: date, end: date) -> tuple[list[str], Callable[[date], int | None]]:
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
    labels = [d.strftime("%m.%Y") for d in months]
    lookup = {(d.year, d.month): i for i, d in enumerate(months)}

    def month_index(value: date) -> int | None:
        return lookup.get((value.year, value.month))

    return labels, month_index
```

Change `render_report_png` signature to `def render_report_png(frame, period, reference, path, date_range=None):` and at the top:

```python
    if date_range is not None:
        labels, index_of = _range_buckets(*date_range)
        title = f"Звіт {date_range[0]:%d.%m.%Y}–{date_range[1]:%d.%m.%Y}"
    else:
        labels, index_of = _period_buckets(period, reference)
        title = PERIOD_TITLES[period]
```

Use `title` in `set_title` instead of `PERIOD_TITLES[period]`.

- [ ] **Step 5: Thread `date_range` through analytics + CLI + handler**

- `analytics_service.frame(..., date_range=None)`: if `date_range`, `start, end = date_range; return frame.loc[(dates >= start) & (dates <= end)].copy()` (before the period switch).
- `summary(..., date_range=None)` and `build_chart_artifacts(..., date_range=None)` accept and forward it; `_write_chart_artifacts` forwards to `render_report_png`.
- `main.py`: add `analytics.add_argument("--from", dest="date_from")` and `--to`; build a `date_range` when both present and pass to `summary`.
- `analytics_handler.chart_handler`: if the command text has args, `parse_date_range(...)`; on success call `send_chart(message, service, date_range=range_)`, else show the period keyboard. Add `date_range` param to `send_chart` and `build_chart_artifacts`.

- [ ] **Step 6: Write handler test + run all**

```python
# tests/handlers/test_analytics_handler.py
async def test_chart_command_with_range_builds_chart(...):
    # message.text = "/chart 01.08 15.08"; assert send_chart called with date_range
```

Run: `uv run pytest tests/unit/test_date_range.py tests/unit/test_report_chart.py tests/handlers/test_analytics_handler.py tests/smoke/test_cli.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/income_stats tests main.py
git commit -m "feat: arbitrary date-range reports via /chart and CLI"
```

---

### Task 9: ChatGoal model + goals repository

**Files:**
- Modify: `src/income_stats/models/domain.py` (`ChatGoal`), `src/income_stats/models/__init__.py`
- Modify: `src/income_stats/config/settings.py` (`StorageConfig.goals_file`)
- Modify: `src/income_stats/repositories/records_repository.py` (goal read/write) + Protocol
- Test: `tests/integration/test_csv_repository.py`, `tests/unit/test_models.py`

**Interfaces:**
- Produces: `ChatGoal(chat_id, amount: Decimal>0, currency, updated_by, updated_at)`; `set_goal_sync(chat_id, amount, currency, updated_by) -> ChatGoal`; `get_goal_sync(chat_id) -> ChatGoal | None`; async wrappers `set_goal` / `get_goal`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_csv_repository.py
def test_set_and_get_goal(tmp_path):
    repo = _repo(tmp_path)
    repo.set_goal_sync(chat_id=1, amount=Decimal("50000"), currency="UAH", updated_by=7)
    goal = repo.get_goal_sync(1)
    assert goal.amount == Decimal("50000") and goal.currency == "UAH"

def test_set_goal_overwrites(tmp_path):
    repo = _repo(tmp_path)
    repo.set_goal_sync(1, Decimal("100"), "UAH", 7)
    repo.set_goal_sync(1, Decimal("200"), "UAH", 7)
    assert repo.get_goal_sync(1).amount == Decimal("200")
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/integration/test_csv_repository.py -k goal -v`
Expected: FAIL.

- [ ] **Step 3: Add model + config + columns**

`domain.py`:

```python
class ChatGoal(BaseModel):
    chat_id: int
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    updated_by: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return _normalize_currency(value)
```

Export `ChatGoal` in `models/__init__.py`. `StorageConfig`: `goals_file: Path = Path("data/goals.csv")`.

- [ ] **Step 4: Add repository methods (mirror chat-settings pattern)**

In `records_repository.py`: `GOAL_COLUMNS = list(ChatGoal.model_fields)`; store `self.goals_path = config.goals_file` in `__init__`; add `get_goal_sync` / `set_goal_sync` mirroring `get_chat_setting_sync` / `set_chat_enabled_sync` (read `_read(self.goals_path, GOAL_COLUMNS)`, upsert by `chat_id`, atomic write). Add async `get_goal` / `set_goal` and extend the `RecordsRepository` Protocol.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/integration/test_csv_repository.py -k goal tests/unit/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats tests
git commit -m "feat: persist per-chat monthly income goal"
```

---

### Task 10: Goal pace service

**Files:**
- Create: `src/income_stats/services/goal_service.py`
- Modify: `src/income_stats/models/domain.py` (`GoalConfig`), `src/income_stats/config/settings.py` (`AppConfig.goals`)
- Modify: `src/income_stats/services/__init__.py`
- Test: `tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: repository `get_goal`, `AnalyticsService.frame` + `totals_by_type`, `GoalConfig` phrases.
- Produces: `GoalProgress(goal, actual, expected, per_day_needed, status)` where `status ∈ {"ahead","behind","reached"}`; `GoalService.progress(chat_id, *, today) -> GoalProgress | None`; `GoalService.render(progress) -> str`; `GoalService.after_save_line(chat_id, *, today) -> str` (empty when no goal or disabled).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_goal_service.py
async def test_progress_ahead(goal_service_seeded):
    # goal 30000 UAH; income-to-date 20000 on day 10 of a 31-day month → expected ~9677 → ahead
    progress = await goal_service_seeded.progress(1, today=date(2026, 8, 10))
    assert progress.status == "ahead"

async def test_progress_behind(goal_service_low_income):
    progress = await goal_service_low_income.progress(1, today=date(2026, 8, 20))
    assert progress.status == "behind"
    assert progress.per_day_needed > 0

async def test_reached(goal_service_reached):
    progress = await goal_service_reached.progress(1, today=date(2026, 8, 20))
    assert progress.status == "reached"

async def test_after_save_line_empty_without_goal(goal_service_no_goal):
    assert await goal_service_no_goal.after_save_line(1, today=date(2026, 8, 20)) == ""
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/unit/test_goal_service.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Add `GoalConfig`**

`domain.py`:

```python
class GoalConfig(BaseModel):
    enabled: bool = True
    after_save_line: bool = True
    ahead_phrases: list[str] = Field(default_factory=lambda: ["Так тримати! Ти випереджаєш темп 🚀"])
    behind_phrases: list[str] = Field(default_factory=lambda: ["Час пришвидшитись — ще все встигаєш 💪"])
    reached_phrases: list[str] = Field(default_factory=lambda: ["Ціль досягнута! Ти неймовірна 🎉"])
```

Export it; add `goals: GoalConfig = Field(default_factory=GoalConfig)` to `AppConfig`.

- [ ] **Step 4: Implement `GoalService`**

```python
"""Monthly income goal pacing."""

import calendar
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from income_stats.models import GoalConfig
from income_stats.repositories import RecordsRepository
from income_stats.services.analytics_service import AnalyticsService


@dataclass(frozen=True)
class GoalProgress:
    amount: Decimal
    currency: str
    actual: Decimal
    expected: Decimal
    per_day_needed: Decimal
    status: str  # "ahead" | "behind" | "reached"


class GoalService:
    def __init__(self, repository: RecordsRepository, analytics: AnalyticsService,
                 config: GoalConfig) -> None:
        self._repository = repository
        self._analytics = analytics
        self._config = config

    async def progress(self, chat_id: int, *, today: date) -> GoalProgress | None:
        goal = await self._repository.get_goal(chat_id)
        if goal is None:
            return None
        frame = await self._analytics.frame(chat_id, "month", today=today)
        totals = self._analytics.totals_by_type(frame)
        actual = totals.get(goal.currency, {}).get("income", Decimal())
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        expected = goal.amount * Decimal(today.day) / Decimal(days_in_month)
        days_left = max(1, days_in_month - today.day)
        remaining = max(Decimal(), goal.amount - actual)
        per_day = remaining / Decimal(days_left)
        if actual >= goal.amount:
            status = "reached"
        elif actual >= expected:
            status = "ahead"
        else:
            status = "behind"
        return GoalProgress(goal.amount, goal.currency, actual, expected, per_day, status)

    def render(self, progress: GoalProgress) -> str:
        pct = (progress.actual / progress.amount * 100) if progress.amount else Decimal()
        phrase = self._phrase(progress.status)
        lines = [
            f"🎯 Ціль: {progress.amount:,.0f} {progress.currency}/місяць",
            f"Виконано: {progress.actual:,.0f} ({pct:.0f}%)",
        ]
        if progress.status == "behind":
            lines.append(f"Треба ~{progress.per_day_needed:,.0f} {progress.currency}/день")
        lines.append(phrase)
        return "\n".join(lines)

    async def after_save_line(self, chat_id: int, *, today: date) -> str:
        if not (self._config.enabled and self._config.after_save_line):
            return ""
        progress = await self.progress(chat_id, today=today)
        return self._phrase(progress.status) if progress else ""

    def _phrase(self, status: str) -> str:
        pool = {
            "ahead": self._config.ahead_phrases,
            "behind": self._config.behind_phrases,
            "reached": self._config.reached_phrases,
        }[status]
        return random.SystemRandom().choice(pool) if pool else ""
```

Export `GoalService` / `GoalProgress` from `services/__init__.py`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/unit/test_goal_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats tests/unit/test_goal_service.py
git commit -m "feat: monthly income goal pacing service"
```

---

### Task 11: `/goal` command + after-save goal line

**Files:**
- Create: `src/income_stats/handlers/goal_handler.py`
- Modify: `src/income_stats/handlers/__init__.py` (register router)
- Modify: `src/income_stats/bot/application.py` (build `GoalService`, inject into dispatcher)
- Modify: `src/income_stats/handlers/income_handler.py` (append goal line after income saves)
- Test: `tests/handlers/test_goal_handler.py`, `tests/handlers/test_income_handler.py`

**Interfaces:**
- Consumes: `GoalService`, repository `set_goal`.
- Produces: `/goal <amount> [CUR]` sets goal; `/goal` shows progress; income replies gain a goal line when a goal is set and the record `type == "income"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/handlers/test_goal_handler.py
async def test_goal_set_stores_amount(...):
    # message.text = "/goal 50000"; assert repository.set_goal called with 50000
async def test_goal_show_renders_progress(...):
    # goal exists; message.text = "/goal"; assert answer contains "Ціль"
```

```python
# tests/handlers/test_income_handler.py
async def test_income_reply_appends_goal_line_when_goal_set(...):
    # goal set; income "500 зарплата"; assert reply text contains goal phrase
async def test_expense_reply_has_no_goal_line(...):
    # expense "-500 таксі"; assert no goal phrase in reply
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/handlers/test_goal_handler.py tests/handlers/test_income_handler.py -k "goal or expense_reply" -v`
Expected: FAIL — handler missing / no goal line.

- [ ] **Step 3: Implement `goal_handler.py`**

```python
"""/goal command: set or show the monthly income goal."""

from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.repositories import RecordsRepository
from income_stats.services import GoalService

goal_router = Router(name="goal")


@goal_router.message(Command("goal"))
async def goal_handler(message: Message, command: CommandObject,
                       repository: RecordsRepository, goal_service: GoalService,
                       app_config: AppConfig) -> None:
    today = datetime.now(ZoneInfo(app_config.bot.timezone)).date()
    if command.args:
        parts = command.args.split()
        try:
            amount = Decimal(parts[0].replace(",", "."))
        except (InvalidOperation, IndexError):
            await message.answer("Формат: /goal 50000 [UAH]")
            return
        currency = parts[1].upper() if len(parts) > 1 else app_config.income.default_currency
        if amount <= 0:
            await message.answer("Ціль має бути більшою за нуль.")
            return
        user = message.from_user
        await repository.set_goal(message.chat.id, amount, currency,
                                  user.id if user else 0)
        await message.answer(f"🎯 Ціль встановлено: {amount:,.0f} {currency}/місяць")
        return
    progress = await goal_service.progress(message.chat.id, today=today)
    if progress is None:
        await message.answer("Ціль ще не задана. Встанови: /goal 50000")
        return
    await message.answer(goal_service.render(progress))
```

Register `goal_router` in `handlers/__init__.py` and in `application.py`'s dispatcher include list; build `GoalService(repository, analytics_service, config.goals)` and pass it as a workflow-data kwarg (mirror how `analytics_service` is injected).

- [ ] **Step 4: Append goal line in `income_handler`**

Inject `goal_service: GoalService` into `income_message_handler`. After building `records`, compute once:

```python
    goal_line = await goal_service.after_save_line(message.chat.id, today=today)
```

(where `today` is the same date already computed for capture). In `deliver_reply`, append the line only for income records:

```python
    async def deliver_reply(record: IncomeRecord) -> None:
        extra = goal_line if record.type == "income" else ""
        body = format_success(record, analytics_service.fun_summary(record))
        if extra:
            body = f"{body}\n\n{extra}"
        await message.reply(body, reply_markup=success_keyboard(record))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/handlers/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats tests/handlers
git commit -m "feat: /goal command and after-save goal feedback line"
```

---

### Task 12: Richer fun-basket (mid/high tier, drop cheapest)

**Files:**
- Modify: `config.yaml` (`fun_summary.items`)
- Test: `tests/unit/test_analytics_service.py` (fun-summary already tested) + a config-load assertion

**Interfaces:**
- Consumes: existing `FunItem` / `build_fun_summary`.
- Produces: curated item list — most cheapest removed, mid items repriced, mid/high tier added.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config.py  (create if absent)
def test_basket_has_no_ultra_cheap_items(tmp_path):
    config = load_config(Path("config.yaml"))
    prices = [item.price_uah for item in config.fun_summary.items.values()]
    assert min(prices) >= 25  # cheapest trinkets removed
    assert max(prices) >= 20000  # at least one high-tier item present
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — pencils (12) still present.

- [ ] **Step 3: Curate `config.yaml`**

Remove: `candy(15)`, `rachky(12)`, `banana(18)`, `cookie(20)`, `brick(20)`, `pencil(12)`, `balloon(15)`. Keep 2-3 low items (`water(25)`, `cucumber(25)`, `toilet_paper(25)`). Bump mid items (`skirt` 800→1200, `socks` 120→220, `headphones` 1000→1800, `keyboard` 1200→2200). Add high tier, e.g.:

```yaml
    console: {label: "ігрових консолей", emoji: "🎮", price_uah: 22000, fractional: true}
    monitor: {label: "моніторів", emoji: "🖥️", price_uah: 9000, fractional: true}
    bike: {label: "велосипедів", emoji: "🚲", price_uah: 12000, fractional: true}
    dinner: {label: "вечер у ресторані", emoji: "🍽️", price_uah: 900}
    weekend: {label: "вихідних-подорожей", emoji: "🧳", price_uah: 8000, fractional: true}
    rent: {label: "місяців оренди", emoji: "🏠", price_uah: 15000, fractional: true}
    vacuum: {label: "пилососів", emoji: "🧹", price_uah: 6000, fractional: true}
    course: {label: "онлайн-курсів", emoji: "🎓", price_uah: 5000, fractional: true}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_analytics_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/unit/test_config.py
git commit -m "feat: richer mid/high-tier fun-basket"
```

---

### Task 13: Wiring, config, keyboard, full verification

**Files:**
- Modify: `config.yaml` (`income.expense_markers`, `storage.goals_file`, `goals:` block)
- Modify: `src/income_stats/bot/ui.py` (`chart_period_keyboard` adds preset buttons)
- Modify: `src/income_stats/bot/application.py` (already builds GoalService in Task 11; confirm `migrate_records_sync` still runs)
- Test: `tests/handlers/test_analytics_handler.py`, full suite

**Interfaces:**
- Consumes: everything above.
- Produces: preset buttons in the chart keyboard; config carries new blocks; whole suite + lint + typecheck green.

- [ ] **Step 1: Add preset buttons test**

```python
# tests/handlers/test_analytics_handler.py
def test_chart_keyboard_has_preset_buttons():
    markup = chart_period_keyboard()
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "📅 Тиждень" in labels
    assert any("минулий" in label.lower() for label in labels)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/handlers/test_analytics_handler.py -k preset -v`
Expected: FAIL.

- [ ] **Step 3: Extend the keyboard**

```python
def chart_period_keyboard():
    builder = InlineKeyboardBuilder()
    rows = (
        ("📅 Тиждень", "week"), ("🗓 Місяць", "month"), ("📆 Рік", "year"),
        ("📅 Мин. тиждень", "last_week"), ("🗓 Мин. місяць", "last_month"),
        ("📆 Мин. рік", "last_year"),
    )
    for label, period in rows:
        builder.button(text=label, callback_data=ChartPeriod(period=period))
    builder.adjust(3, 3)
    return builder.as_markup()
```

- [ ] **Step 4: Add config blocks**

`config.yaml`:

```yaml
income:
  # ...existing...
  expense_markers: [витрата, витратив, витратила, мінус]

storage:
  # ...existing...
  goals_file: data/goals.csv

goals:
  enabled: true
  after_save_line: true
  ahead_phrases:
    - "Так тримати! Ти випереджаєш темп 🚀"
    - "Красиво йдеш — ціль уже близько ✨"
  behind_phrases:
    - "Час пришвидшитись — ще все встигаєш 💪"
    - "Трохи відстаєш, але це поправно 🔧"
  reached_phrases:
    - "Ціль досягнута! Ти неймовірна 🎉"
    - "Місячна планка взята — вітаю! 🏆"
```

- [ ] **Step 5: Full verification**

Run:
```bash
uv run pytest -q
just lint
just typecheck
```
Expected: all green. Fix fallout (e.g. any test asserting the old summary wording, old keyboard arity, or `_aggregate` shape).

- [ ] **Step 6: Smoke via just**

```bash
uv run python main.py check-config
uv run python main.py analytics --chat-id 1 --period last_month
```
Expected: exit 0, no traceback.

- [ ] **Step 7: Commit**

```bash
git add config.yaml src/income_stats tests
git commit -m "feat: wire goals, expense markers, preset chart buttons"
```

---

## Self-Review

**Spec coverage:**
- E Expenses → Tasks 1-5 (model, parser, migration, service, analytics). ✓
- A Diverging chart + legend totals → Task 6. ✓
- C Presets + arbitrary range → Tasks 7-8. ✓
- D Monthly goal + pace + after-save line → Tasks 9-11. ✓
- B Richer basket → Task 12. ✓
- Wiring/config/keyboard → Task 13. ✓

**Placeholder scan:** No TBD/TODO; every code step carries real code. Test helper frames are described with concrete column/value expectations.

**Type consistency:** `RecordType` (Task 1) used in Tasks 2/5/6/11; `totals_by_type` shape `{currency: {"income","expense"}}` consistent across Tasks 5/6/10; `date_range: tuple[date, date]` consistent across Task 8 (parser, frame, render_report_png); `GoalProgress.status ∈ {ahead,behind,reached}` consistent across Tasks 10/11.

**Open risk to watch during execution:** the HTML per-day chart in `_write_chart_artifacts` currently sums income+expense without a type split (Task 6 leaves it as-is). If the HTML must also diverge, that is a follow-up — flagged, not silently dropped.
