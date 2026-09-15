# Tag-Mix Chart Redesign + /tags Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Redesign the report chart so tags are shown *inside* the bars (colour = tag, multi-tag = clear colour blocks), currency = hatch pattern, bar height = UAH-equivalent (USD/EUR converted for height only), income up / expense down stacked; a grouped legend that sums records **by tag-mix combination** plus income/expense/net and per-currency totals; month charts label the x-axis with day-number + weekday. Add a `/tags` command to list and add tags/aliases at runtime.

**Architecture:** A design-validated reference implementation of the new chart lives at `docs/superpowers/reference/2026-08-17-tag-mix-chart-demo.py` — the renderer is a near-direct port of it into `report_chart.py`. FX rates come from config (`analytics.fx_to_uah`). Runtime tags persist in a new `tags.csv` via the repository and are merged with the static `income.tags` config when the parser detects tags.

**Tech Stack:** Python 3.13, aiogram 3.30, pydantic v2, pandas, matplotlib (Agg OO API), uv, just, pytest, ruff, pyright.

**Reference:** `docs/superpowers/reference/2026-08-17-tag-mix-chart-demo.py` (validated visual design — port its render/legend/mix logic).

## Global Constraints

- Matplotlib OO API only (`Figure` + `FigureCanvasAgg`), never `pyplot`.
- **No emoji in matplotlib text** (DejaVu Sans renders them as tofu) and **no `warnings.filterwarnings` suppression** — fix the text, never silence.
- Height uses UAH-equivalent (`amount × fx_to_uah[currency]`); currency identity stays visible via hatch. FX is for height/aggregation only.
- Every commit passes `just check` (`ruff format --check` + `ruff check` + `pyright` + `pytest`) with **0 warnings**.
- Reads side-effect free; only migrate/atomic-write paths touch disk.
- Bot copy Ukrainian; code identifiers and GitHub text English.
- The PostToolUse ruff hook strips an import added before its first use.

---

### Task 1: FX config + `to_uah` helper

**Files:**
- Modify: `src/income_stats/config/settings.py` (`AnalyticsConfig.fx_to_uah`)
- Modify: `src/income_stats/services/report_chart.py` (add `to_uah`)
- Test: `tests/unit/test_report_chart.py`

**Interfaces:**
- Produces: `AnalyticsConfig.fx_to_uah: dict[str, float]` default `{"USD": 41.0, "EUR": 45.0}` (UAH implicitly 1.0); `to_uah(amount: Decimal, currency: str, fx: dict[str, float]) -> Decimal` returning `amount * Decimal(str(fx.get(currency, 1)))` (UAH and unknown → ×1).

- [ ] **Step 1: Failing test**
```python
# tests/unit/test_report_chart.py
def test_to_uah_converts_by_rate():
    from income_stats.services.report_chart import to_uah

    assert to_uah(Decimal("100"), "USD", {"USD": 41.0}) == Decimal("4100")
    assert to_uah(Decimal("100"), "UAH", {"USD": 41.0}) == Decimal("100")
    assert to_uah(Decimal("100"), "GBP", {"USD": 41.0}) == Decimal("100")
```
- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/unit/test_report_chart.py -k to_uah -v`.
- [ ] **Step 3: Add config field**
`settings.py` `AnalyticsConfig`:
```python
    fx_to_uah: dict[str, float] = Field(default_factory=lambda: {"USD": 41.0, "EUR": 45.0})
```
- [ ] **Step 4: Add helper** in `report_chart.py`:
```python
def to_uah(amount: Decimal, currency: str, fx: dict[str, float]) -> Decimal:
    return amount * Decimal(str(fx.get(currency, 1)))
```
- [ ] **Step 5: Run pass; Step 6: `just check` + commit** `feat: FX-to-UAH config and helper for chart height`.

---

### Task 2: New tag-mix chart renderer

**Files:**
- Modify: `src/income_stats/services/report_chart.py`
- Test: `tests/unit/test_report_chart.py`

**Interfaces:**
- Consumes: `to_uah` (Task 1); a per-record frame with columns `income_date, amount, currency, tags, type`.
- Produces: `render_report_png(frame, period, reference, path, *, date_range=None, fx, goal=None, forecast=None)` drawing the new design; helpers `_mix_cmap(tags)`, `MixHandler`, `_tag_label`, `_bucket_index` for period (week/month/year/range), month/last_month labels as `"{day}\n{weekday}"`.

**Design (port from the reference file):**
- Bucket records by period (week=Пн–Нд, month=days 1..N with weekday two-line labels, year=months, range=by day/month). For each record: height = `float(to_uah(amount, currency, fx))`, signed (income +, expense −); draw a horizontal gradient image (`imshow`) using `_mix_cmap(tags)` — **flat colour blocks with short blends** so 2–3 tags stay readable (exact `_cmap_for` from the reference); overlay a `Rectangle(fill=False, hatch=CURRENCY_HATCH[currency])`; stack per (bucket, income|expense) via a running base.
- `TAG_COLOR` / `TAG_LABELS` / `CURRENCY_HATCH` / `NO_TAG` maps and `MixHandler` (legend key drawing side-by-side colour blocks): copy from the reference.
- Legend groups (each via `axes.legend(...)` + `axes.add_artist`, collected and passed to `savefig(bbox_inches="tight", bbox_extra_artists=legends)` — do NOT call `tight_layout`):
  - **Разом (₴-екв):** Дохід / Витрати / Чистими.
  - **Мікси тегів (сума ₴-екв):** one row per unique tag-combination (records grouped by `tuple(sorted(tags, key=_ORDER))`), summed, biggest first, with a `MixHandler` multi-colour swatch. Untagged → "Без тегу".
  - **Валюта (₴-екв):** per-currency ₴-equiv totals with the currency hatch swatch.
- If `goal`/`forecast` given (single-currency period), overlay `axhline` at those ₴-equiv values labelled "Ціль …" / "Прогноз ~…" (plain text, no emoji).
- Empty frame → guard: no bars, no legend crash.

- [ ] **Step 1: Failing tests**
```python
def test_render_tag_mix_writes_png(tmp_path):
    path = tmp_path / "c.png"
    render_report_png(
        _mixed_frame(), "week", date(2026, 8, 17), path, fx={"USD": 41.0, "EUR": 45.0}
    )
    assert path.exists() and path.stat().st_size > 0


def test_mix_cmap_multi_tag_has_flat_blocks():
    from income_stats.services.report_chart import _mix_cmap

    cm = _mix_cmap(["card", "groceries", "cafe"])
    # three distinct block colours sampled away from the seams
    assert cm(0.15) != cm(0.5) != cm(0.85)


def test_month_labels_include_weekday():
    from income_stats.services.report_chart import _month_labels

    labels = _month_labels(2026, 8)
    assert labels[0] == "1\nСб"  # 2026-08-01 is Saturday
```
Build `_mixed_frame()` inline (a few `IncomeRecord.model_dump(mode="python")` rows: mixed currencies, single/multi tags, income+expense).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — port the reference `render`, `_cmap_for` (rename `_mix_cmap`), `MixHandler`, maps, and add period bucketing + `_month_labels(year, month)`/week labels. Reuse existing `_format_amount`. Delete the old diverging/`_aggregate`/`_legend_label`/tag-breakdown-second-axes code paths this replaces (and their now-dead tests — rewrite them for the new API).
- [ ] **Step 4: Run pass; Step 5: `just check` (0 warnings) + commit** `feat: tag-mix report chart (colour=tag, hatch=currency, UAH-equiv height)`.

---

### Task 3: Wire analytics to the new renderer

**Files:**
- Modify: `src/income_stats/services/analytics_service.py`
- Test: `tests/unit/test_analytics_service.py`

**Interfaces:**
- Consumes: new `render_report_png(..., fx=..., goal=..., forecast=...)`.
- Produces: `build_chart_artifacts`/`_write_chart_artifacts` pass the per-record `frame`, `self._config.fx_to_uah`, and the existing goal/forecast (single-currency month/year) into the renderer; the obsolete `tag_totals`/second-axes threading is removed.

- [ ] **Step 1: Failing test** — assert a month chart build passes the frame + fx into `render_report_png` (patch/monkeypatch it and check kwargs include `fx`).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `_write_chart_artifacts` calls `render_report_png(frame, period, reference, png, date_range=date_range, fx=config.fx_to_uah, goal=goal, forecast=forecast)`. Remove the `tag_totals = tag_breakdown(...)` plumbing (superseded — the renderer derives tag mixes from the frame). Keep the HTML branch as-is (out of scope).
- [ ] **Step 4: Run pass; Step 5: `just check` + commit** `feat: feed per-record frame and FX into the tag-mix chart`.

---

### Task 4: Runtime tag store + parser merge

**Files:**
- Modify: `src/income_stats/models/domain.py` (`TagAlias` model)
- Modify: `src/income_stats/config/settings.py` (`StorageConfig.tags_file`)
- Modify: `src/income_stats/repositories/records_repository.py` (tag CRUD)
- Modify: `src/income_stats/parsers/income_parser.py` (accept extra tags)
- Test: `tests/integration/test_csv_repository.py`, `tests/unit/test_income_parser.py`

**Interfaces:**
- Produces: `TagAlias(tag: str, aliases: list[str], updated_by: int, updated_at: datetime)`; repository `list_tags_sync()/list_tags()` → `dict[str, list[str]]`, `add_tag_sync(tag, aliases, updated_by)/add_tag(...)` (creates or extends a tag's aliases, dedup, atomic write to `tags.csv`); parser `parse_income_message(text, config, *, today=None, extra_tags: dict[str, list[str]] | None = None)` merges `extra_tags` over `config.tags` for `_detect_labels`.

- [ ] **Step 1: Failing tests** — repo: add a tag with aliases then `list_tags` returns it; adding again extends aliases (dedup). parser: with `extra_tags={"gym": ["зал","спортзал"]}`, `"оплата 500 зал"` yields a record tagged `gym`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `TagAlias` model + `tags_file: Path = Path("data/tags.csv")`; repo CRUD mirroring the chat-settings/goals upsert pattern (key = tag, aliases stored as a JSON list cell); parser merges `extra_tags` with config tags (normalized) before `_detect_labels`. Keep `parse_income_message` backward compatible (`extra_tags` optional).
- [ ] **Step 4: Run pass; Step 5: `just check` + commit** `feat: persistent runtime tags merged into the parser taxonomy`.

---

### Task 5: `/tags` command + wiring

**Files:**
- Create: `src/income_stats/handlers/tags_handler.py`
- Modify: `src/income_stats/handlers/__init__.py`, `src/income_stats/bot/application.py`
- Modify: `src/income_stats/services/income_service.py` (pass runtime tags into the parser)
- Test: `tests/handlers/test_tags_handler.py`

**Interfaces:**
- Produces: `/tags` (list config + runtime tags with aliases); `/tags add <tag> <alias> [alias...]` (admin-only, calls `repository.add_tag`); `IncomeService.capture` reads `repository.list_tags()` and passes it as `extra_tags` to `parse_income_message` so new tags take effect immediately.

- [ ] **Step 1: Failing tests** — `/tags` lists tags incl. a runtime-added one; `/tags add gym зал спортзал` (admin) calls `add_tag("gym", ["зал","спортзал"], ...)` and replies confirmation; non-admin `/tags add` rejected; `IncomeService.capture` passes `extra_tags` from `list_tags()`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `tags_router`; admin gate via `is_telegram_admin` (mirror `/export`); list = merge config + store; add = validate tag/alias non-empty, `repository.add_tag`. Register router in `handlers/__init__` + `application.py` (dispatcher workflow-data already has `repository`). `IncomeService.capture`: `extra = await self._repository.list_tags(); parse_income_message(..., extra_tags=extra)`.
- [ ] **Step 4: Run pass; Step 5: `just check` + commit** `feat: /tags command to list and add tags and aliases`.

---

### Task 6: config wiring + full verification

**Files:**
- Modify: `config.yaml` (`analytics.fx_to_uah`, `storage.tags_file`)
- Modify: `src/income_stats/bot/application.py` (confirm `migrate_records_sync` unaffected; tags store path)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Failing test** — `config.income`/`analytics`: `load_config("config.yaml").analytics.fx_to_uah["USD"] == 41.0`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3:** `config.yaml`: under `analytics:` add `fx_to_uah: {USD: 41, EUR: 45}`; under `storage:` add `tags_file: data/tags.csv`.
- [ ] **Step 4: Full verification** — `just check` (0 warnings); smoke `uv run python main.py check-config` and `check-storage`.
- [ ] **Step 5: Commit** `feat: wire FX rates and tags store into config`.

---

## Self-Review

**Coverage:** chart redesign (colour=tag block-mix, hatch=currency, ₴-equiv height, stacked income/expense) → T2; FX config → T1/T6; grouped legend (Разом / Мікси тегів with sums + multi-colour swatch / Валюта) → T2; month weekday labels → T2; /tags list+add + runtime persistence + parser merge → T4/T5; wiring → T3/T6. ✓

**Placeholder scan:** each step carries concrete code or a concrete reference (the demo file) — no TBD.

**Type consistency:** `to_uah(amount, currency, fx)` T1↔T2↔T3; `render_report_png(..., fx=, goal=, forecast=)` T2↔T3; `list_tags()/add_tag()` T4↔T5; `extra_tags` param T4↔T5.

**Watch during execution:** T2 replaces the PR-#4 diverging + separate tag-breakdown chart — expect several existing `test_report_chart.py`/`test_analytics_service.py` tests to need rewriting for the new API (that's owned by T2/T3, not a regression). Keep the goal/forecast overlay working on the new ₴-equiv axis. No emoji in any matplotlib label; no warning suppression.
