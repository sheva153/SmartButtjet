set dotenv-load
export UV_CACHE_DIR := ".uv-cache"

setup:
    uv sync --locked

run:
    uv run python main.py bot

parse message:
    uv run python main.py parse "{{message}}"

test pattern="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "{{pattern}}" ]]; then
        uv run pytest -k "{{pattern}}"
    else
        uv run pytest
    fi

cov:
    uv run pytest --cov=income_stats --cov-report=term-missing --cov-report=html

smoke:
    uv run pytest tests/smoke -v

lint:
    uv run ruff check .

format:
    uv run ruff format .

typecheck:
    uv run pyright

check:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright
    uv run pytest

check-config:
    uv run python main.py check-config

check-storage:
    uv run python main.py check-storage

analytics chat_id period="month":
    uv run python main.py analytics --chat-id "{{chat_id}}" --period "{{period}}"

export chat_id:
    uv run python main.py export --chat-id "{{chat_id}}"

chats:
    #!/usr/bin/env bash
    set -euo pipefail
    cmd=(uv run python main.py chats)
    echo "→ ${cmd[*]}" >&2
    rc=0
    "${cmd[@]}" || rc=$?
    if [[ $rc -eq 0 ]]; then echo "  OK" >&2; else echo "  FAIL ($rc)" >&2; fi
    exit $rc

# Pick a chat (gum -> fzf -> all-records fallback) then run a chat-scoped
# `main.py <action>` with the loud OK/FAIL wrapper. Private helper shared by
# `records` and `retag` so the picker lives in one place.
_scoped action header chat_id="":
    #!/usr/bin/env bash
    set -euo pipefail
    chat_id="{{chat_id}}"
    if [[ -z "$chat_id" && -t 1 ]]; then
        if command -v gum >/dev/null 2>&1; then
            picked="$(uv run python main.py chats | gum filter --placeholder="{{header}}" || true)"
        elif command -v fzf >/dev/null 2>&1; then
            picked="$(uv run python main.py chats | fzf --prompt='chat_id> ' --header="{{header}}" || true)"
        else
            picked=""
        fi
        if [[ -n "${picked:-}" ]]; then
            chat_id="$(awk '{print $1}' <<< "$picked")"
        fi
    fi
    if [[ -n "$chat_id" ]]; then
        cmd=(uv run python main.py "{{action}}" --chat-id "$chat_id")
    else
        cmd=(uv run python main.py "{{action}}")
    fi
    echo "→ ${cmd[*]}" >&2
    rc=0
    "${cmd[@]}" || rc=$?
    if [[ $rc -eq 0 ]]; then echo "  OK" >&2; else echo "  FAIL ($rc)" >&2; fi
    exit $rc

# Review all records (optionally one chat; interactive picker when no chat_id).
records chat_id="":
    echo "━━━ just ━━━  just _scoped records 'select a chat to review' {{chat_id}}" >&2
    just _scoped records 'select a chat to review' "{{chat_id}}"

# Backfill tags across records (optionally one chat; interactive picker).
retag chat_id="":
    echo "━━━ just ━━━  just _scoped retag 'select a chat to retag' {{chat_id}}" >&2
    just _scoped retag 'select a chat to retag' "{{chat_id}}"

tmux-setup:
    tmux source-file -n "$HOME/.tmux.conf"
    tmux source-file "$HOME/.tmux.conf"
    @echo "tmux configuration reloaded"
