# Income Notes Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Build an interactive Telegram bot that locally parses every group
message as income, lets members edit records and attach notes, stores data in
CSV, and returns Pandas/Plotly analytics.

**Architecture:** Keep the MVP application in `main.py`, with pure parsing,
validation, storage, analytics, and keyboard-building functions separated by
clear sections. Telegram handlers orchestrate these functions asynchronously;
blocking Pandas and Plotly operations run with `asyncio.to_thread`.

**Tech Stack:** Python 3.12, uv, aiogram 3, Pandas, Pydantic,
pydantic-settings, PyYAML, Plotly, Kaleido, pytest, pytest-asyncio, Ruff,
Pyright.

## Global Constraints

- Do not use paid AI requests, local language models, or OpenAI API calls.
- Parse Ukrainian and English text deterministically.
- Default missing currency to UAH.
- Store money as `Decimal`.
- Allow every group member to edit records during the MVP.
- Keep application logic in `main.py`.
- Never push directly to `main` or `master`.

---

### Task 1: Project Tooling and Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `config.yaml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `justfile`
- Create: `AGENTS.md`

**Interfaces:**
- Produces: `uv sync`, `just run`, `just check`, and validated YAML/env inputs.

- [ ] **Step 1: Define runtime and development dependencies**

Add Python `>=3.12,<3.14`, the runtime dependencies from the design, and
development groups for pytest, Ruff, and Pyright to `pyproject.toml`.

- [ ] **Step 2: Define safe defaults**

Add allowed chats, administrators, categories, CSV paths, timezone, edit-lock
duration, and analytics defaults to `config.yaml`; keep tokens only in
`.env.example`.

- [ ] **Step 3: Define developer commands**

Make `just` delegate to `uv run python main.py <command>` and quality tools.

- [ ] **Step 4: Verify metadata**

Run:

```bash
uv lock
uv sync
just --list
```

Expected: lock and environment creation succeed; all recipes are listed.

### Task 2: Models, Configuration, and Local Parser

**Files:**
- Modify: `main.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Produces: `Settings`, `AppConfig`, `IncomeRecord`, `RecordNote`,
  `ParsedIncome`, `load_config()`, and `parse_income_message()`.

- [ ] **Step 1: Write parser tests**

Cover Ukrainian/English currencies, default UAH, decimal/thousands separators,
multiple amounts, missing amounts, descriptions, and category suggestions.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
uv run pytest tests/test_main.py -v
```

Expected: collection or import failure because the new interfaces do not exist.

- [ ] **Step 3: Implement Pydantic models and parser**

Use compiled regular expressions, normalized currency aliases, keyword category
maps, and `Decimal` conversion. Return one parsed item per detected amount and
one empty-amount draft when none is detected.

- [ ] **Step 4: Verify parser tests pass**

Run the same pytest command and expect all parser/config tests to pass.

### Task 3: Atomic CSV Storage and Notes

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Produces: `CsvStorage.create_record()`, `update_record()`, `delete_record()`,
  `list_records()`, `add_note()`, `update_note()`, and `list_notes()`.

- [ ] **Step 1: Write isolated storage tests**

Use pytest `tmp_path` for record creation, duplicate protection, updates,
deletion, linked notes, and damaged CSV detection.

- [ ] **Step 2: Implement storage**

Validate rows through Pydantic, serialize Decimal/datetime values explicitly,
write via a sibling temporary file and `Path.replace`, and expose asynchronous
wrappers guarded by `asyncio.Lock`.

- [ ] **Step 3: Run storage tests**

Expected: storage tests pass without writing to the real `data/` directory.

### Task 4: Interactive Telegram Workflow

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Produces: income cards, typed callback data, reply menu, record pagination,
  FSM edit states, two-minute edit locks, deletion confirmation, and note flow.

- [ ] **Step 1: Test pure keyboard/card builders and permissions**

Assert callback payloads, visible fields, owner/admin deletion rules, and
expired edit-lock behavior.

- [ ] **Step 2: Implement routers and handlers**

Handle regular messages, `/start`, `/help`, `/menu`, `/records`, `/cancel`,
callbacks for editing/saving/deleting, and note creation. Ignore bot/service
messages and chats outside the allowlist.

- [ ] **Step 3: Run interaction unit tests**

Expected: pure interaction logic passes without making Telegram API requests.

### Task 5: Analytics, Charts, and Export

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Produces: `income_summary()`, `build_chart()`, `build_export_zip()`, analytics
  menu callbacks, and `/stats`, `/chart`, `/export`.

- [ ] **Step 1: Write analytics tests**

Cover today/week/month/all-time filtering, totals, averages, categories, and
ZIP membership.

- [ ] **Step 2: Implement Pandas analytics**

Parse timestamps as UTC, convert to configured timezone for periods, group by
category/member/day/month, and format Ukrainian summaries.

- [ ] **Step 3: Implement Plotly and ZIP output**

Generate interactive HTML and optional PNG; include both CSV files and HTML in
the administrator-only ZIP export.

- [ ] **Step 4: Run analytics tests**

Expected: deterministic summaries and export contents pass.

### Task 6: CLI, Verification, and Documentation

**Files:**
- Modify: `main.py`
- Create: `README.md`

**Interfaces:**
- Produces: CLI commands `bot`, `check-config`, `check-storage`, `parse`,
  `analytics`, and `export`.

- [ ] **Step 1: Implement argparse CLI**

Importing `main.py` must not require a Telegram token; only `bot` validates it.

- [ ] **Step 2: Document setup and BotFather privacy mode**

Document `uv`, `.env`, allowed chat IDs, bot launch, commands, data files, and
the need to disable privacy mode in a dedicated group.

- [ ] **Step 3: Run all checks**

Run:

```bash
just check
just check-config
just parse "Отримав 1 500 грн за консультацію"
git diff --check
```

Expected: formatting, lint, types, tests, configuration, parser smoke test, and
whitespace checks succeed.

- [ ] **Step 4: Review handoff**

Report changed files, checks, limitations, and exact Git commands the user can
run to commit and open a pull request.
