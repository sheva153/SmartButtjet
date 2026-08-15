# Safe Income Routing and Bot Resilience — Design

## Goal

Make income capture deliberate without losing the convenience of automatic
parsing. Messages that clearly describe income are saved automatically.
Messages containing an amount but no income signal require confirmation.
Ordinary text, dates, and clock times must not create records accidentally.

The same change set fixes broken developer recipes, makes deletion and editing
safe when a record disappears concurrently, prevents menu buttons from being
used as edited values, and adds privacy-conscious operational logging.

## Scope

This design covers:

- income-intent detection and confirmation of ambiguous numeric messages;
- exclusion of dates and valid clock times from monetary amounts;
- safe interaction between reply-menu actions and FSM input;
- idempotent deletion and missing-record handling;
- direct `uv` use in every `justfile` recipe;
- Loguru output to the console and a rotating file;
- focused automated tests for the changed behavior.

It does not change the CSV schema, analytics, permissions, supported
currencies, or category detection. It introduces no paid API or AI calls.

## Message Classification

Intent detection is a separate step from monetary parsing. This keeps two
questions independent:

1. Does the message appear to describe income?
2. Which amount, currency, category, description, and date does it contain?

`has_income_intent(text)` recognizes common Ukrainian word forms and basic
English equivalents. The initial vocabulary includes:

- Ukrainian forms of `отримати`, `заробити`, and `продати`;
- `дохід`, `оплата`, and their common inflected forms;
- English `earned`, `received`, `sold`, `income`, and `payment`.

The vocabulary is deterministic and local. Adding a synonym is a small,
testable change and does not affect the amount parser.

After intent and amount parsing, the router applies this decision table:

| Income signal | Parsed amount | Result |
| --- | --- | --- |
| yes | yes | Save automatically |
| yes | no | Ignore silently |
| no | yes | Ask for inline confirmation |
| no | no | Ignore silently |

An ambiguous candidate is held in the user's FSM context and is not written to
CSV. The bot asks `Записати як дохід?` with `Так` and `Ні` inline buttons.
`Так` saves the parsed record or records; `Ні` clears the candidate without a
write. The callback data identifies the pending action but does not embed the
message text or parsed payload.

Only one pending candidate per user and chat is needed. A new candidate
replaces the previous unconfirmed candidate. `/cancel` and menu navigation
clear it.

Examples:

- `Отримав 1500 грн` is saved automatically.
- `Продав велосипед за 5000` is saved automatically.
- `1500 грн за дизайн` asks for confirmation.
- `15` asks for confirmation.
- `отримав оплату` is ignored silently because it has no amount.
- text with neither an income signal nor an amount is ignored silently.

## Amount, Date, and Time Parsing

The parser identifies protected spans before looking for monetary amounts.
Existing date spans remain protected. A new clock-time pattern protects only a
valid `HH:MM` value in the inclusive range `00:00` through `23:59`.

Money matching skips every candidate that overlaps a protected date or time
span. A standalone integer is not treated as a clock time.

Examples:

- `зустріч о 15:00` contains no monetary amount and is ignored;
- `зустріч о 15` contains the candidate amount `15` and asks for confirmation;
- `о 9:30 отримав 1500 грн` ignores `9:30` and saves `1500 грн`;
- `отримав 15:00 грн` finds no amount and is ignored silently;
- invalid clock-like text such as `25:70` is not protected as a valid time.

## Confirmation State

The FSM gains a dedicated pending-confirmation state rather than reusing an
edit state. The stored data is the validated parsed candidate plus the source
message identifiers needed to construct records. Record creation still uses
the existing duplicate guard, so repeated callback delivery cannot create a
second record.

Confirmation callbacks handle expired, cancelled, and already-consumed state
with a short alert instead of raising an exception. They always acknowledge
the Telegram callback.

## Editing and Menu Navigation

Reply-menu labels are navigation actions, never field values. Menu handlers
take precedence over handlers waiting for an edited value, missing value,
note, or candidate confirmation.

When a user selects Records, Analytics, Chart, or Help during an FSM
interaction, the bot:

1. releases an owned record edit lock, if present;
2. clears the FSM state;
3. performs the selected navigation action.

Invalid genuine edit input keeps the edit state and lock active so the user
can retry. A lock is released only after a successful edit, explicit
cancellation, menu navigation, a missing-record result, or lock expiry.

Before an update or note write, the handler tolerates the target having been
deleted. It clears state and lock and replies that the record no longer
exists. The missing record is an expected interaction outcome, not a bot
crash.

## Idempotent Deletion

Storage deletion becomes idempotent. Its result reports whether a record was
actually removed:

- `True` means the record and its notes were deleted;
- `False` means the record was already absent.

A confirmation callback maps these results to `Запис видалено` and
`Запис уже видалено`. Concurrent confirmations therefore have a stable result
and never expose `KeyError` to polling.

Storage update and note operations may still signal that a required record is
missing, because silently accepting those writes would be misleading.
Telegram handlers translate that outcome into a user-facing message and
cleanup rather than allowing it to escape.

## Developer Commands

Every `justfile` recipe invokes `uv` directly:

```text
uv sync
uv run python main.py bot
uv run pytest
uv run ruff check .
uv run pyright
```

The same rule applies to all other recipes. `UV_CACHE_DIR := ".uv-cache"`
remains local to the repository. Recipes continue to be thin command aliases
and contain no application logic.

Recipe verification first checks that each recipe resolves and starts with the
expected arguments. Final handoff requires the full `just check` command to
pass.

## Logging

Loguru is added as a runtime dependency through `uv`. Logging is configured
once during application startup with two sinks:

- console at `INFO` by default, suitable for the existing `tmux` workflow;
- `logs/bot.log` at `DEBUG`, rotated at 10 MB, retained for 14 days, with
  rotated files compressed.

`LOG_LEVEL` in `.env` may override the console threshold. An invalid value
fails startup with a readable configuration error. `logs/` is ignored by Git.
Repeated initialization in tests or application setup removes application
sinks before adding them, preventing duplicate lines.

Structured context includes applicable `chat_id`, `user_id`, and `record_id`.
Logs cover:

- application startup and shutdown;
- automatic saves and ambiguous-candidate decisions;
- record creation, editing, deletion, and already-deleted outcomes;
- storage failures and uncaught polling errors.

Full ordinary-message text, note contents, `.env` values, and Telegram tokens
are never logged. Expected races such as an already-deleted record use
`WARNING`; operational failures use `ERROR` or `EXCEPTION`.

## Error Handling

Expected user and concurrency outcomes are handled at the Telegram boundary
with concise Ukrainian messages. FSM state and edit locks are cleaned up on
success, cancellation, navigation, and missing-record outcomes.

Validation errors preserve the relevant state so the user can retry.
Unexpected storage or Telegram failures are logged with a stack trace and
follow the bot's existing error propagation policy; secrets and private text
are excluded from log context.

## Testing

Unit and handler-level tests cover:

- each supported income-intent family and representative inflections;
- automatic save when both intent and amount exist;
- silent ignore when intent exists without an amount;
- confirmation and rejection when an amount exists without intent;
- silent ignore when neither exists;
- `15:00` excluded as time while standalone `15` remains an amount candidate;
- a real amount alongside a date and clock time;
- multiple monetary amounts after protected spans are removed;
- every reply-menu action during FSM input;
- invalid edit input retaining its state and lock;
- edit or note submission after concurrent deletion;
- first and repeated deletion results;
- logging initialization without duplicate sinks and without private content;
- direct `uv` invocation by all `justfile` recipes.

Tests use temporary storage and do not require a Telegram token or network
access. The complete `just check` suite must pass before implementation
handoff.

## Delivery

Implementation remains on a feature branch and is delivered through a pull
request. The dependency lockfile is updated only through `uv`. `.env`, bot
tokens, generated logs, and data files are never committed.

