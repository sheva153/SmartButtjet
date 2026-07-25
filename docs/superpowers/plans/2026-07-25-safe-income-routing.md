# Safe Income Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent accidental income records and interaction crashes while making developer commands and operational logging reliable.

**Architecture:** Keep the existing single-module structure, but separate income-intent classification from monetary parsing. Add a dedicated confirmation FSM state, translate expected storage races at handler boundaries, and configure Loguru once at startup.

**Tech Stack:** Python 3.12, aiogram 3, Pydantic, Pandas, Loguru, pytest, uv, just.

## Global Constraints

- Use only deterministic local parsing; add no paid AI or API calls.
- A valid clock time is only `00:00` through `23:59`; standalone `15` remains an amount candidate.
- Intent without an amount is ignored silently.
- Logs must never contain Telegram tokens, full message text, or note contents.
- Work only on `feature/safe-income-routing`; never push directly to `main` or `master`.
- Use `uv` for dependencies and `just` for developer commands.
- Run `just check` before handoff.

---

### Task 1: Developer Commands and Logging

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `justfile`
- Modify: `.gitignore`
- Modify: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `LogConfig`, `configure_logging(config: LogConfig) -> None`
- Produces: every recipe invoking `uv` directly

- [ ] **Step 1: Add failing logging and recipe tests**

Add a test that configures Loguru twice against a temporary log path, emits
one marker, and asserts the marker occurs once. Add a static test that reads
`justfile`, rejects `python -m uv`, and checks representative direct commands.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_main.py -k "logging or justfile" -v`

Expected: FAIL because logging configuration does not exist and recipes still
contain `python -m uv`.

- [ ] **Step 3: Add Loguru and implement configuration**

Run: `uv add "loguru>=0.7,<1"`.

Add Pydantic settings for console level, file path `logs/bot.log`, 10 MB
rotation, 14-day retention, and compression. Implement idempotent sink setup:

```python
def configure_logging(config: LogConfig) -> None:
    logger.remove()
    logger.add(sys.stderr, level=config.level)
    config.file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        config.file,
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
    )
```

Call it before bot startup and bind identifiers at event sites rather than
including message or note bodies.

- [ ] **Step 4: Fix recipes and ignore generated logs**

Replace `python -m uv sync` with `uv sync` and each
`python -m uv run ...` with `uv run ...`. Add `logs/` to `.gitignore`.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/test_main.py -k "logging or justfile" -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock justfile .gitignore main.py tests/test_main.py
git commit -m "chore: fix commands and add bot logging"
```

### Task 2: Intent Classification and Protected Times

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `has_income_intent(text: str) -> bool`
- Produces: `parse_income_message(...)` that excludes valid time spans
- Consumes: existing date-span exclusion and money parser

- [ ] **Step 1: Write failing parser tests**

Cover Ukrainian and English intent forms, `15:00` yielding no amount,
standalone `15` yielding `Decimal("15.00")`, and
`о 9:30 отримав 1500 грн 10.07` yielding only `1500`.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/test_main.py -k "intent or clock_time" -v`

Expected: FAIL because intent detection and clock protection are absent.

- [ ] **Step 3: Implement intent detection**

Use bounded, case-insensitive deterministic patterns for the approved
Ukrainian verb families and English words:

```python
INCOME_INTENT_PATTERN = re.compile(
    r"\b(?:отрим\w*|зароб\w*|прода(?:в|ла|ли)|дохід|доход\w*|"
    r"оплат\w*|earned|received|sold|income|payment)\b",
    re.IGNORECASE,
)


def has_income_intent(text: str) -> bool:
    return INCOME_INTENT_PATTERN.search(text) is not None
```

- [ ] **Step 4: Protect valid clock spans**

Add a compiled `HH:MM` pattern with constrained hours and minutes. Merge its
spans with date spans and reject every money match that overlaps either set.
Do not protect standalone integers.

- [ ] **Step 5: Run parser tests**

Run: `uv run pytest tests/test_main.py -k "parser or intent or clock_time" -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: classify income intent and ignore clock times"
```

### Task 3: Ambiguous Candidate Confirmation

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `EditState.waiting_income_confirmation`
- Produces: packed confirmation callbacks with `confirm_income` and
  `reject_income` actions
- Consumes: `has_income_intent()` and validated `ParsedIncome` values

- [ ] **Step 1: Write failing handler tests**

Use mocked `Message`, `FSMContext`, and storage collaborators to prove:
intent plus amount saves immediately; intent without amount is silent; amount
without intent stores no record and asks for confirmation; neither is silent;
accept saves once; reject clears state.

- [ ] **Step 2: Verify handler tests fail**

Run: `uv run pytest tests/test_main.py -k "income_routing or confirmation" -v`

Expected: FAIL because every parsed number currently saves immediately.

- [ ] **Step 3: Implement routing and confirmation**

Parse once, filter items with amounts, and apply the decision table. Store only
validated serializable candidate fields and source identifiers in FSM data.
Inline callbacks reconstruct `IncomeRecord` objects, rely on the existing
duplicate guard, acknowledge the callback, and clear state on accept or reject.

- [ ] **Step 4: Add privacy-safe decision logs**

Log `chat_id`, `user_id`, result, and number of candidates. Do not log
`message.text`, descriptions, notes, or callback payload data containing user
content.

- [ ] **Step 5: Run focused and parser tests**

Run: `uv run pytest tests/test_main.py -k "income_routing or confirmation or parser" -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: confirm ambiguous income messages"
```

### Task 4: FSM Navigation and Record Races

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Changes: `CsvStorage.delete_record_sync(record_id: str) -> bool`
- Changes: `CsvStorage.delete_record(record_id: str) -> bool`
- Produces: shared FSM cleanup that releases an owned edit lock

- [ ] **Step 1: Write failing race and navigation tests**

Test first deletion returning `True`, repeated deletion returning `False`,
confirmation of an absent record producing an already-deleted response,
missing-record edit/note cleanup, menu labels never reaching edited fields,
and invalid edit values preserving state and lock.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/test_main.py -k "delete or menu_during or edit_lock" -v`

Expected: FAIL due to `KeyError`, handler ordering, and unconditional lock
release.

- [ ] **Step 3: Make deletion idempotent**

Return `False` when no row exists and `True` after atomically writing filtered
records and notes. Update the async wrapper and callback messages accordingly.
Log repeated deletion as `WARNING`.

- [ ] **Step 4: Centralize FSM cleanup and prioritize navigation**

Create a helper that reads state, releases `record_id` for the current user,
and clears state. Call it from `/cancel` and menu handlers before navigation.
Register or filter handlers so menu labels cannot match the edit-value handler.

- [ ] **Step 5: Correct edit error behavior**

Release the edit lock only on successful writes or terminal cleanup. Catch a
missing target around update and note writes, clean up, and send
`Запис більше не існує.` Validation errors keep state and lock for retry.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_main.py -k "delete or menu_during or edit_lock" -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "fix: harden editing and record deletion"
```

### Task 5: Full Verification and Handoff

**Files:**
- Modify if needed: `README.md`
- Review: all changed files

**Interfaces:**
- Consumes: all preceding tasks
- Produces: a checked feature branch ready for a pull request

- [ ] **Step 1: Update user-facing documentation**

Replace the statement that every ordinary message is income with the approved
intent/confirmation decision table. Document Loguru output and the direct
`uv`/`just` workflow without exposing `.env` values.

- [ ] **Step 2: Run all recipes required for handoff**

Run: `just check`

Expected: ruff formatting check, lint, pyright, and all pytest tests PASS.

- [ ] **Step 3: Review the complete diff**

Run: `git diff origin/main...HEAD --check` and
`git diff origin/main...HEAD --stat`.

Expected: no whitespace errors; only scoped source, tests, dependency,
command, ignore, and documentation changes.

- [ ] **Step 4: Commit final documentation or cleanup**

```bash
git add README.md main.py tests/test_main.py
git commit -m "docs: explain safe income capture"
```

- [ ] **Step 5: Prepare pull-request handoff**

Report the feature branch, commits, `just check` result, behavioral summary,
and any command the maintainer should use to open the pull request. Do not push
or open a pull request without explicit authorization for the external action.

