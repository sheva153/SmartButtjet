# PR #1 Review Remediation — Design

## Goal

Prepare PR #1 for reliable use in a real Telegram group and reorganize the
single-file application into a compact modular monolith.

The change covers selected review findings A1–A3, A5, and B1–B10. It also
supports multiple categories and tags per income record and sends every chart
as both a PNG preview and an interactive HTML document.

The bot remains intentionally permissive: every monetary amount is treated as
income unless the number belongs to a recognized non-money pattern. It does
not ask for confirmation and does not require a prefix.

## Non-goals

- Do not implement the unselected `everyone_can_edit` change from A4.
- Do not remove static chart configuration or Kaleido; both become active.
- Do not introduce a database, paid API, AI call, or external parsing service.
- Do not redesign the user interface beyond changes required by the selected
  findings and the module split.
- Do not push to `main` or `master`.

## Source Layout

All application code except the root entrypoint moves under `src/income_stats`.
The package is split by conventional layers, with one file per major use case
instead of many small files:

```text
src/
└── income_stats/
    ├── __init__.py
    ├── config/
    │   ├── __init__.py
    │   └── settings.py
    ├── models/
    │   ├── __init__.py
    │   └── domain.py
    ├── parsers/
    │   ├── __init__.py
    │   └── income_parser.py
    ├── repositories/
    │   ├── __init__.py
    │   └── records_repository.py
    ├── services/
    │   ├── __init__.py
    │   ├── income_service.py
    │   ├── records_service.py
    │   ├── analytics_service.py
    │   └── admin_service.py
    ├── handlers/
    │   ├── __init__.py
    │   ├── income_handler.py
    │   ├── records_handler.py
    │   ├── analytics_handler.py
    │   └── admin_handler.py
    ├── bot/
    │   ├── __init__.py
    │   ├── application.py
    │   └── ui.py
    └── utils/
        ├── __init__.py
        ├── logging.py
        └── files.py

tests/
├── unit/
├── integration/
└── handlers/

main.py
```

Dependencies flow in one direction:

```text
handlers -> services -> repository protocol
                    -> parser
                    -> models

CSV repository -> repository protocol
bot/application -> constructs and injects dependencies
```

Handlers never read CSV directly. Repositories know nothing about Telegram.
Global `APP_CONFIG` and `STORAGE` variables are removed.

## Domain Model and CSV Migration

`IncomeRecord` changes from one `category` string to:

```text
categories: list[str]
tags: list[str]
```

Both lists are normalized, de-duplicated, and stored in deterministic order.
At least one category is always present; `other` is the fallback. Tags may be
empty.

CSV stores both fields as JSON arrays. When an existing records file has the
legacy `category` column and no `categories` column, the repository migrates
each non-empty value to a one-item JSON array, creates an empty `tags` array,
and atomically rewrites the file. No existing income record is discarded.

Editing categories and tags accepts a comma-separated list. Analytics counts
every category assigned to a record; one record with two categories
contributes its amount to both category breakdowns. Overall totals still count
the record amount only once.

## Configurable Taxonomy

Category and tag aliases are configured locally in `config.yaml`:

```yaml
income:
  categories:
    salary: [зарплата, зп, аванс]
    debt: [борг, повернули борг]
    sales: [продаж, продав, продала]
    other: []
  tags:
    card: [картка, карта, на карту, на картку]
    cash: [готівка, готівкою]
```

All matching categories and tags are returned. Matching is
case-insensitive, word-bounded where appropriate, and deterministic. Aliases
are configuration, not hard-coded service logic. The default config includes
the requested salary, card, and debt vocabulary plus the project’s existing
income categories.

## Income Parsing and Protected Spans

Parsing is deterministic and local. It first detects protected spans, then
looks for money only outside those spans.

Protected spans include:

- valid clock times from `00:00` through `23:59`;
- Ukrainian and international phone numbers with common separators;
- explicit address numbers introduced by markers such as `вул.`, `вулиця`,
  `буд.`, `будинок`, `кв.`, `квартира`, `під'їзд`, `street`, `house`, and
  `apartment`;
- existing absolute and relative date patterns.

Examples:

| Message | Result |
| --- | --- |
| `отримав 500` | save 500 |
| `500` | save 500 |
| `зп 20 000 на картку` | save 20,000; category `salary`; tag `card` |
| `повернули борг 3000` | save 3,000; category `debt` |
| `зустріч о 15:30` | ignore |
| `телефон +380 67 123 45 67` | ignore |
| `вул. Шевченка, будинок 12, квартира 35` | ignore |
| `отримав 500, телефон +380 67 123 45 67` | save only 500 |

Multiple unprotected amounts create multiple records, as today. All records
from one message receive the same detected categories and tags. There is no
confirmation state and no required `+` prefix.

## Repository Design

`records_repository.py` contains the repository protocol and the CSV
implementation to keep the module count small. Services depend on the
protocol, while application bootstrap selects the CSV implementation.

Repository responsibilities:

- atomic CSV reads, writes, and schema migration;
- record, note, and chat-setting persistence;
- duplicate protection;
- serialized access through the existing async lock;
- idempotent deletion returning whether a record existed;
- one shared model-to-row serializer instead of duplicate `_record_row` and
  `_note_row` implementations.

Record updates construct a new payload and never mutate the caller’s
`changes` dictionary.

CSV parsing catches specific Pandas, encoding, and filesystem exceptions and
preserves the original cause. Unexpected programming errors are not converted
into generic `ValueError` results.

## Service Design

### IncomeService

Parses incoming text, creates records sequentially through the repository, and
returns created records to the handler. CSV writes stay sequential because
they share one file.

### RecordsService

Owns record listing, edits, notes, edit locks, and deletion. Deletion is
idempotent. Update and note operations translate a concurrently deleted record
into a domain-level `RecordNotFound` result.

### AnalyticsService

Builds filtered frames and summaries, expands multi-category values for
breakdowns, creates an interactive HTML chart, creates a PNG preview through
Kaleido, and builds ZIP exports.

`Period` is a shared precise type:

```text
Literal["today", "week", "month", "all"]
```

No `type: ignore` is needed.

### AdminService

Owns per-chat recording state. Telegram administrator lookup remains at the
handler boundary because it is a Telegram operation; only
`TelegramAPIError`-family failures are caught and logged.

## Handler and FSM Behavior

Each handler module exports an aiogram router. `bot/application.py` places
config and services into Dispatcher workflow data so aiogram injects explicit
handler parameters.

Menu labels are navigation, never edited values. When a menu action is used
during an FSM interaction, the handler releases any owned edit lock, clears
the state, and performs the requested navigation.

Deleted-record outcomes are user-visible and never escape as `KeyError`:

- repeated delete reports that the record was already deleted;
- editing a deleted record clears the state and lock;
- adding a note to a deleted record reports that the record no longer exists.

Keyboard construction uses `InlineKeyboardBuilder` consistently.
`short_id()` lives in `bot/ui.py` and replaces repeated `record.id[:8]`.

After multiple records are persisted, independent Telegram replies are sent
with `asyncio.gather(..., return_exceptions=True)`. Individual send failures
are logged without preventing the remaining replies.

## Charts and Temporary Files

When `/chart` is requested, `AnalyticsService` generates:

1. a PNG image for immediate Telegram preview;
2. an interactive HTML document.

The handler sends both. `utils/files.py` supplies a temporary-artifact context
manager so PNG, HTML, and transient export files are removed in `finally`
blocks even when Telegram sending fails.

`analytics.static_preview` controls PNG generation and
`analytics.interactive_html` controls HTML generation. The default
configuration enables both, matching the approved behavior.

## Logging and Error Handling

`utils/logging.py` configures Loguru once:

- console level from `LOG_LEVEL`;
- rotating debug file;
- 10 MB rotation;
- 14-day retention;
- compressed archives.

Logs include relevant `chat_id`, `user_id`, and `record_id`, but never Telegram
tokens, complete ordinary messages, note contents, or `.env` values.

Expected validation and concurrency outcomes use domain exceptions and
Ukrainian user messages. Specific external errors are logged with context.
Unexpected exceptions retain their traceback and propagate to aiogram’s error
handling instead of being silently converted into permission failures.

## Developer Commands and CI

Every recipe invokes `uv` directly. The `justfile` adds:

- `just test pattern=""`;
- `just cov`;
- `just smoke`;
- the existing `just check`.

`pytest-cov` is added as a development dependency through `uv`. GitHub Actions
runs the same `just check` and `just smoke` recipes used locally instead of
duplicating their internal commands.

The smoke recipe uses temporary data and exercises configuration, parsing,
storage, analytics, chart generation, and export without a Telegram token or
network call.

## Testing

Tests mirror the source boundaries:

- parser unit tests for amounts, dates, times, phone formats, address markers,
  multiple amounts, mixed money-plus-phone input, and configured taxonomy;
- model tests for normalization and multiple categories/tags;
- repository integration tests for atomic persistence, legacy CSV migration,
  duplicate protection, non-mutating updates, notes, and repeated deletion;
- service tests for income creation, analytics, PNG+HTML generation, edit
  locks, and missing-record outcomes;
- handler tests with fake services for menu navigation, state cleanup,
  deleted records, admin lookup failures, and concurrent reply sending;
- logging tests proving idempotent sink configuration and private-data
  exclusion;
- static checks for direct `uv` recipes and CI use of `just`.

Handoff requires `just check` and `just smoke` to pass.

## Delivery

Implementation is performed on a feature branch. Once verified, the selected
commits are applied to `feature/fun-baskets`, the head branch of PR #1, and
pushed there. No commit is pushed directly to `main` or `master`.
