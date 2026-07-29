# PR #1 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix selected PR #1 review findings, introduce permissive context-aware income parsing with multiple categories and tags, produce PNG and HTML charts, and split the bot into a compact modular monolith.

**Architecture:** Application code lives in `src/income_stats` and follows `handlers -> services -> repository protocol`. A single CSV repository module owns persistence, aiogram workflow data injects services into handlers, and the root `main.py` remains a small CLI entrypoint.

**Tech Stack:** Python 3.12–3.13, aiogram 3.26, Pydantic 2, Pandas 3, Plotly 6, Kaleido 1, Loguru, pytest, pytest-cov, uv, just.

## Global Constraints

- Implement review findings A1–A3, A5, and B1–B10 only; do not implement A4.
- Treat every unprotected monetary number as income; do not add confirmation or prefix requirements.
- Protect valid times, phone numbers, dates, and explicitly marked address numbers.
- Support multiple configured categories and tags per record.
- Generate both PNG and interactive HTML for `/chart`.
- Add no paid API, AI call, database, or external parsing service.
- Keep Telegram tokens and `.env` values out of source control and logs.
- Use `uv` for dependencies and `just` for developer commands.
- Work only on feature branches and never push directly to `main` or `master`.
- Run `just check` and `just smoke` before handoff.

---

## File Map

### Create

- `src/income_stats/__init__.py` — package marker and version.
- `src/income_stats/config/__init__.py` — public config exports.
- `src/income_stats/config/settings.py` — Pydantic configuration and secrets.
- `src/income_stats/models/__init__.py` — public domain exports.
- `src/income_stats/models/domain.py` — domain models, literals, validation.
- `src/income_stats/parsers/__init__.py` — parser exports.
- `src/income_stats/parsers/income_parser.py` — protected spans, amount parsing, taxonomy extraction.
- `src/income_stats/repositories/__init__.py` — repository exports.
- `src/income_stats/repositories/records_repository.py` — protocol and CSV implementation.
- `src/income_stats/services/__init__.py` — service exports.
- `src/income_stats/services/income_service.py` — incoming-message use case.
- `src/income_stats/services/records_service.py` — records, notes, locks, deletion.
- `src/income_stats/services/analytics_service.py` — stats, charts, exports.
- `src/income_stats/services/admin_service.py` — chat recording state.
- `src/income_stats/handlers/__init__.py` — router aggregation.
- `src/income_stats/handlers/income_handler.py` — ordinary income messages.
- `src/income_stats/handlers/records_handler.py` — records, edits, notes, deletion.
- `src/income_stats/handlers/analytics_handler.py` — stats, chart, export.
- `src/income_stats/handlers/admin_handler.py` — help, status, recording mode.
- `src/income_stats/bot/__init__.py` — bot exports.
- `src/income_stats/bot/application.py` — dependency composition and polling.
- `src/income_stats/bot/ui.py` — callbacks, menu, keyboards, formatting.
- `src/income_stats/utils/__init__.py` — utility exports.
- `src/income_stats/utils/logging.py` — Loguru setup.
- `src/income_stats/utils/files.py` — temporary artifact cleanup.
- `tests/unit/test_models.py` — model and taxonomy validation.
- `tests/unit/test_income_parser.py` — parser and protected-span behavior.
- `tests/unit/test_income_service.py` — income creation use case.
- `tests/unit/test_records_service.py` — edit locks, deletion, missing records.
- `tests/unit/test_analytics_service.py` — filtering, categories, charts, exports.
- `tests/integration/test_csv_repository.py` — CSV persistence and migration.
- `tests/handlers/test_income_handler.py` — income handler and concurrent replies.
- `tests/handlers/test_records_handler.py` — FSM navigation and stale records.
- `tests/handlers/test_analytics_handler.py` — PNG/HTML and cleanup.
- `tests/handlers/test_admin_handler.py` — Telegram admin errors.
- `tests/smoke/test_cli.py` — end-to-end CLI smoke test.
- `.github/workflows/check.yml` — CI invoking `just`.

### Modify

- `main.py` — replace monolith with CLI entrypoint.
- `config.yaml` — configured category/tag aliases and enabled chart formats.
- `pyproject.toml` — src package, Hatchling, pytest-cov, test paths.
- `uv.lock` — generated only through `uv`.
- `justfile` — direct uv commands, filtered tests, coverage, chart setup, smoke.
- `README.md` — new package layout, taxonomy, chart setup, commands.
- `.gitignore` — generated logs, charts, coverage.
- `.env.example` — documented log level only.

### Remove after migration

- `tests/test_main.py` — split into focused test modules after equivalent coverage exists.

---

### Task 1: Package Scaffold, Domain Models, and Configuration

**Files:**
- Create: `src/income_stats/__init__.py`
- Create: `src/income_stats/config/__init__.py`
- Create: `src/income_stats/config/settings.py`
- Create: `src/income_stats/models/__init__.py`
- Create: `src/income_stats/models/domain.py`
- Create: `tests/unit/test_models.py`
- Modify: `pyproject.toml`
- Modify: `config.yaml`

**Interfaces:**
- Produces: `Period`, `ParsedIncome`, `IncomeRecord`, `RecordNote`, `ChatSetting`.
- Produces: `TaxonomyConfig`, `IncomeConfig`, `AppConfig`, `Secrets`, `load_config`.
- Produces: `normalize_label(value: str) -> str`.

- [ ] **Step 1: Add failing model and config tests**

```python
from decimal import Decimal

from income_stats.config.settings import AppConfig
from income_stats.models.domain import IncomeRecord


def test_record_normalizes_multiple_categories_and_tags() -> None:
    record = IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=7,
        original_text="зп 500 на картку",
        amount=Decimal("500"),
        currency="uah",
        categories=["Salary", "salary", "Debt"],
        tags=["Card", "card"],
        updated_by=7,
    )
    assert record.currency == "UAH"
    assert record.categories == ["salary", "debt"]
    assert record.tags == ["card"]


def test_config_accepts_alias_maps() -> None:
    config = AppConfig.model_validate(
        {
            "income": {
                "categories": {"salary": ["зарплата", "зп"], "other": []},
                "tags": {"card": ["картка"]},
            }
        }
    )
    assert config.income.categories["salary"] == ["зарплата", "зп"]
    assert config.income.tags["card"] == ["картка"]
```

- [ ] **Step 2: Run the focused tests and verify import failure**

Run: `uv run pytest tests/unit/test_models.py -v`

Expected: FAIL because `income_stats.models.domain` and config modules do not exist.

- [ ] **Step 3: Configure the src package**

Add to `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/income_stats"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]

[tool.pyright]
include = ["main.py", "src", "tests"]
```

Run: `uv sync`

Expected: Hatchling metadata and package installation are reflected in `uv.lock`.

- [ ] **Step 4: Implement domain types and normalization**

Create `src/income_stats/models/domain.py` with these public definitions:

```python
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

Period = Literal["today", "week", "month", "all"]


def normalize_label(value: str) -> str:
    normalized = "_".join(value.strip().casefold().split())
    if not normalized:
        raise ValueError("Label must not be empty")
    return normalized[:50]


def _normalize_labels(values: list[str], *, fallback: str | None) -> list[str]:
    labels = list(dict.fromkeys(normalize_label(value) for value in values))
    if not labels and fallback is not None:
        return [fallback]
    return labels


class ParsedIncome(BaseModel):
    amount: Decimal | None
    currency: str = "UAH"
    categories: list[str] = Field(default_factory=lambda: ["other"])
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    income_date: date


class IncomeRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    telegram_message_id: int
    source_index: int = 0
    chat_id: int
    user_id: int
    username: str = ""
    original_text: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    categories: list[str] = Field(default_factory=lambda: ["other"])
    tags: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1000)
    income_date: date = Field(default_factory=date.today)
    status: Literal["saved"] = "saved"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: int

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback="other")

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values, fallback=None)
```

Move `RecordNote`, `ChatSetting`, `FunItem`, and `FunSummaryConfig` from the
old `main.py` into the same module without changing their public behavior.

- [ ] **Step 5: Implement typed configuration**

Create `src/income_stats/config/settings.py` with the current bot, storage,
permission, analytics, fun-summary, and secrets models. Define taxonomy maps:

```python
class IncomeConfig(BaseModel):
    default_currency: str = "UAH"
    categories: dict[str, list[str]] = Field(
        default_factory=lambda: {"other": []}
    )
    tags: dict[str, list[str]] = Field(default_factory=dict)
    allow_custom_categories: bool = True

    @field_validator("categories", "tags")
    @classmethod
    def normalize_taxonomy(
        cls, values: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        return {
            normalize_label(name): list(dict.fromkeys(alias.casefold() for alias in aliases))
            for name, aliases in values.items()
        }
```

Update `config.yaml` with alias maps for salary, debt, sales, freelance,
consulting, investment, gift, other, card, and cash. Keep both
`analytics.static_preview` and `analytics.interactive_html` enabled.

- [ ] **Step 6: Run model tests**

Run: `uv run pytest tests/unit/test_models.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock config.yaml src/income_stats tests/unit/test_models.py
git commit -m "refactor: add package models and configuration"
```

---

### Task 2: Protected-Span Income Parser

**Files:**
- Create: `src/income_stats/parsers/__init__.py`
- Create: `src/income_stats/parsers/income_parser.py`
- Create: `tests/unit/test_income_parser.py`

**Interfaces:**
- Consumes: `IncomeConfig`, `ParsedIncome`.
- Produces: `find_protected_spans(text: str) -> list[tuple[int, int]]`.
- Produces: `parse_income_message(text: str, config: IncomeConfig, *, today: date | None = None) -> list[ParsedIncome]`.

- [ ] **Step 1: Write failing protected-span and taxonomy tests**

```python
import pytest
from decimal import Decimal

from income_stats.config.settings import IncomeConfig
from income_stats.parsers.income_parser import parse_income_message


@pytest.fixture
def income_config() -> IncomeConfig:
    return IncomeConfig(
        categories={
            "salary": ["зарплата", "зп"],
            "debt": ["борг", "повернули борг"],
            "other": [],
        },
        tags={"card": ["картка", "на картку"]},
    )


@pytest.mark.parametrize(
    "text",
    [
        "зустріч о 15:30",
        "телефон +380 67 123 45 67",
        "вул. Шевченка, будинок 12, квартира 35",
    ],
)
def test_non_money_patterns_are_ignored(
    text: str, income_config: IncomeConfig
) -> None:
    assert parse_income_message(text, income_config) == []


def test_money_next_to_phone_is_kept(income_config: IncomeConfig) -> None:
    parsed = parse_income_message(
        "отримав 500, телефон +380 67 123 45 67", income_config
    )
    assert [item.amount for item in parsed] == [Decimal("500.00")]


def test_multiple_categories_and_tags_are_detected(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "зп 20 000 на картку, повернули борг", income_config
    )
    assert parsed[0].categories == ["salary", "debt"]
    assert parsed[0].tags == ["card"]
```

- [ ] **Step 2: Run the parser tests and verify failure**

Run: `uv run pytest tests/unit/test_income_parser.py -v`

Expected: FAIL because the parser module does not exist.

- [ ] **Step 3: Implement protected spans**

In `income_parser.py`, compile separate patterns and merge overlapping spans:

```python
TIME_PATTERN = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?380|0)\s*\(?\d{2,3}\)?"
    r"(?:[\s-]*\d){7,9}(?!\d)"
)
ADDRESS_PATTERN = re.compile(
    r"(?ix)\b(?:вул(?:иця)?|буд(?:инок)?|кв(?:артира)?|"
    r"під['’]?їзд|street|house|apartment)\.?\s*"
    r"(?:[\w.'’А-Яа-яІіЇїЄєҐґ-]+\s*,?\s*){0,4}"
    r"(?P<number>\d+[A-Za-zА-Яа-я]?(?:[/-]\d+)?)"
)


def find_protected_spans(text: str) -> list[tuple[int, int]]:
    spans = [
        match.span()
        for pattern in (TIME_PATTERN, PHONE_PATTERN, DATE_PATTERN, ADDRESS_PATTERN)
        for match in pattern.finditer(text)
    ]
    return merge_spans(spans)
```

The amount matcher must discard any match whose span overlaps a protected
span. Do not replace protected text before matching because replacement can
shift positions and allow partial phone-number matches.

- [ ] **Step 4: Implement configured taxonomy extraction**

```python
def detect_labels(text: str, aliases: dict[str, list[str]]) -> list[str]:
    lowered = text.casefold()
    return [
        label
        for label, terms in aliases.items()
        if any(
            re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
            for term in terms
        )
    ]
```

`parse_income_message` must return `[]` when every numeric span is protected.
For every unprotected amount it returns one `ParsedIncome`, assigning all
detected categories and tags. Categories fall back to `["other"]`.

- [ ] **Step 5: Run parser tests**

Run: `uv run pytest tests/unit/test_income_parser.py -v`

Expected: PASS, including bare `500`, time, phone, address, mixed input,
multiple amounts, dates, and taxonomy cases.

- [ ] **Step 6: Commit**

```bash
git add src/income_stats/parsers tests/unit/test_income_parser.py
git commit -m "feat: parse income around protected numeric patterns"
```

---

### Task 3: Repository Protocol, CSV Migration, and Safe Updates

**Files:**
- Create: `src/income_stats/repositories/__init__.py`
- Create: `src/income_stats/repositories/records_repository.py`
- Create: `tests/integration/test_csv_repository.py`

**Interfaces:**
- Consumes: domain models and `StorageConfig`.
- Produces: `RecordNotFoundError`.
- Produces: `RecordsRepository(Protocol)`.
- Produces: `CsvRecordsRepository`.

- [ ] **Step 1: Write failing repository tests**

```python
import json
from decimal import Decimal


def test_legacy_category_is_migrated(csv_repository, legacy_record_row) -> None:
    legacy_record_row["category"] = "salary"
    csv_repository.records_path.write_text(
        make_csv(legacy_record_row), encoding="utf-8"
    )
    records = csv_repository.read_records_sync()
    assert json.loads(records.iloc[0]["categories"]) == ["salary"]
    assert json.loads(records.iloc[0]["tags"]) == []


def test_update_does_not_mutate_changes(csv_repository, saved_record) -> None:
    changes = {"categories": ["salary", "debt"]}
    csv_repository.update_record_sync(saved_record.id, changes, updated_by=9)
    assert changes == {"categories": ["salary", "debt"]}


def test_delete_is_idempotent(csv_repository, saved_record) -> None:
    assert csv_repository.delete_record_sync(saved_record.id) is True
    assert csv_repository.delete_record_sync(saved_record.id) is False
```

- [ ] **Step 2: Run repository tests and verify failure**

Run: `uv run pytest tests/integration/test_csv_repository.py -v`

Expected: FAIL because `CsvRecordsRepository` does not exist.

- [ ] **Step 3: Define the repository protocol**

```python
class RecordNotFoundError(LookupError):
    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        super().__init__(f"Record not found: {record_id}")


class RecordsRepository(Protocol):
    async def create_record(self, record: IncomeRecord) -> IncomeRecord: ...
    async def get_record(self, record_id: str) -> IncomeRecord | None: ...
    async def list_records(self, chat_id: int) -> list[IncomeRecord]: ...
    async def update_record(
        self, record_id: str, changes: Mapping[str, object], updated_by: int
    ) -> IncomeRecord: ...
    async def delete_record(self, record_id: str) -> bool: ...
    async def add_note(self, note: RecordNote) -> RecordNote: ...
    async def list_notes(self, record_id: str) -> list[RecordNote]: ...
    async def is_chat_enabled(self, chat_id: int) -> bool: ...
    async def set_chat_enabled(
        self, chat_id: int, enabled: bool, updated_by: int
    ) -> ChatSetting: ...
```

- [ ] **Step 4: Move CSV persistence and implement migration**

Move storage behavior from `main.py` into `CsvRecordsRepository`. Serialize
list fields with compact JSON:

```python
def _encode_cell(value: object) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _to_row(model: BaseModel) -> dict[str, str]:
    return {
        key: _encode_cell(value)
        for key, value in model.model_dump(mode="json").items()
    }
```

Before schema validation, migrate `category` to `categories`, add `tags`, drop
the legacy column, order columns using `IncomeRecord.model_fields`, and write
atomically.

- [ ] **Step 5: Replace broad exceptions and mutation**

Catch `OSError`, `UnicodeError`, `pandas.errors.ParserError`, and
`pandas.errors.EmptyDataError` explicitly. Preserve causes with `raise ... from
error`. Replace `changes.update(...)` with:

```python
payload = {
    **changes,
    "updated_at": datetime.now(UTC),
    "updated_by": updated_by,
}
updated = current.model_copy(update=payload)
```

Raise `RecordNotFoundError(record_id)` for update and note races. Deletion
returns `False` for an absent record.

- [ ] **Step 6: Run repository tests**

Run: `uv run pytest tests/integration/test_csv_repository.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/income_stats/repositories tests/integration/test_csv_repository.py
git commit -m "refactor: extract safe CSV records repository"
```

---

### Task 4: Income and Records Services

**Files:**
- Create: `src/income_stats/services/__init__.py`
- Create: `src/income_stats/services/income_service.py`
- Create: `src/income_stats/services/records_service.py`
- Create: `tests/unit/test_income_service.py`
- Create: `tests/unit/test_records_service.py`

**Interfaces:**
- Consumes: `RecordsRepository`, `IncomeConfig`, parser, domain models.
- Produces: `IncomeService.capture(...) -> list[IncomeRecord]`.
- Produces: `RecordsService`, `EditLocks`, `RecordPage`.

- [ ] **Step 1: Write failing IncomeService tests**

```python
async def test_capture_persists_each_unprotected_amount(fake_repository) -> None:
    service = IncomeService(fake_repository, income_config)
    records = await service.capture(
        text="зп 500 і 300 на картку",
        telegram_message_id=10,
        chat_id=-100,
        user_id=7,
        username="felix",
        today=date(2026, 7, 29),
    )
    assert [record.amount for record in records] == [
        Decimal("500.00"),
        Decimal("300.00"),
    ]
    assert all(record.categories == ["salary"] for record in records)
    assert all(record.tags == ["card"] for record in records)
```

- [ ] **Step 2: Write failing RecordsService tests**

```python
async def test_repeated_delete_has_stable_result(records_service, record) -> None:
    assert await records_service.delete(record.id) is True
    assert await records_service.delete(record.id) is False


async def test_invalid_edit_keeps_lock(records_service, record) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)
    with pytest.raises(ValueError):
        await records_service.update_field(record.id, "amount", "zero", 7)
    assert not records_service.acquire_edit(record.id, user_id=8)
```

- [ ] **Step 3: Run service tests and verify failure**

Run: `uv run pytest tests/unit/test_income_service.py tests/unit/test_records_service.py -v`

Expected: FAIL because the services do not exist.

- [ ] **Step 4: Implement IncomeService**

```python
class IncomeService:
    def __init__(
        self, repository: RecordsRepository, config: IncomeConfig
    ) -> None:
        self._repository = repository
        self._config = config

    async def capture(
        self,
        *,
        text: str,
        telegram_message_id: int,
        chat_id: int,
        user_id: int,
        username: str,
        today: date,
    ) -> list[IncomeRecord]:
        parsed = parse_income_message(text, self._config, today=today)
        created: list[IncomeRecord] = []
        for source_index, item in enumerate(parsed):
            if item.amount is None:
                continue
            created.append(
                await self._repository.create_record(
                    IncomeRecord(
                        telegram_message_id=telegram_message_id,
                        source_index=source_index,
                        chat_id=chat_id,
                        user_id=user_id,
                        username=username,
                        original_text=text,
                        amount=item.amount,
                        currency=item.currency,
                        categories=item.categories,
                        tags=item.tags,
                        description=item.description,
                        income_date=item.income_date,
                        updated_by=user_id,
                    )
                )
            )
        return created
```

- [ ] **Step 5: Implement RecordsService**

Move page building, edit locks, field parsing, notes, and deletion from
`main.py`. Use a `RecordField` literal for editable fields:

```python
@dataclass(frozen=True)
class RecordPage:
    records: list[IncomeRecord]
    page: int
    total_pages: int


class EditLocks:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[int, datetime]] = {}

    def acquire(self, record_id: str, user_id: int, seconds: int) -> bool:
        now = datetime.now(UTC)
        owner = self._locks.get(record_id)
        if owner and owner[1] > now and owner[0] != user_id:
            return False
        self._locks[record_id] = (
            user_id,
            now + timedelta(seconds=seconds),
        )
        return True

    def release(self, record_id: str, user_id: int) -> None:
        owner = self._locks.get(record_id)
        if owner and owner[0] == user_id:
            self._locks.pop(record_id, None)


RecordField = Literal[
    "amount", "currency", "categories", "tags", "income_date", "description"
]
```

Comma-separated categories and tags are normalized through domain validators.
Release locks only after success, explicit cancellation/navigation,
missing-record results, or lock expiry.

- [ ] **Step 6: Run service tests**

Run: `uv run pytest tests/unit/test_income_service.py tests/unit/test_records_service.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/income_stats/services tests/unit/test_income_service.py tests/unit/test_records_service.py
git commit -m "refactor: add income and records services"
```

---

### Task 5: Analytics, PNG/HTML Charts, Exports, and Admin Service

**Files:**
- Create: `src/income_stats/services/analytics_service.py`
- Create: `src/income_stats/services/admin_service.py`
- Create: `src/income_stats/utils/__init__.py`
- Create: `src/income_stats/utils/files.py`
- Create: `tests/unit/test_analytics_service.py`

**Interfaces:**
- Consumes: repository, `AnalyticsConfig`, `StorageConfig`, `Period`.
- Produces: `ChartArtifacts(png: Path | None, html: Path | None)`.
- Produces: `AnalyticsService.summary`, `build_chart_artifacts`, `build_export`.
- Produces: `temporary_artifacts(*paths: Path)`.
- Produces: `AdminService`.

- [ ] **Step 1: Write failing analytics tests**

```python
async def test_category_breakdown_expands_multiple_categories(
    analytics_service, records
) -> None:
    frame = await analytics_service.frame(chat_id=-100)
    breakdown = analytics_service.category_breakdown(frame)
    assert breakdown.loc["salary", "amount"] == 500
    assert breakdown.loc["debt", "amount"] == 500
    assert analytics_service.total(frame) == 500


async def test_chart_builds_png_and_html(
    analytics_service, monkeypatch
) -> None:
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.write_image",
        lambda self, path: Path(path).write_bytes(b"png"),
    )
    artifacts = await analytics_service.build_chart_artifacts(-100, "month")
    assert artifacts.png is not None and artifacts.png.read_bytes() == b"png"
    assert artifacts.html is not None and artifacts.html.exists()
```

- [ ] **Step 2: Run analytics tests and verify failure**

Run: `uv run pytest tests/unit/test_analytics_service.py -v`

Expected: FAIL because `AnalyticsService` does not exist.

- [ ] **Step 3: Implement AnalyticsService and precise Period typing**

Move analytics, summary, chart, export, and fun-summary code from `main.py`.
Parse list-valued CSV columns before frame operations. Use `DataFrame.explode`
only for category/tag breakdowns, never for the overall total.

Generate both formats from the same figure:

```python
@dataclass(frozen=True)
class ChartArtifacts:
    png: Path | None
    html: Path | None


figure.write_html(html_path, include_plotlyjs=True)
figure.write_image(png_path, format="png", width=1200, height=700, scale=2)
```

Kaleido v1 requires Chrome or Chromium; no runtime auto-download occurs in the
service.

- [ ] **Step 4: Implement temporary artifact cleanup**

```python
@contextmanager
def temporary_artifacts(*paths: Path) -> Iterator[tuple[Path, ...]]:
    try:
        yield paths
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
```

Use this context in handlers for chart and export sending.

- [ ] **Step 5: Implement AdminService**

```python
class AdminService:
    def __init__(self, repository: RecordsRepository) -> None:
        self._repository = repository

    async def status(self, chat_id: int) -> bool:
        return await self._repository.is_chat_enabled(chat_id)

    async def set_status(
        self, chat_id: int, *, enabled: bool, updated_by: int
    ) -> ChatSetting:
        return await self._repository.set_chat_enabled(
            chat_id, enabled=enabled, updated_by=updated_by
        )
```

- [ ] **Step 6: Run analytics tests**

Run: `uv run pytest tests/unit/test_analytics_service.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/income_stats/services/analytics_service.py src/income_stats/services/admin_service.py src/income_stats/utils tests/unit/test_analytics_service.py
git commit -m "feat: add analytics services and dual chart export"
```

---

### Task 6: Bot UI, Thin Handlers, and Dependency Injection

**Files:**
- Create: `src/income_stats/bot/__init__.py`
- Create: `src/income_stats/bot/ui.py`
- Create: `src/income_stats/handlers/__init__.py`
- Create: `src/income_stats/handlers/income_handler.py`
- Create: `src/income_stats/handlers/records_handler.py`
- Create: `src/income_stats/handlers/analytics_handler.py`
- Create: `src/income_stats/handlers/admin_handler.py`
- Create: `tests/handlers/test_income_handler.py`
- Create: `tests/handlers/test_records_handler.py`
- Create: `tests/handlers/test_analytics_handler.py`
- Create: `tests/handlers/test_admin_handler.py`

**Interfaces:**
- Consumes: all four services through aiogram workflow data.
- Produces: `income_router`, `records_router`, `analytics_router`, `admin_router`.
- Produces: `EditState`, `RecordAction`, menu and keyboard helpers.

- [ ] **Step 1: Write failing handler tests with fake services**

```python
async def test_income_handler_sends_replies_concurrently(
    fake_message, fake_income_service, monkeypatch
) -> None:
    fake_income_service.capture.return_value = [record_one, record_two]
    gathered: list[object] = []

    async def fake_gather(*awaitables, return_exceptions):
        gathered.extend(awaitables)
        assert return_exceptions is True
        return [None, None]

    monkeypatch.setattr(asyncio, "gather", fake_gather)
    await income_message_handler(fake_message, fake_income_service)
    assert len(gathered) == 2


async def test_menu_during_edit_clears_state_without_update(
    fake_message, fake_state, fake_records_service
) -> None:
    fake_message.text = "📊 Аналітика"
    handled = await handle_menu_during_interaction(
        fake_message, fake_state, fake_records_service
    )
    assert handled is True
    fake_records_service.update_field.assert_not_awaited()
    fake_state.clear.assert_awaited_once()
```

- [ ] **Step 2: Run handler tests and verify failure**

Run: `uv run pytest tests/handlers -v`

Expected: FAIL because handler modules do not exist.

- [ ] **Step 3: Move callbacks, formatting, and keyboards into bot/ui.py**

Use `InlineKeyboardBuilder` for every inline keyboard. Provide:

```python
def short_id(record_id: str) -> str:
    return record_id[:8]


def format_labels(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "—"
```

Add edit buttons for both categories and tags. Keep existing Ukrainian
user-facing behavior except where selected findings require new messages.

- [ ] **Step 4: Implement thin income and records handlers**

Handlers accept services by parameter name:

```python
@router.message(F.text)
async def income_message_handler(
    message: Message, income_service: IncomeService
) -> None:
    ...
```

Create records sequentially through `IncomeService`, then schedule independent
Telegram replies with:

```python
results = await asyncio.gather(
    *(message.reply(format_success(record), reply_markup=success_keyboard(record))
      for record in records),
    return_exceptions=True,
)
```

Log each exception returned by gather. Do not re-add income confirmation state.

- [ ] **Step 5: Implement stale-record and FSM behavior**

Every record callback checks service results. Repeated delete reports
`Запис уже видалено.` Edit and note races report
`Запис більше не існує.` and clear state/lock.

Menu labels are handled before field parsing. Navigation clears interaction
state and releases the owned lock.

- [ ] **Step 6: Implement analytics and admin handlers**

The chart handler sends PNG with `answer_photo`, then HTML with
`answer_document`, inside artifact cleanup. Catch only
`TelegramAPIError` during admin lookup:

```python
except TelegramAPIError:
    logger.bind(chat_id=chat_id, user_id=user_id).warning(
        "Telegram admin lookup failed"
    )
    return False
```

- [ ] **Step 7: Run handler tests**

Run: `uv run pytest tests/handlers -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/income_stats/bot/ui.py src/income_stats/handlers tests/handlers
git commit -m "refactor: add injected Telegram handlers"
```

---

### Task 7: Application Bootstrap, CLI, and Logging

**Files:**
- Create: `src/income_stats/bot/application.py`
- Create: `src/income_stats/utils/logging.py`
- Modify: `main.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Test: `tests/unit/test_logging.py`
- Test: `tests/smoke/test_cli.py`

**Interfaces:**
- Consumes: settings, repository, services, routers.
- Produces: `build_dispatcher(config: AppConfig) -> Dispatcher`.
- Produces: `run_bot(config: AppConfig) -> None`.
- Produces: `configure_logging(level: str, log_file: Path) -> None`.
- Produces: CLI commands preserving current names.

- [ ] **Step 1: Write failing bootstrap and logging tests**

```python
def test_dispatcher_contains_injected_services(app_config) -> None:
    dispatcher = build_dispatcher(app_config)
    assert isinstance(dispatcher["income_service"], IncomeService)
    assert isinstance(dispatcher["records_service"], RecordsService)
    assert isinstance(dispatcher["analytics_service"], AnalyticsService)
    assert isinstance(dispatcher["admin_service"], AdminService)


def test_logging_configuration_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "bot.log"
    configure_logging("INFO", log_file)
    configure_logging("INFO", log_file)
    logger.info("one-marker")
    logger.complete()
    assert log_file.read_text().count("one-marker") == 1
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/unit/test_logging.py tests/smoke/test_cli.py -v`

Expected: FAIL because application bootstrap and the new CLI are absent.

- [ ] **Step 3: Configure privacy-safe logging**

Move the current Loguru setup to `utils/logging.py`. Keep console and rotating
file sinks. Bind only identifiers and decisions; never log full message text,
notes, token, or `.env` values.

- [ ] **Step 4: Build Dispatcher workflow data**

Use aiogram 3.26 workflow injection:

```python
dispatcher = Dispatcher(
    income_service=income_service,
    records_service=records_service,
    analytics_service=analytics_service,
    admin_service=admin_service,
    app_config=config,
)
```

Include the four routers. Remove `APP_CONFIG`, `STORAGE`, and `app_context`.

- [ ] **Step 5: Replace root main.py with a thin CLI**

Keep `bot`, `parse`, `check-config`, `check-storage`, `analytics`, and `export`
subcommands. Each command loads config and calls package services. `main.py`
must contain no domain model, parser, repository, analytics, or Telegram
handler implementation.

- [ ] **Step 6: Run bootstrap and CLI tests**

Run: `uv run pytest tests/unit/test_logging.py tests/smoke/test_cli.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py src/income_stats/bot/application.py src/income_stats/utils/logging.py .env.example .gitignore tests/unit/test_logging.py tests/smoke/test_cli.py
git commit -m "refactor: compose bot application and thin CLI"
```

---

### Task 8: Replace Legacy Tests and Complete Review Coverage

**Files:**
- Modify: focused tests under `tests/unit`, `tests/integration`, `tests/handlers`.
- Delete: `tests/test_main.py`.

**Interfaces:**
- Consumes: all new package interfaces.
- Produces: equivalent coverage for every legacy parser, storage, analytics,
  fun-summary, keyboard, and chat-isolation behavior.

- [ ] **Step 1: Map every legacy test to a focused module**

Move parser assertions to `tests/unit/test_income_parser.py`, storage assertions
to `tests/integration/test_csv_repository.py`, analytics and fun-summary
assertions to `tests/unit/test_analytics_service.py`, and keyboard assertions
to handler/UI tests. Keep test names descriptive and remove direct access to
private repository helpers.

- [ ] **Step 2: Run old and new suites together**

Run: `uv run pytest tests -v`

Expected: PASS with both `tests/test_main.py` and the new modules present.

- [ ] **Step 3: Delete the legacy monolith test file**

Delete `tests/test_main.py` only after the new suite covers all existing
behavior and selected review regressions.

- [ ] **Step 4: Run the complete suite**

Run: `uv run pytest tests -v`

Expected: PASS without importing application behavior from the old monolithic
`main.py`.

- [ ] **Step 5: Commit**

```bash
git add tests
git commit -m "test: replace monolith tests with layered coverage"
```

---

### Task 9: Developer Commands, Coverage, Smoke Test, and CI

**Files:**
- Modify: `justfile`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `.github/workflows/check.yml`
- Modify: `README.md`

**Interfaces:**
- Produces: `just test pattern=""`, `just cov`, `just setup-chart`,
  `just smoke`, and `just check`.

- [ ] **Step 1: Add pytest-cov through uv**

Run: `uv add --dev "pytest-cov>=7,<8"`

Expected: `pyproject.toml` and `uv.lock` update through uv.

- [ ] **Step 2: Add just recipes**

```just
test pattern="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "{{pattern}}" ]]; then
        uv run pytest -k "{{pattern}}"
    else
        uv run pytest
    fi

cov:
    uv run pytest --cov=income_stats --cov-report=term-missing

setup-chart:
    uv run plotly_get_chrome -y

smoke:
    uv run pytest tests/smoke -v
```

Ensure every existing recipe invokes `uv` directly, never `python -m uv`.

- [ ] **Step 3: Add CI using the same recipes**

Create `.github/workflows/check.yml`:

```yaml
name: check

on:
  pull_request:
  push:
    branches-ignore: [main, master]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - uses: extractions/setup-just@v3
      - run: uv sync --locked
      - run: just setup-chart
      - run: just check
      - run: just smoke
```

- [ ] **Step 4: Document taxonomy, chart dependencies, and commands**

Update README with:

- the `src/income_stats` package layout;
- configurable category/tag aliases;
- examples showing protected time, phone, and address numbers;
- `just setup-chart` after setup;
- PNG and HTML chart behavior;
- `just test`, `just cov`, `just smoke`, and `just check`.

- [ ] **Step 5: Verify recipes**

Run: `just --list`

Expected: all existing and new recipes are listed.

Run: `just test income_parser`

Expected: only matching parser tests run and pass.

Run: `just cov`

Expected: tests pass and a coverage report for `income_stats` is printed.

- [ ] **Step 6: Commit**

```bash
git add justfile pyproject.toml uv.lock .github/workflows/check.yml README.md
git commit -m "chore: add shared local and CI verification"
```

---

### Task 10: Full Verification and PR #1 Delivery

**Files:**
- Review: all changed files.
- Modify only if checks reveal scoped defects.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a verified `feature/fun-baskets` head for PR #1.

- [ ] **Step 1: Install static chart runtime**

Run: `just setup-chart`

Expected: a compatible Chrome/Chromium installation is available to Kaleido.

- [ ] **Step 2: Run required checks**

Run: `just check`

Expected: Ruff formatting, Ruff lint, Pyright, and all pytest tests PASS.

Run: `just smoke`

Expected: CLI parsing, temporary CSV persistence, analytics, PNG, HTML, and ZIP
generation PASS without a Telegram token or network request.

- [ ] **Step 3: Review scope and whitespace**

Run: `git diff --check feature/fun-baskets...HEAD`

Expected: no whitespace errors.

Run: `git diff --stat feature/fun-baskets...HEAD`

Expected: only package refactor, tests, config, dependencies, commands, CI,
documentation, and approved design/plan files.

- [ ] **Step 4: Verify secrets and generated files**

Run: `git status --short`

Expected: clean worktree; no `.env`, Telegram token, logs, CSV data, chart,
archive, Chrome binary, or coverage artifact is tracked.

- [ ] **Step 5: Fast-forward the PR branch**

Because `feature/safe-income-routing` descends from `feature/fun-baskets`,
switch and fast-forward:

```bash
git switch feature/fun-baskets
git merge --ff-only feature/safe-income-routing
```

Expected: no merge commit and no conflict.

- [ ] **Step 6: Push only the PR head branch**

```bash
git push origin feature/fun-baskets
```

Expected: PR #1 updates. Do not push `main` or `master`.

- [ ] **Step 7: Re-read PR state**

Use GitHub API or authenticated `gh` to confirm PR #1 still targets `main`,
its head is `feature/fun-baskets`, and the pushed head SHA matches local
`HEAD`.

- [ ] **Step 8: Handoff**

Report:

- fixed review item numbers;
- parser examples and taxonomy behavior;
- module architecture;
- CSV migration behavior;
- PNG and HTML chart behavior;
- final test count;
- `just check` and `just smoke` results;
- PR #1 URL and head commit.
