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

tmux-setup:
    tmux source-file -n "$HOME/.tmux.conf"
    tmux source-file "$HOME/.tmux.conf"
    @echo "tmux configuration reloaded"
