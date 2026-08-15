# Income Notes Telegram Bot — Design

## Goal

Build an interactive Telegram bot for a dedicated group where every regular
user message represents income. The bot parses Ukrainian and English text
locally, creates an editable income card, stores confirmed data in CSV, and
produces Pandas-based statistics and Plotly charts without paid AI requests.

## Product Decisions

- Every regular message in an allowed group is an income candidate.
- Parsing is deterministic and local; no OpenAI API, local LLM, or paid model.
- Ukrainian and English input are supported.
- If a currency is omitted, it defaults to `UAH`.
- Any group member may edit any record during the MVP.
- Inline cards are the primary interaction.
- Recommended defaults from the design questionnaire are accepted where the
  user did not specify another choice.

## Input Parsing

The parser normalizes whitespace, letter case, decimal separators, and common
thousands separators. It recognizes:

- UAH: `грн`, `гривня`, `гривні`, `гривень`, `₴`, `UAH`;
- USD: `$`, `USD`, `дол`, `долар`, `долари`, `dollar`, `dollars`;
- EUR: `€`, `EUR`, `євро`, `euro`, `euros`.

It extracts one or more monetary amounts. A message containing multiple
amounts creates a separate draft for each amount. A missing amount creates one
draft with an empty amount that must be filled manually.

Both the unchanged `original_text` and a cleaned `description` are retained.
The parser suggests a category from Ukrainian and English keywords; users can
replace it with a configured or custom category.

## Interactive Flow

After parsing, the bot replies with an inline card:

```text
Новий дохід

Сума: 1 500.00 UAH
Категорія: consulting
Опис: консультація для клієнта

[💵 Сума]       [💱 Валюта]
[🏷 Категорія]  [📝 Опис]
[➕ Нотатка]    [✅ Зберегти]
[❌ Скасувати]
```

Valid parsed drafts are saved automatically and remain editable. Drafts with a
missing amount require explicit confirmation after the amount is entered.

Pressing an edit button starts an aiogram FSM step. The next message from the
person who pressed the button becomes the new value. A record is locked for
two minutes while a field is being edited. Other members receive a short
"record is being edited" response.

Deletion requires confirmation. During the MVP, the author and configured or
Telegram group administrators may delete a record. All group members may edit
record fields. Access rules are centralized so stricter permissions can be
enabled later without changing handlers.

## Notes

Each income record may have multiple notes. Notes are stored separately and
linked through `record_id`. Any member may add a note. A note may be edited or
deleted by its author or an administrator.

## Telegram Navigation

The bot provides:

- a persistent reply keyboard for Records, Analytics, Charts, and Help;
- an inline `/menu` equivalent;
- paginated record lists;
- Back, Cancel, and Main Menu buttons;
- `/cancel` for terminating any active FSM interaction.

The interface language is Ukrainian in the MVP. Input parsing remains
bilingual.

## Storage

`data/records.csv` contains:

```text
id,telegram_message_id,chat_id,user_id,username,original_text,amount,currency,
category,description,status,created_at,updated_at,updated_by
```

`data/record_notes.csv` contains:

```text
id,record_id,user_id,username,text,created_at,updated_at
```

Pydantic validates every record before storage. Money uses `Decimal`, never
binary floating point. Timestamps are stored in UTC and displayed using the
configured `Europe/Kyiv` timezone.

Pandas CSV work runs through `asyncio.to_thread()` so it does not block the
Telegram event loop. Writes use `asyncio.Lock` and atomic temporary-file
replacement. The pair `(chat_id, telegram_message_id)` prevents duplicate
message processing.

## Analytics

Supported periods:

- today;
- current week;
- current month;
- all time;
- custom date range.

Statistics include totals, record count, average income, and breakdowns by
category, member, day, and month.

Plotly produces a PNG preview and an interactive HTML file. Export produces a
ZIP containing records, notes, and generated analytics. Export is restricted
to administrators.

## Configuration and Secrets

`.env` contains only secrets:

```text
TELEGRAM_BOT_TOKEN
```

`config.yaml` contains allowed chats, administrators, timezone, categories,
storage paths, edit lock duration, permissions, and analytics defaults.
Configuration is loaded with `yaml.safe_load()` and validated by Pydantic.

## Dependencies

Runtime:

- `aiogram` — asynchronous Telegram handlers, FSM, keyboards, callbacks;
- `pandas` — CSV loading and analytics;
- `pydantic` — domain and configuration validation;
- `pydantic-settings` — `.env` settings;
- `PyYAML` — safe YAML loading;
- `plotly` — interactive charts;
- `kaleido` — static Plotly image export.

Development:

- `pytest` and `pytest-asyncio` — tests;
- `ruff` — formatting and linting;
- `pyright` — static type checking.

The project uses Python 3.12, `uv` for dependency management, and `just` as the
developer command interface.

## CLI and Just Recipes

Application CLI:

```text
python main.py bot
python main.py check-config
python main.py check-storage
python main.py parse "<message>"
python main.py analytics
python main.py export
```

Just recipes:

```text
setup, run, parse, test, lint, format, typecheck, check,
check-config, check-storage, analytics, export, tmux-setup
```

`justfile` only delegates to application and quality-tool commands; it does
not contain business logic.

## Error Handling

- Invalid configuration stops startup with a readable error.
- Telegram API failures are logged without secrets.
- Malformed CSV is reported by `check-storage`; the bot does not overwrite it.
- A failed chart returns a text summary instead.
- FSM sessions expire and release locks.
- Invalid manual input preserves the active step and asks again.

## Testing

Tests cover bilingual parsing, default UAH, multiple amounts, missing amounts,
Pydantic validation, duplicate messages, atomic concurrent writes, edit locks,
permissions, notes, period filters, aggregation, chart generation, callbacks,
FSM cancellation, and damaged storage.

Telegram calls, time, and filesystem paths are isolated in tests. No real bot
token or external network request is required.

## Delivery

All work happens in feature branches. Direct pushes to `main` or `master` are
forbidden. Each pull request must pass `just check` and receive a diff
self-review before it is opened.

The initial implementation may keep application logic in `main.py` as
requested. Tests, configuration, documentation, and generated data remain in
their appropriate files. Code is split later only when the single file becomes
an actual maintenance problem.
